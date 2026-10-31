# -*- coding: utf-8 -*-
"""watcher：只推进状态，不启动进程。

每 30 秒扫一次库，做三件事：

1. 把 `pending` 的建模 / 网格 / 求解 / 低保真任务行认领为 `queued`；
2. 僵死回收：`running` 但 `task_process` 里的 pid 已经不存在 → `rejected` + `attempts+1` + 上报单；
3. 复活被 `gc` 删过产物的几何：又有 `pending` 工况且产物路径为 NULL → 建模/网格回落 `pending`。

**它不启动任何 ANSYS / DATCOM 进程**，那是 `dispatch.py` 的事。这样"谁在跑"与
"谁该跑"永远是两件事，停下 watcher 不会打断正在跑的算例。

-------------------------------------------------------------------------------
输入 / 输出 / 参数配置
-------------------------------------------------------------------------------
输入
    数据库      命令行 `--database` > 环境变量 `MISSILE_DB` > `workspace.json` 的
                `database` 字段（解析逻辑与 `scripts/db.py` 共用，本文件不重复实现）。
    `control`   表里的启停开关：被 `paused` / `stopped` 的工作流或阶段整轮跳过。
输出
    只改库（`geometry_state` / `cfd_task` / `datcom_task` / `report` / `task_process`），
    不写任何文件、不建目录。所有写入都调用 `scripts/db.py` 的函数，本文件不含 SQL 写入语。
参数配置
    SCAN_INTERVAL_SECONDS  循环模式下的扫描间隔（秒）
    CLAIM_BATCH_PER_STAGE  每轮每阶段最多认领几条（并发上限由 dispatch.py 控制，
                           这里只是别让一条 SQL 认领几百行）
    STAGE_WORKFLOW         阶段 → 工作流，决定该看哪个启停开关
覆盖优先级
    命令行 > `workspace.json`。时间戳一律 UTC（由 db.py 统一生成）。
-------------------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 脚本根路径必须先定义：下面的 import 依赖它。
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import db  # noqa: E402  （同目录的 scripts/db.py，唯一的写库入口）

# =============================================================================
# 1. 参数配置区
# =============================================================================
SCAN_INTERVAL_SECONDS = 30
CLAIM_BATCH_PER_STAGE = 8
STAGE_WORKFLOW = {
    "spaceclaim": "high",
    "fluent_meshing": "high",
    "fluent_solution": "high",
    "datcom": "low",
}
STAGE_ORDER = ("spaceclaim", "fluent_meshing", "fluent_solution", "datcom")


# =============================================================================
# 2. 单轮扫描
# =============================================================================
def scan_once(database: str | None) -> list[str]:
    """跑一轮，返回发生过的事（没人消费就打印出来）。"""
    events: list[str] = []
    conn = db.connect(db.resolve_database(database))
    try:
        # ---- 僵死回收 ----
        for item in db.reclaim_zombies(conn):
            events.append(
                f"僵死回收：{item['run_id']}（pid {item['pid']} 已消失）"
                f" → rejected / 已上报"
            )

        # ---- 复活被删产物的几何 ----
        for geometry_id in db.revive_deleted_artifacts(conn):
            events.append(f"复活：{geometry_id} 的产物已被 gc 删除，建模/网格回落 pending")

        # ---- 认领 pending → queued ----
        for stage in STAGE_ORDER:
            workflow = STAGE_WORKFLOW[stage]
            state, source = db.effective_control(conn, workflow=workflow, stage=stage)
            if state != "running":
                events.append(f"跳过 {stage}：{source} = {state}")
                continue

            if stage in db.STAGE_STATUS_FIELD:
                claimed = db.claim_geometry_stage(conn, stage, CLAIM_BATCH_PER_STAGE)
                for geometry_id in claimed:
                    events.append(f"{stage}：{geometry_id} pending → queued")
            elif stage == "fluent_solution":
                for geometry_id, condition_id in db.claim_cfd_tasks(conn, CLAIM_BATCH_PER_STAGE):
                    events.append(
                        f"{stage}：{geometry_id} / {condition_id} pending → queued"
                    )
            elif stage == "datcom":
                for geometry_id, condition_id in db.claim_datcom_tasks(conn, CLAIM_BATCH_PER_STAGE):
                    events.append(
                        f"{stage}：{geometry_id} / {condition_id} pending → queued"
                    )
    finally:
        conn.close()
    return events


# =============================================================================
# 3. 命令行
# =============================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watch.py",
        description="watcher：pending → queued、僵死回收、产物复活。不启动任何进程。",
    )
    parser.add_argument("--database", help="库文件路径（默认取 workspace.json）")
    parser.add_argument("--once", action="store_true", help="只扫一轮就退出")
    parser.add_argument("--interval", type=float, default=SCAN_INTERVAL_SECONDS,
                        help=f"循环模式的扫描间隔秒数（默认 {SCAN_INTERVAL_SECONDS}）")
    return parser


def main(argv: list[str] | None = None) -> int:
    db._configure_output()
    args = build_parser().parse_args(argv)
    db_path = db.resolve_database(args.database)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}\n先运行：python scripts/db.py init", file=sys.stderr)
        return 1

    if args.once:
        events = scan_once(args.database)
        print("\n".join(events) if events else "无变化。")
        return 0

    print(f"watcher 启动：{db_path}，每 {args.interval:g} 秒扫一次，Ctrl+C 停止。")
    try:
        while True:
            try:
                events = scan_once(args.database)
            except Exception as exc:                     # 一次异常不该打死循环
                print(f"[{db.utc_sql()}] 扫描失败：{exc}", file=sys.stderr)
            else:
                if events:
                    stamp = db.utc_sql()
                    for event in events:
                        print(f"[{stamp}] {event}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nwatcher 已停止（库内状态不受影响）。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
