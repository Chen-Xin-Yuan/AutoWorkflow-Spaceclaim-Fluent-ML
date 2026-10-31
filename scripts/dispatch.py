# -*- coding: utf-8 -*-
"""dispatcher：领一条 `queued` 任务，建运行目录，拉起本阶段脚本，按退出情况推进状态。

它**只负责启动与状态推进**，不做审核：脚本成功就停在 `reviewing`，等审核（`scripts/review.py`
＋审核子智能体）用 `db.py record-review` 决定是否入库。任何失败都是 `rejected` 且
`attempts+1`，超出预算由 `record_review` 写上报单。

每阶段并发 1：一条一条领、一条一条跑。启停看 `control` 表（`stage` > `workflow`）。

-------------------------------------------------------------------------------
输入 / 输出 / 参数配置
-------------------------------------------------------------------------------
输入
    数据库       命令行 `--database` > 环境变量 `MISSILE_DB` > `workspace.json`（与 db.py 共用）。
    任务          `geometry_state`（建模/网格）与 `cfd_task` / `datcom_task`（求解/低保真）。
    本阶段命令    见 STAGE_COMMAND 配置：命令行、工作目录、日志名。
输出
    运行目录      `data_lib/runs/high/<stage>/<run_id>/`（脚本自己创建并写 run.json）
    日志          `data_lib/runs/high/<stage>/<run_id>/<geometry_id>_<stage>.log`
    库            `task_process`（pid）、任务状态、`attempts`
参数配置
    STAGE_COMMAND           各阶段的命令行模板
    CLAIM_LIMIT             每轮最多领几条（并发 1 时保持 1）
    LOOP_INTERVAL_SECONDS   循环模式空闲时的等待秒数
覆盖优先级
    命令行 > `workspace.json`。时间戳一律 UTC（由 db.py 统一生成）。
-------------------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# 脚本根路径必须先定义：下面的 import 依赖它。
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import control  # noqa: E402  （同目录，提供整棵进程树终止）
import db  # noqa: E402  （同目录的 scripts/db.py，唯一的写库入口）

# =============================================================================
# 1. 参数配置区
# =============================================================================
CLAIM_LIMIT = 1
LOOP_INTERVAL_SECONDS = 30
DISPATCH_LOG_DIR_REL = "data_lib/runtime/logs"
BATCH_INPUT_DIR_REL = "data_lib/runtime/batch_inputs"
TIMEOUT_SECONDS = {
    "spaceclaim": 1800,          # SpaceClaim 冷启动 + 建模 + 验收
    "fluent_meshing": 3600,
    "fluent_solution": 7200,
    "datcom": 1800,
    "point_cloud": 1800,         # 建模通过后生成点云（模型审核的否决项之一）
}
LOG_NAME_TEMPLATE = "{geometry_id}_{stage}.log"
KILL_GRACE_SECONDS = 30        # 超时杀树后等子进程回收的上限
RUNS_HIGH_REL = "data_lib/runs/high"
# 喂给 Fluent 的模型副本目录：原始 .scdoc 保持只读，避免下游重存破坏 sha256 证据链
MODEL_STAGE_DIR_REL = "data_lib/runtime/model_stage"
# 副本必须叫 `missile.scdoc`，目录按 run_id 分开。Fluent 用**文件名主干**加部件名
# 生成 face zone label（实测 `<主干>-boi`、`<主干>-missile`），而 `fluent_meshing` 的
# REQUIRED_FACE_LABELS 硬性要求 `missile-boi`。研究产物名是
# `<geometry_id>_spaceclaim.scdoc`（审核要求文件名以 geometry_id 开头），
# 直接喂过去标签会变成 `<geometry_id>_spaceclaim-boi`，sizing 直接报 Unknown exception。
MODEL_STAGE_FILENAME = "missile.scdoc"
# 点云生成脚本：模型审核要求点云存在且与库内参数一致，所以由派发器负责调用
POINT_CLOUD_SCRIPT_REL = "DATCOM/scripts/04_pointnet_demo.py"
STAGE_WORKFLOW = {
    "spaceclaim": "high",
    "fluent_meshing": "high",
    "fluent_solution": "high",
    "datcom": "low",
}
# 本阶段还没实现时，dispatcher 明确拒绝启动，而不是静默跳过或假装成功。
UNIMPLEMENTED_STAGES = {
    "fluent_solution": "求解脚本未实现（计划 P5 只建骨架）：没有可启动的求解命令行。",
}


# =============================================================================
# 2. 阶段命令
# =============================================================================
def spaceclaim_command(conn, geometry_id: str, run_id: str) -> list[str]:
    """按库里的 16 参数拼出建模命令行。参数只从库里来，不读任何配置文件。"""
    geometry = conn.execute("SELECT * FROM geometry WHERE geometry_id=?", (geometry_id,)).fetchone()
    if geometry is None:
        raise SystemExit(f"库里没有几何 {geometry_id}")
    out_dir = db.WORKSPACE_ROOT / RUNS_HIGH_REL / "spaceclaim"
    command = [
        "node",
        str(db.WORKSPACE_ROOT / "spaceclaim" / "scripts" / "missile.mjs"),
        "--out", str(out_dir),
        "--run-id", run_id,
        "--name", f"{geometry_id}_spaceclaim.scdoc",
    ]
    for key in db.GEOMETRY_LENGTH_KEYS:
        command += [f"--{key}", f"{float(geometry[key]):.6f}"]
    command += [
        "--fin_section", db.SECTION_TO_TOOL[geometry["fin_section_type"]],
        "--thickness_percent", f"{float(geometry['thickness_percent']):.6f}",
        "--nose_shape", geometry["nose_shape"],
    ]
    if geometry["fin_section_type"] == "hex":
        command += ["--flat_percent", f"{float(geometry['flat_percent']):.6f}"]
    else:
        command += [
            "--le_radius_index", str(int(geometry["le_radius_index"])),
            "--max_thickness_tenths", str(int(geometry["max_thickness_tenths"])),
        ]
    return command


def staged_model_path(run_id: str) -> Path:
    """喂给 Fluent 的模型副本路径（`model_stage/<run_id>/missile.scdoc`）。"""
    return db.WORKSPACE_ROOT / MODEL_STAGE_DIR_REL / run_id / MODEL_STAGE_FILENAME


def drop_staged_model(run_id: str) -> None:
    """删掉本次的模型副本。它是模型产物的逐字节拷贝，随时可由 `model_path` 重建，
    留着只是重复占用几个 GB。"""
    directory = db.WORKSPACE_ROOT / MODEL_STAGE_DIR_REL / run_id
    shutil.rmtree(directory, ignore_errors=True)


def fluent_meshing_command(conn, geometry_id: str, run_id: str) -> list[str]:
    """按库里的模型路径拼出网格命令行。模型必须已经通过审核（model_path 非空）。

    喂给 Fluent 的是一份**副本**，不是过审的产物本身：SpaceClaim 文档一旦被重新
    保存（界面打开后保存、或读取器写回加密片缓存），字节就变了，`run.json` 里
    记录的 sha256 随之失配，模型产物的证据链直接断掉。副本让原始产物保持只读。
    """
    row = conn.execute(
        "SELECT model_path FROM geometry_state WHERE geometry_id=?", (geometry_id,)).fetchone()
    if row is None or not row["model_path"]:
        raise SystemExit(f"{geometry_id} 没有通过审核的模型路径，先跑 spaceclaim 阶段")
    model = db.WORKSPACE_ROOT / row["model_path"]
    staged = staged_model_path(run_id)
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(model, staged)
    launcher = db.WORKSPACE_ROOT / "fluent_meshing" / "run_meshing.cmd"
    # 显式给 `--out`，让网格文件名以 geometry_id 开头：`db.record_review` 的入库闸门要求
    # 产物名以 geometry_id 开头，而 `fluent_meshing` 的默认名是 `missile_mesh.msh.h5`。
    # 跑出来落在本次运行目录里，`--out` 要求目标不存在，正好由新目录保证。
    mesh_out = db.WORKSPACE_ROOT / RUNS_HIGH_REL / "fluent_meshing" / run_id / f"{geometry_id}_mesh.msh.h5"
    # .cmd 必须经 cmd /c 启动；--run-id 让运行目录落在 runs/high/fluent_meshing/<run_id>/
    return ["cmd", "/c", str(launcher), "--geometry", str(staged), "--run-id", run_id,
            "--out", str(mesh_out)]


# =============================================================================
# 3. 领任务 / 跑一条 / 推进状态
# =============================================================================
def claim_one(conn, stage: str) -> dict | None:
    """从 `queued` 领一条并直接推到 `running`：`queued` 是 watcher 认领过的队列状态。"""
    if stage in db.STAGE_STATUS_FIELD:
        claimed = db.claim_geometry_stage(conn, stage, CLAIM_LIMIT,
                                          source_status="queued", target_status="running")
        if not claimed:
            return None
        return {"stage": stage, "geometry_id": claimed[0], "condition_id": None}
    if stage == "fluent_solution":
        claimed = db.claim_cfd_tasks(conn, CLAIM_LIMIT,
                                     source_status="queued", target_status="running")
    elif stage == "datcom":
        claimed = db.claim_datcom_tasks(conn, CLAIM_LIMIT,
                                        source_status="queued", target_status="running")
    else:
        raise SystemExit(f"未知阶段：{stage}")
    if not claimed:
        return None
    geometry_id, condition_id = claimed[0]
    return {"stage": stage, "geometry_id": geometry_id, "condition_id": condition_id}


def dispatch_once(conn, stage: str, database: str | None = None) -> str:
    """跑一条任务，返回一句人读的结果。没有可领的任务时返回空串。"""
    if stage == "datcom":
        return dispatch_datcom_batch(conn, database)
    task = claim_one(conn, stage)
    if task is None:
        return ""
    geometry_id = task["geometry_id"]
    condition_id = task["condition_id"]
    run = {"stage": stage, "geometry_id": geometry_id, "condition_id": condition_id,
           "table": "geometry_state" if stage in db.STAGE_STATUS_FIELD else
                    ("cfd_task" if stage == "fluent_solution" else "datcom_task"),
           "field": db.STAGE_STATUS_FIELD.get(stage, "status")}

    run_id = db.make_run_id(geometry_id, condition_id or db.SHARED_CONDITION_ID,
                            _taken_run_ids(conn))
    run_dir = db.WORKSPACE_ROOT / RUNS_HIGH_REL / stage / run_id
    log_rel = (Path(RUNS_HIGH_REL) / stage / run_id / LOG_NAME_TEMPLATE.format(
        geometry_id=geometry_id, stage=stage)).as_posix()

    if stage in UNIMPLEMENTED_STAGES:
        message = UNIMPLEMENTED_STAGES[stage]
        db.mark_task(conn, run, "rejected", bump_attempts=True, run_id=run_id)
        db.record_report(conn, stage=stage, geometry_id=geometry_id, run_id=run_id,
                         attempts=1, condition_id=condition_id,
                         last_error=f"dispatcher 拒绝启动：{message}")
        return f"{stage} {run_id} → rejected（{message}）"

    if stage == "spaceclaim":
        command = spaceclaim_command(conn, geometry_id, run_id)
    elif stage == "fluent_meshing":
        command = fluent_meshing_command(conn, geometry_id, run_id)
    else:
        raise SystemExit(f"未知阶段：{stage}")
    db.mark_task(conn, run, "running", run_id=run_id, result_dir=db.workspace_rel(run_dir),
                 log_path=log_rel)

    try:
        stdout, stderr, code, elapsed = _run_child(command, TIMEOUT_SECONDS[stage],
                                                   conn=conn, run_id=run_id)
    finally:
        if stage == "fluent_meshing":
            drop_staged_model(run_id)

    # 运行目录由脚本创建；失败时脚本会把整个目录移到 feedback/<stage>/<run_id>/，
    # 所以日志要写到"当前真实的那个目录"里，否则失败日志会凭空消失。
    manifest = {}
    passed = False
    actual_dir = run_dir
    if not actual_dir.is_dir():
        moved = db.WORKSPACE_ROOT / "feedback" / stage / run_id
        if moved.is_dir():
            actual_dir = moved
    if actual_dir.is_dir():
        (actual_dir / Path(log_rel).name).write_text(
            f"$ {' '.join(command)}\n\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n",
            encoding="utf-8",
        )
        manifest_path = actual_dir / "run.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                manifest = {}
    passed = code == 0 and manifest.get("status") == "PASSED"
    if passed:
        cloud_note = ""
        if stage == "spaceclaim":
            # 点云是模型审核的**否决项**，必须由流水线生成，不能指望人工补跑。
            cloud_note = _generate_point_cloud(conn, geometry_id, run_id)
        db.mark_task(conn, run, "reviewing")
        return f"{stage} {run_id} → reviewing（{elapsed:.1f}s{cloud_note}，等审核）"

    db.mark_task(conn, run, "rejected", bump_attempts=True)
    lines = (stderr or "").strip().splitlines() or (stdout or "").strip().splitlines()
    error = (lines[-1] if lines else "没有输出")[:400]
    db.record_report(conn, stage=stage, geometry_id=geometry_id, run_id=run_id,
                     attempts=max(1, _attempts(conn, run)), condition_id=condition_id,
                     last_error=f"退出码 {code}；{error}",
                     feedback_dir=_feedback_dir(conn, stage, run_id))
    return f"{stage} {run_id} → rejected（退出码 {code}，{elapsed:.1f}s）：{error}"


def _run_child(command: list[str], timeout: int, *, conn=None,
               run_id: str | None = None) -> tuple[str, str, int, float]:
    """跑一个子进程并返回 (stdout, stderr, 退出码, 耗时)。

    子进程输出统一按 UTF-8 解：Windows 默认按 GBK 解，中文日志会直接抛 UnicodeDecodeError；
    `PYTHONIOENCODING` 让 python 子进程也用 UTF-8 写，否则日志里全是乱码。

    给了 `conn` + `run_id` 时把 pid 登记进 `task_process`，退出（含超时）后注销：
    那是 `control.py stop` 和 `watch.py` 僵死回收唯一能找到进程的地方。
    超时用 `control.kill_tree` 杀**整棵**进程树——ANSYS 会再叉子进程，
    只终止父进程会留下孤儿继续占许可证。
    """
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    started = time.monotonic()
    try:
        child = subprocess.Popen(command, cwd=str(db.WORKSPACE_ROOT),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, encoding="utf-8", errors="replace",
                                 env=environment)
    except OSError as exc:
        return "", f"启动失败：{exc}", -1, time.monotonic() - started

    if conn is not None and run_id:
        db.register_task_process(conn, run_id, child.pid)
    try:
        stdout, stderr = child.communicate(timeout=timeout)
        code = child.returncode
    except subprocess.TimeoutExpired:
        control.kill_tree(child.pid)
        try:
            stdout, stderr = child.communicate(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        stderr = (stderr or "") + f"\n超时 {timeout} 秒，已终止整棵进程树"
        code = -1
    finally:
        if conn is not None and run_id:
            db.clear_task_process(conn, run_id)
    return stdout or "", stderr or "", code, time.monotonic() - started


def _write_dispatch_log(name: str, command: list[str], stdout: str, stderr: str) -> str:
    path = db.WORKSPACE_ROOT / DISPATCH_LOG_DIR_REL / f"{name}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"$ {' '.join(command)}\n\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n",
        encoding="utf-8",
    )
    return db.workspace_rel(path)


def dispatch_datcom_batch(conn, database: str | None = None) -> str:
    """低保真：一批（同一几何 + 同一高度）一次 DATCOM 调用，然后脚本自检 + 入库。

    这条链路**没有 LLM 审核子智能体**（计划 §6.2）：自检通过就直接写库，
    不通过照样进 `report`，让主智能体看得见。
    """
    batch = db.claim_datcom_batch(conn)
    if batch is None:
        return ""
    geometry_id = batch["geometry_id"]
    run_id = batch["run_id"]
    condition_ids = batch["condition_ids"]

    input_path = db.WORKSPACE_ROOT / BATCH_INPUT_DIR_REL / f"{run_id}.csv"
    db.export_batch_input(conn, geometry_id, condition_ids, input_path)
    command = [
        sys.executable, str(db.WORKSPACE_ROOT / "DATCOM" / "scripts" / "01_batch_datcom.py"),
        "--input", str(input_path),
        "--run-id", run_id,
    ]
    stdout, stderr, code, elapsed = _run_child(command, TIMEOUT_SECONDS["datcom"],
                                               conn=conn, run_id=run_id)
    log_rel = _write_dispatch_log(f"datcom_{run_id}", command, stdout, stderr)

    if code != 0:
        lines = (stderr or "").strip().splitlines() or (stdout or "").strip().splitlines()
        error = (lines[-1] if lines else "没有输出")[:400]
        for condition_id in condition_ids:
            db.mark_task(conn, {"stage": "datcom", "geometry_id": geometry_id,
                                "condition_id": condition_id, "table": "datcom_task",
                                "field": "status"}, "rejected", bump_attempts=True)
        db.record_report(conn, stage="datcom", geometry_id=geometry_id, run_id=run_id,
                         attempts=1, condition_id=None,
                         last_error=f"01_batch_datcom.py 退出码 {code}：{error}",
                         feedback_dir=log_rel)
        return (f"datcom {run_id} → rejected（退出码 {code}，{elapsed:.1f}s）：{error}")

    # 自检与入库交给 review.py：它按 §7.4 核四条判据，逐工况写 datcom_task
    review_command = [
        sys.executable, str(SCRIPT_DIR / "review.py"),
        "--kind", "datcom", "--run-id", run_id, "--record",
        "--reviewer", "script:review.py",
    ]
    if database:
        review_command += ["--database", database]
    review_out, review_err, review_code, _ = _run_child(review_command, 300)
    _write_dispatch_log(f"datcom_{run_id}_review", review_command, review_out, review_err)

    if review_code == 0:
        return (f"datcom {run_id} → passed（{len(condition_ids)} 个工况，{elapsed:.1f}s，"
                f"自检通过并已入库）")
    first = [line for line in (review_out or review_err).splitlines() if "否决" in line]
    detail = first[0].strip() if first else (review_err or review_out).strip().splitlines()[-1:]
    return f"datcom {run_id} → rejected（{elapsed:.1f}s）：{detail}"


def _generate_point_cloud(conn, geometry_id: str, run_id: str) -> str:
    """建模通过后生成点云（模型审核的否决项之一）。返回给结果行追加的说明。"""
    script = db.WORKSPACE_ROOT / POINT_CLOUD_SCRIPT_REL
    if not script.exists():
        return "；点云脚本缺失"
    command = [sys.executable, str(script), "--geometry-id", geometry_id]
    out, err, code, secs = _run_child(command, TIMEOUT_SECONDS["point_cloud"],
                                      conn=conn, run_id=run_id)
    _write_dispatch_log(f"spaceclaim_{run_id}_pointcloud", command, out, err)
    if code == 0:
        return f" + 点云 {secs:.1f}s"
    tail = (err or "").strip().splitlines() or (out or "").strip().splitlines()
    detail = (tail[-1] if tail else "没有输出")[:200]
    print(f"  警告：点云生成失败（退出码 {code}）：{detail}", file=sys.stderr)
    print("  模型审核会因此判否决；修好后可单独重跑 04_pointnet_demo.py。", file=sys.stderr)
    return f"；点云失败（退出码 {code}）"


def _taken_run_ids(conn) -> set[str]:
    return db.taken_run_ids(conn)


def _attempts(conn, run: dict) -> int:
    if run["table"] == "geometry_state":
        return 0
    row = conn.execute(
        f"SELECT attempts FROM {run['table']} WHERE geometry_id=? AND condition_id=?",
        (run["geometry_id"], run["condition_id"]),
    ).fetchone()
    return int(row["attempts"]) if row else 0


def _feedback_dir(conn, stage: str, run_id: str) -> str | None:
    """失败时 SpaceClaim 会把运行目录整体移到 feedback/<stage>/<run_id>/。"""
    candidate = db.WORKSPACE_ROOT / "feedback" / stage / run_id
    return db.workspace_rel(candidate) if candidate.exists() else None


# =============================================================================
# 4. 命令行
# =============================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dispatch.py",
        description="领取 queued 任务并拉起本阶段脚本；不做审核，成功即停在 reviewing。",
    )
    parser.add_argument("--stage", required=True, choices=list(db.STAGES))
    parser.add_argument("--database", help="库文件路径（默认取 workspace.json）")
    parser.add_argument("--once", action="store_true", help="最多跑一条就退出")
    parser.add_argument("--interval", type=float, default=LOOP_INTERVAL_SECONDS,
                        help=f"循环模式空闲等待秒数（默认 {LOOP_INTERVAL_SECONDS}）")
    parser.add_argument("--ignore-control", action="store_true",
                        help="忽略 control 表里的 paused/stopped（仅用于单次调试）")
    return parser


def main(argv: list[str] | None = None) -> int:
    db._configure_output()
    args = build_parser().parse_args(argv)
    db_path = db.resolve_database(args.database)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}\n先运行：python scripts/db.py init", file=sys.stderr)
        return 1

    while True:
        conn = db.connect(db_path)
        try:
            state, source = db.effective_control(
                conn, workflow=STAGE_WORKFLOW[args.stage], stage=args.stage)
            if state != "running" and not args.ignore_control:
                message = f"{args.stage} 被 {source} 置为 {state}，不领取新任务。"
            else:
                message = dispatch_once(conn, args.stage, args.database) or (
                    f"{args.stage} 没有 queued 任务。")
        finally:
            conn.close()
        print(f"[{db.utc_sql()}] {message}" if not args.once else message, flush=True)
        if args.once:
            return 0
        if "没有 queued 任务" in message or "不领取新任务" in message:
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
