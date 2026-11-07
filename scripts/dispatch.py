# -*- coding: utf-8 -*-
"""执行器：原子领一条待处理行 → 建运行目录 → 拉起本阶段脚本 → 成功回 `pending` 等审核。

它**只负责启动与状态推进**，不做审核。三个状态词的含义：

    pending    待处理（含"脚本跑完了、等审核"）
    repairing  审核不通过，修理中
    queued     审核通过，可被下一流程领取

所以 dispatch 干完活把行放回 `pending`（等审核），审核通过才由 `record_review` 推成
`queued`。审核不通过由审核侧置 `repairing`，这里不碰。

**"谁在跑"不靠状态词**：`db.claim_*` 用 `UPDATE ... WHERE claimed_at IS NULL` 的
rowcount 原子领取，同时写 `run_id`；进程启动后用 `register_task_process` 登记 pid，
结束时清 `claimed_at`。同一个任务被 watch 连续多轮发现也只有一个进程能领到它。

两种用法：

    python scripts/dispatch.py --stage spaceclaim --once      # 前台跑一条，跑完才退出
    python scripts/dispatch.py --stage spaceclaim --spawn     # 领一条、起子进程、立刻退出
    python scripts/dispatch.py --stage spaceclaim             # 循环模式

`watch.py` 用 `--spawn`：它只负责发现与建行，把"跑 30 分钟"这件事交给独立的子进程，
自己的循环永远不会被卡住。

-------------------------------------------------------------------------------
输入 / 输出 / 参数配置
-------------------------------------------------------------------------------
输入
    数据库       命令行 `--database` > 环境变量 `MISSILE_DB` > `workspace.json`（与 db.py 共用）。
    任务          `geometry_state`（建模）、`mesh_task`（网格，按工况）、
                  `cfd_task` / `datcom_task`（求解 / 低保真，按工况）。
    边界层参数    `fluent_meshing/boundary_layer.py` 按该行工况算出的设计值（网格阶段）。
    本阶段命令    见 `spaceclaim_command` / `fluent_meshing_command` 与 STAGE_COMMAND 说明。
输出
    运行目录      `data_lib/runs/high/<stage>/<run_id>/`（脚本自己创建并写 run.json）
    日志          `data_lib/runs/high/<stage>/<run_id>/<geometry_id>_<stage>.log`
    spawn 日志    `data_lib/runtime/logs/spawn_<stage>_<run_id>.log`（子进程的独立输出）
    库            `task_process`（pid）、任务状态、`attempts`、`claimed_at`
参数配置
    CLAIM_LIMIT             每轮最多领几条（并发 1 时保持 1）
    LOOP_INTERVAL_SECONDS   循环模式空闲时的等待秒数
    SPAWN_POLL_SECONDS      `--spawn` 时等子进程启动的秒数（只为确认它真的起来了）
    TIMEOUT_SECONDS         各阶段前台运行的超时
    UNIMPLEMENTED_STAGES    还没实现的阶段：明确拒绝启动并写上报单，不假装成功
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
SPAWN_POLL_SECONDS = 1.0      # 起子进程后等这么久确认它没立刻退出
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
# 网格阶段：边界层设计值的来源（唯一的实现，不在这里复制公式）
BOUNDARY_LAYER_DIR = "fluent_meshing"


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


def _boundary_layer_module():
    """取 `fluent_meshing/boundary_layer.py`：公式与参数配置都只在那一份里。"""
    directory = db.WORKSPACE_ROOT / BOUNDARY_LAYER_DIR
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    import boundary_layer  # noqa: PLC0415  （延迟导入：只有网格阶段需要）

    return boundary_layer


def fluent_meshing_command(conn, geometry_id: str, run_id: str,
                           condition_id: str) -> list[str]:
    """按库里的模型路径与该工况的边界层设计值拼出网格命令行。

    模型必须已经通过审核（`model_path` 非空）。喂给 Fluent 的是一份**副本**，不是
    过审的产物本身：SpaceClaim 文档一旦被重新保存（界面打开后保存、或读取器写回
    加密片缓存），字节就变了，`run.json` 里记录的 sha256 随之失配，证据链直接断掉。

    边界层首层高度与层数**按工况算**（`boundary_layer.py`）：同一几何不同马赫数的
    y+ 首层高度差一个量级，共用一份网格必有工况落在 y+ 目标之外。算出来的设计值
    同时写回 `mesh_task` 行（可追溯证据），并传给 Fluent。
    """
    state = conn.execute(
        "SELECT model_path FROM geometry_state WHERE geometry_id=?", (geometry_id,)).fetchone()
    if state is None or not state["model_path"]:
        raise SystemExit(f"{geometry_id} 没有通过审核的模型路径，先跑 spaceclaim 阶段")
    model = db.WORKSPACE_ROOT / state["model_path"]
    staged = staged_model_path(run_id)
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(model, staged)

    design = _boundary_layer_module().design_for_condition(conn, geometry_id, condition_id)
    db.set_mesh_design(conn, geometry_id, condition_id, design.as_dict())

    launcher = db.WORKSPACE_ROOT / "fluent_meshing" / "run_meshing.cmd"
    # 显式给 `--out`，让网格文件名以 geometry_id 开头：`db.record_review` 的入库闸门要求
    # 产物名以 geometry_id 开头，而 `fluent_meshing` 的默认名是 `missile_mesh.msh.h5`。
    mesh_out = (db.WORKSPACE_ROOT / RUNS_HIGH_REL / "fluent_meshing" / run_id
                / f"{geometry_id}_mesh.msh.h5")
    # .cmd 必须经 cmd /c 启动；--run-id 让运行目录落在 runs/high/fluent_meshing/<run_id>/
    return ["cmd", "/c", str(launcher),
            "--geometry", str(staged),
            "--run-id", run_id,
            "--out", str(mesh_out),
            "--condition-id", condition_id,
            "--first-height", f"{design.first_height_mm:.6g}",
            "--layers", str(int(design.n_layers)),
            "--growth-rate", f"{design.growth_rate:g}",
            "--y-plus-target", f"{design.y_plus_target:g}"]


# =============================================================================
# 3. 领任务 / 跑一条 / 推进状态
# =============================================================================
CLAIMERS = {
    "spaceclaim": db.claim_model,
    "fluent_meshing": db.claim_mesh,
    "fluent_solution": db.claim_solution,
    "datcom": db.claim_datcom_tasks,
}


def claim_one(conn, stage: str) -> dict | None:
    """原子领一条待处理行；领不到返回 None。

    返回的字典含 `run_id`（领取时就写进库里了），所以即使进程在登记 pid 之前崩掉，
    watch 的僵死回收也能靠这个 run_id 反查到是哪一行。
    """
    claimed = CLAIMERS[stage](conn, CLAIM_LIMIT)
    return claimed[0] if claimed else None


def dispatch_once(conn, stage: str, database: str | None = None) -> str:
    """跑一条任务，返回一句人读的结果。没有可领的任务时返回空串。"""
    if stage == "datcom":
        return dispatch_datcom_batch(conn, database)
    task = claim_one(conn, stage)
    if task is None:
        return ""
    geometry_id = task["geometry_id"]
    condition_id = task["condition_id"]
    run_id = task["run_id"]
    run = {"stage": stage, "geometry_id": geometry_id, "condition_id": condition_id,
           "table": task["table"], "field": task["field"]}
    target = geometry_id + (f" / {condition_id}" if condition_id else "")

    run_dir = db.WORKSPACE_ROOT / RUNS_HIGH_REL / stage / run_id
    log_rel = (Path(RUNS_HIGH_REL) / stage / run_id / LOG_NAME_TEMPLATE.format(
        geometry_id=geometry_id, stage=stage)).as_posix()

    if stage in UNIMPLEMENTED_STAGES:
        message = UNIMPLEMENTED_STAGES[stage]
        # 领了却不跑：放回 pending 并清掉领取标记，免得占住这一行
        db.mark_task(conn, run, "pending")
        db.record_report(conn, stage=stage, geometry_id=geometry_id, run_id=run_id,
                         attempts=1, condition_id=condition_id, dedupe=True,
                         last_error=f"dispatcher 拒绝启动：{message}")
        return f"{stage} {target} → 未启动（{message}）"

    if stage == "spaceclaim":
        command = spaceclaim_command(conn, geometry_id, run_id)
    elif stage == "fluent_meshing":
        command = fluent_meshing_command(conn, geometry_id, run_id, condition_id)
    else:
        raise SystemExit(f"未知阶段：{stage}")
    db.mark_task(conn, run, "pending", run_id=run_id,
                 result_dir=db.workspace_rel(run_dir), log_path=log_rel, keep_claim=True)

    try:
        stdout, stderr, code, elapsed = _run_child(command, TIMEOUT_SECONDS[stage],
                                                   conn=conn, run_id=run_id)
    finally:
        if stage == "fluent_meshing":
            drop_staged_model(run_id)

    # 运行目录由脚本创建；失败时脚本会把整个目录移到 feedback/<stage>/<run_id>/，
    # 所以日志要写到"当前真实的那个目录"里，否则失败日志会凭空消失。
    manifest = {}
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
    succeeded = code == 0 and manifest.get("status") == "PASSED"
    if succeeded:
        cloud_note = ""
        if stage == "spaceclaim":
            # 点云是模型审核的**否决项**，必须由流水线生成，不能指望人工补跑。
            cloud_note = _generate_point_cloud(conn, geometry_id, run_id)
        # 跑成功 → 回到 pending 等审核（审核通过才由 record_review 推成 queued）
        db.mark_task(conn, run, "pending", run_id=run_id)
        return f"{stage} {target} → pending（{elapsed:.1f}s{cloud_note}，等审核）"

    # 跑失败：也回 pending（本阶段这一轮结束），并写上报单让人看见。
    # 置 repairing 需要"审核不通过"这个判断，而脚本自己崩了还没进审核，
    # 所以这里只留痕，由人/子智能体决定是 requeue 还是修。
    db.mark_task(conn, run, "pending", bump_attempts=True, run_id=run_id)
    lines = (stderr or "").strip().splitlines() or (stdout or "").strip().splitlines()
    error = (lines[-1] if lines else "没有输出")[:400]
    db.record_report(conn, stage=stage, geometry_id=geometry_id, run_id=run_id,
                     attempts=max(1, _attempts(conn, run)), condition_id=condition_id,
                     dedupe=True, last_error=f"退出码 {code}；{error}",
                     feedback_dir=_feedback_dir(conn, stage, run_id))
    return f"{stage} {target} → 失败（退出码 {code}，{elapsed:.1f}s）：{error}"


def spawn_once(conn, stage: str, database: str | None = None) -> str:
    """领一条任务 → 起一个独立子进程去跑它 → 立刻返回。

    这是 `watch.py` 用的模式：watch 的循环只做"发现 + 建行 + 起进程"，绝不等
    30 分钟级的网格任务跑完。防重复领取全靠 db 的 `claimed_at` 原子写——被领走的行
    下一轮根本不会出现在候选里，所以同一个任务不会被起动第二个进程。

    子进程是 `dispatch.py --run-claimed ...`，它自己负责起脚本、推进状态与 pid 登记；
    spawn 只负责把它的输出重定向到独立日志，避免两边的 stdout 互相污染。

    低保真按批次领（同一几何 + 同一高度一次算完），所以 spawn 也要先把批次领下来，
    把 `run_id` 与工况列表交给子进程，而不是让子进程再领一次。
    """
    if stage == "datcom":
        batch = db.claim_datcom_batch(conn)
        if batch is None:
            return ""
        note = (f"{batch['geometry_id']} / h={batch['altitude_m']:g} m"
                f"（{len(batch['condition_ids'])} 个工况，run_id {batch['run_id']}）")
        return spawn_worker(conn, [stage, batch["geometry_id"], batch["run_id"]],
                            stage, database, note=note, batch=batch,
                            claimed={"stage": stage, "geometry_id": batch["geometry_id"],
                                     "condition_id": None, "run_id": batch["run_id"]})
    task = claim_one(conn, stage)
    if task is None:
        return ""
    target = task["geometry_id"] + (f" / {task['condition_id']}" if task["condition_id"] else "")
    # 领取标记已经写进库，子进程会重新读出来跑；这里不能提前清
    return spawn_worker(conn, [stage, task["geometry_id"], task["condition_id"] or ""],
                        stage, database, note=f"{target}（run_id {task['run_id']}）",
                        claimed=task)


def spawn_worker(conn, payload: list[str], stage: str, database: str | None,
                 note: str = "", claimed: dict | None = None,
                 batch: dict | None = None) -> str:
    """起一个后台子进程执行 `dispatch.py --run-claimed`，返回一句人读的话。

    子进程若在登记 pid 之前就退出（依赖缺失、模型路径不存在等），`claimed_at` 会一直
    挂着；这里发现"启动即失败"就当场释放这一行并置 `repairing`，不必等 watch 的
    孤儿超时（`db.CLAIM_ORPHAN_SECONDS`）。
    """
    log_dir = db.WORKSPACE_ROOT / DISPATCH_LOG_DIR_REL
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = db.utc_stamp()
    log_path = log_dir / f"spawn_{stage}_{stamp}.log"
    command = [sys.executable, str(Path(__file__).resolve()),
               "--run-claimed", stage, *payload[1:]]
    if batch is not None:
        # 批次已经领好了：把 run_id 与工况列表交给子进程，让它不要自己再领一次
        command += ["--claimed-batch", f"{batch['run_id']}:" + ",".join(batch["condition_ids"])]
    if database:
        command += ["--database", database]
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    with log_path.open("w", encoding="utf-8") as handle:
        child = subprocess.Popen(
            command, cwd=str(db.WORKSPACE_ROOT), stdout=handle,
            stderr=subprocess.STDOUT, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    # 只等一小会儿确认它没立刻退出；等满整个任务会把 watch 卡住
    time.sleep(SPAWN_POLL_SECONDS)
    code = child.poll()
    if code is not None and code != 0:
        detail = _log_tail(log_path)
        if claimed is not None:
            _release_failed_claim(conn, claimed, detail)
        return (f"{stage} {note} 子进程立刻退出（退出码 {code}）：{detail}"
                f"（详见 {db.workspace_rel(log_path)}）")
    return f"{stage} {note} 已交给子进程 pid {child.pid}（日志 {db.workspace_rel(log_path)}）"


def _log_tail(path: Path, lines: int = 2) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return "日志不可读"
    return (content[-1] if content else "没有输出")[:200]


def _release_failed_claim(conn, claimed: dict, detail: str) -> None:
    """把"领了但没跑起来"的行置 repairing，并写一条上报单。

    这正是"修理工单"该出现的位置：不是环境问题被判成模型问题，而是**根本没启动**，
    所以必须让人看得见；置 repairing 而不是 pending，避免坏环境每轮重试烧机器。
    """
    run = db.run_target(claimed["stage"], claimed["geometry_id"], claimed["condition_id"])
    db.mark_task(conn, run, "repairing", bump_attempts=True)
    db.record_report(
        conn, stage=claimed["stage"], geometry_id=claimed["geometry_id"],
        run_id=claimed["run_id"], attempts=1, condition_id=claimed["condition_id"],
        dedupe=True, last_error=f"子进程启动即退出：{detail}",
        feedback_dir=DISPATCH_LOG_DIR_REL)


def run_claimed(conn, stage: str, geometry_id: str, condition_id: str | None) -> str:
    """跑一条**已被领取**的任务（子进程入口）。

    行是父进程领的，`claimed_at` 已经写上；这里只负责起脚本、推进状态、清标记。
    """
    if stage in UNIMPLEMENTED_STAGES:
        run = db.run_target(stage, geometry_id, condition_id)
        db.mark_task(conn, run, "pending")
        db.record_report(conn, stage=stage, geometry_id=geometry_id,
                         run_id=f"{geometry_id}__{condition_id or db.SHARED_CONDITION_ID}",
                         attempts=1, condition_id=condition_id, dedupe=True,
                         last_error=f"dispatcher 拒绝启动：{UNIMPLEMENTED_STAGES[stage]}")
        return f"{stage} 未启动：{UNIMPLEMENTED_STAGES[stage]}"

    row = conn.execute(
        "SELECT run_id FROM " + db.STAGE_TARGET[stage]["table"] + " WHERE geometry_id=?"
        + (" AND condition_id=?" if db.STAGE_TARGET[stage]["scoped"] else ""),
        (geometry_id, condition_id) if db.STAGE_TARGET[stage]["scoped"] else (geometry_id,),
    ).fetchone()
    run_id = row["run_id"] if row else None
    if not run_id:
        return f"{stage} {geometry_id} 没有 run_id：领取标记可能已被清，跳过"
    run = db.run_target(stage, geometry_id, condition_id)
    run_dir = db.WORKSPACE_ROOT / RUNS_HIGH_REL / stage / run_id
    log_rel = (Path(RUNS_HIGH_REL) / stage / run_id / LOG_NAME_TEMPLATE.format(
        geometry_id=geometry_id, stage=stage)).as_posix()
    try:
        if stage == "spaceclaim":
            command = spaceclaim_command(conn, geometry_id, run_id)
        elif stage == "fluent_meshing":
            command = fluent_meshing_command(conn, geometry_id, run_id, condition_id)
        else:
            raise SystemExit(f"未知阶段：{stage}")
        db.mark_task(conn, run, "pending", result_dir=db.workspace_rel(run_dir),
                     log_path=log_rel, keep_claim=True)
        stdout, stderr, code, elapsed = _run_child(
            command, TIMEOUT_SECONDS[stage], conn=conn, run_id=run_id)
    finally:
        if stage == "fluent_meshing":
            drop_staged_model(run_id)

    manifest = {}
    actual_dir = run_dir
    if not actual_dir.is_dir():
        moved = db.WORKSPACE_ROOT / "feedback" / stage / run_id
        if moved.is_dir():
            actual_dir = moved
    if actual_dir.is_dir():
        (actual_dir / Path(log_rel).name).write_text(
            f"$ {' '.join(command)}\n\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n",
            encoding="utf-8")
        manifest_path = actual_dir / "run.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                manifest = {}
    if code == 0 and manifest.get("status") == "PASSED":
        note = _generate_point_cloud(conn, geometry_id, run_id) if stage == "spaceclaim" else ""
        db.mark_task(conn, run, "pending")
        return f"{stage} {geometry_id} → pending（{elapsed:.1f}s{note}，等审核）"
    lines = (stderr or "").strip().splitlines() or (stdout or "").strip().splitlines()
    error = (lines[-1] if lines else "没有输出")[:400]
    db.mark_task(conn, run, "pending", bump_attempts=True)
    db.record_report(conn, stage=stage, geometry_id=geometry_id, run_id=run_id,
                     attempts=max(1, _attempts(conn, run)), condition_id=condition_id,
                     dedupe=True, last_error=f"退出码 {code}；{error}",
                     feedback_dir=_feedback_dir(conn, stage, run_id))
    return f"{stage} {geometry_id} → 失败（退出码 {code}，{elapsed:.1f}s）：{error}"


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


def dispatch_datcom_batch(conn, database: str | None = None,
                          batch: dict | None = None) -> str:
    """低保真：一批（同一几何 + 同一高度）一次 DATCOM 调用，然后脚本自检 + 入库。

    这条链路**没有 LLM 审核子智能体**（计划 §6.2）：自检通过就直接由 `record_review`
    推成 `queued`，不通过置 `repairing` 并写 `report`，让主智能体看得见。

    `batch` 给了就用它（spawn 模式：父进程已经领好了）；不给就当场领一批。
    """
    batch = batch or db.claim_datcom_batch(conn)
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
            db.mark_task(conn, db.run_target("datcom", geometry_id, condition_id),
                         "pending", bump_attempts=True)
        db.record_report(conn, stage="datcom", geometry_id=geometry_id, run_id=run_id,
                         attempts=1, condition_id=None, dedupe=True,
                         last_error=f"01_batch_datcom.py 退出码 {code}：{error}",
                         feedback_dir=log_rel)
        return (f"datcom {run_id} → 失败（退出码 {code}，{elapsed:.1f}s）：{error}")

    # 自检与入库交给 review.py：它按 §7.4 核判据，逐工况写 datcom_task 并推成 queued
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
        return (f"datcom {run_id} → queued（{len(condition_ids)} 个工况，{elapsed:.1f}s，"
                f"自检通过并已入库）")
    first = [line for line in (review_out or review_err).splitlines() if "否决" in line]
    detail = first[0].strip() if first else (review_err or review_out).strip().splitlines()[-1:]
    return f"datcom {run_id} → repairing（{elapsed:.1f}s）：{detail}"


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
        description="领一条待处理行并拉起本阶段脚本；不做审核，成功即回到 pending 等审核。",
    )
    parser.add_argument("--stage", choices=list(db.STAGES),
                        help="前台 / 循环 / spawn 模式要跑哪个阶段")
    parser.add_argument("--database", help="库文件路径（默认取 workspace.json）")
    parser.add_argument("--once", action="store_true", help="最多跑一条就退出（前台执行）")
    parser.add_argument("--spawn", action="store_true",
                        help="领一条、起独立子进程、立刻退出（watch.py 用，不会被长任务卡住）")
    parser.add_argument("--interval", type=float, default=LOOP_INTERVAL_SECONDS,
                        help=f"循环模式空闲等待秒数（默认 {LOOP_INTERVAL_SECONDS}）")
    parser.add_argument("--ignore-control", action="store_true",
                        help="忽略 control 表里的 paused/stopped（仅用于单次调试）")
    # 子进程入口：行已经被父进程领走，这里只跑、不领
    parser.add_argument("--run-claimed", metavar="STAGE", help=argparse.SUPPRESS)
    parser.add_argument("claimed_geometry", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("claimed_condition", nargs="?", help=argparse.SUPPRESS)
    # 低保真批次：父进程领好的 run_id + 工况列表（`<run_id>:<cid>,<cid>`），避免子进程再领一次
    parser.add_argument("--claimed-batch", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    db._configure_output()
    args = build_parser().parse_args(argv)
    db_path = db.resolve_database(args.database)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}\n先运行：python scripts/db.py init", file=sys.stderr)
        return 1

    # ---- 子进程入口：跑一条已被领取的任务 ----
    if args.run_claimed:
        stage = args.run_claimed
        conn = db.connect(db_path)
        try:
            if stage == "datcom" and args.claimed_batch:
                run_id, _, condition_text = args.claimed_batch.partition(":")
                message = dispatch_datcom_batch(conn, args.database, batch={
                    "geometry_id": args.claimed_geometry or "",
                    "altitude_m": None,
                    "condition_ids": [item for item in condition_text.split(",") if item],
                    "run_id": run_id})
            elif stage == "datcom":
                message = dispatch_datcom_batch(conn, args.database)
            else:
                message = run_claimed(conn, stage, args.claimed_geometry or "",
                                      args.claimed_condition or None)
        finally:
            conn.close()
        print(f"[{db.utc_sql()}] {message}", flush=True)
        return 0

    if not args.stage:
        print("需要 --stage（或用 --run-claimed 作为子进程入口）。", file=sys.stderr)
        return 2
    stage = args.stage

    while True:
        conn = db.connect(db_path)
        try:
            state, source = db.effective_control(
                conn, workflow=STAGE_WORKFLOW[stage], stage=stage)
            if state != "running" and not args.ignore_control:
                message = f"{stage} 被 {source} 置为 {state}，不领取新任务。"
            elif args.spawn:
                message = spawn_once(conn, stage, args.database) or (
                    f"{stage} 没有待处理任务。")
            else:
                message = dispatch_once(conn, stage, args.database) or (
                    f"{stage} 没有待处理任务。")
        finally:
            conn.close()
        print(f"[{db.utc_sql()}] {message}" if not args.once else message, flush=True)
        if args.once or args.spawn:
            return 0
        if "没有待处理任务" in message or "不领取新任务" in message:
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
