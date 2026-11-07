# -*- coding: utf-8 -*-
"""watcher：唯一的推进器。只认 `queued`，把它推到下一个流程。

每 `SCAN_INTERVAL_SECONDS` 扫一轮，按顺序做这些事：

1. **修理超时上报**：`repairing` 超过 `db.REPAIR_TIMEOUT_SECONDS` 还没解决 → 写一条
   `open` 的 `report`（按 stage + 几何 + 工况去重），并在输出里明确写出来；
2. **僵死回收**：`claimed_at` 还挂着但 `task_process` 里的 pid 已不存在 →
   `repairing` + `attempts+1` + 上报；
3. **复活被 `gc` 删过产物的行**：`queued` 但路径为 NULL → 回落 `pending`；
4. **建模过审 → 建网格行**：`geometry_state.model_status='queued'` 且有 `model_path`，
   就给该几何**每个还没有 `mesh_task` 行的工况**建一条 `pending` 的 `mesh_task`；
5. **推进 `queued`**：
   * 网格 `mesh_task.status='queued'` 且有 `mesh_path` → 触发求解（**求解未实现**，
     明确跳过并说明，不假装能跑）；
   * 求解数据 `cfd_task.status='queued'` → 推到数据库（**占位**，见下）；
   * 低保真 `datcom_task.status='queued'` → 已入库，不需要推进。

**"触发"怎么落地**：`dispatch.py --spawn` 领一条、起一个独立子进程、立刻退出。
watch 自己不跑 30 分钟级的任务，所以循环永远不会被卡住；防重复领取全靠
`claimed_at` 的原子写——被领走的行下一轮根本不在候选里，同一个任务不会被起第二个进程。

-------------------------------------------------------------------------------
输入 / 输出 / 参数配置
-------------------------------------------------------------------------------
输入
    数据库      命令行 `--database` > 环境变量 `MISSILE_DB` > `workspace.json` 的
                `database` 字段（解析逻辑与 `scripts/db.py` 共用，本文件不重复实现）。
    `control`   表里的启停开关：被 `paused` / `stopped` 的工作流或阶段整轮跳过。
输出
    库          只经 `scripts/db.py` 的函数写（`geometry_state` / `mesh_task` /
                `cfd_task` / `datcom_task` / `report` / `task_process`），本文件不含写 SQL。
    子进程      网格 / 建模阶段经 `dispatch.py --spawn` 起独立进程；日志落在
                `dispatch.DISPATCH_LOG_DIR_REL`（`data_lib/runtime/logs/spawn_*.log`）。
    标准输出    每轮发生过的事；没有任何变化时打印"无变化"。
参数配置
    SCAN_INTERVAL_SECONDS     循环模式的扫描间隔（秒）
    REPAIR_TIMEOUT_SECONDS    修理超时秒数（默认取 `db.REPAIR_TIMEOUT_SECONDS`）
    DISPATCH_STAGES           本 watcher 会真正触发脚本的阶段；求解不在其中
    STAGE_WORKFLOW            阶段 → 工作流，决定该看哪个启停开关
覆盖优先级
    命令行 > `workspace.json`。时间戳一律 UTC（由 db.py 统一生成）。

只巡检不派发：`--no-dispatch` 跳过第 6 步（不起任何子进程），其余巡检步骤照常执行。
验收与排障时用它——默认模式下 `--once` 一旦发现 `pending` 的网格就会真的拉起 Fluent
（30 分钟级），不该为了看一行超时报告就烧一次机器。
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
import dispatch  # noqa: E402  （同目录的 scripts/dispatch.py，唯一拉起进程的地方）

# =============================================================================
# 1. 参数配置区
# =============================================================================
SCAN_INTERVAL_SECONDS = 30
REPAIR_TIMEOUT_SECONDS = db.REPAIR_TIMEOUT_SECONDS
# 本 watcher 会真正拉起脚本的阶段。`fluent_solution` 不在里面：求解脚本没有实现，
# 它只做"明确拒绝/跳过并说明"，不会启动任何进程，也不会假装把数据推进库。
DISPATCH_STAGES = ("fluent_meshing",)
# 还没实现、只能明确跳过的阶段（写在这里是为了让输出能说清楚"为什么没动"）
UNIMPLEMENTED_STAGES = {
    "fluent_solution": "求解脚本未实现：queued 的网格不会启动求解进程（dispatch 也会拒绝启动）。",
}
STAGE_WORKFLOW = {
    "spaceclaim": "high",
    "fluent_meshing": "high",
    "fluent_solution": "high",
    "datcom": "low",
}
# 建模阶段由 dispatch.py 直接跑（不是本 watcher 的职责），所以这里不列它。
NO_CHANGES_MESSAGE = "无变化。"


# =============================================================================
# 2. 单轮扫描
# =============================================================================
def scan_once(database: str | None, *, dispatch_enabled: bool = True) -> list[str]:
    """跑一轮，返回发生过的事（没人消费就打印出来）。

    `dispatch_enabled=False`（命令行 `--no-dispatch`）时跳过第 6 步：仍做修理超时上报、
    僵死回收、复活、建网格行，但**不拉起任何子进程**。用于验收与排障——默认模式下
    一次扫描就可能启动 30 分钟级的 Fluent 网格任务。
    """
    events: list[str] = []
    conn = db.connect(db.resolve_database(database))
    try:
        # ---- 1. 修理超时 ----
        for item in db.scan_repair_timeouts(conn, REPAIR_TIMEOUT_SECONDS):
            target = item["geometry_id"] + (f" / {item['condition_id']}"
                                            if item["condition_id"] else "")
            events.append(
                f"修理超时：{item['stage']} {target} 已停滞超过 "
                f"{REPAIR_TIMEOUT_SECONDS / 60:g} 分钟"
                f"（repair_started_at {item['repair_started_at']}）"
                f" → 上报 {item['report_id']}，等主智能体处理")

        # ---- 2. 僵死回收 ----
        for item in db.reclaim_zombies(conn):
            if item.get("stage") is None:
                events.append(f"僵死回收：{item['run_id']}（pid {item['pid']} 已消失）"
                              " → 库里已找不到对应任务，只清登记")
                continue
            events.append(
                f"僵死回收：{item['stage']} {item['geometry_id']}（pid {item['pid']} 已消失）"
                f" → repairing / attempts+1 / 上报 {item['report_id']}")

        # ---- 3. 复活被 gc 删过产物的行 ----
        for key in db.revive_deleted_artifacts(conn):
            events.append(f"复活：{key} 的产物已被 gc 删除 → 回落 pending")

        # ---- 4. 建模过审 → 为每个工况建网格行 ----
        for geometry_id in db.geometries_ready_for_mesh(conn):
            created = db.ensure_mesh_tasks(conn, geometry_id)
            if created:
                events.append(
                    f"网格建行：{geometry_id} 为 {len(created)} 个工况新建 mesh_task"
                    f"（pending）：{', '.join(created)}")

        # ---- 5. 推进 queued 的网格 → 求解 ----
        for stage, message in UNIMPLEMENTED_STAGES.items():
            pending = conn.execute(
                "SELECT COUNT(*) FROM mesh_task WHERE status='queued'"
                " AND mesh_path IS NOT NULL AND claimed_at IS NULL").fetchone()[0]
            if pending and stage == "fluent_solution":
                events.append(f"跳过 {stage}：{pending} 条 queued 网格可以求解，但{message}")

        # ---- 6. 触发真正能跑的阶段（每个阶段每轮最多起一个子进程）----
        if not dispatch_enabled:
            queued_mesh = conn.execute(
                "SELECT COUNT(*) FROM mesh_task WHERE status='pending'"
                " AND claimed_at IS NULL").fetchone()[0]
            events.append(f"只巡检：不派发（{queued_mesh} 条 pending 网格未被领取）")
            return events
        for stage in DISPATCH_STAGES:
            workflow = STAGE_WORKFLOW[stage]
            state, source = db.effective_control(conn, workflow=workflow, stage=stage)
            if state != "running":
                events.append(f"跳过 {stage}：{source} = {state}")
                continue
            message = dispatch.spawn_once(conn, stage, database)
            if message:
                events.append(message)
    finally:
        conn.close()
    return events


# =============================================================================
# 3. 命令行
# =============================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watch.py",
        description="watcher：唯一推进器。只认 queued，触发下一阶段、僵死回收、修理超时上报。",
    )
    parser.add_argument("--database", help="库文件路径（默认取 workspace.json）")
    parser.add_argument("--once", action="store_true", help="只扫一轮就退出")
    parser.add_argument("--interval", type=float, default=SCAN_INTERVAL_SECONDS,
                        help=f"循环模式的扫描间隔秒数（默认 {SCAN_INTERVAL_SECONDS}）")
    parser.add_argument("--repair-timeout", type=float, default=REPAIR_TIMEOUT_SECONDS,
                        help=f"修理超时秒数（默认 {REPAIR_TIMEOUT_SECONDS}）")
    parser.add_argument("--no-dispatch", action="store_true",
                        help="只巡检不派发：跳过拉起子进程（验收/排障用，避免真起 Fluent）")
    return parser


def main(argv: list[str] | None = None) -> int:
    db._configure_output()
    args = build_parser().parse_args(argv)
    db_path = db.resolve_database(args.database)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}\n先运行：python scripts/db.py init", file=sys.stderr)
        return 1

    global REPAIR_TIMEOUT_SECONDS
    REPAIR_TIMEOUT_SECONDS = args.repair_timeout

    if args.once:
        events = scan_once(args.database, dispatch_enabled=not args.no_dispatch)
        print("\n".join(events) if events else NO_CHANGES_MESSAGE)
        return 0

    print(f"watcher 启动：{db_path}，每 {args.interval:g} 秒扫一次，Ctrl+C 停止。")
    try:
        while True:
            try:
                events = scan_once(args.database, dispatch_enabled=not args.no_dispatch)
            except Exception as exc:                     # 一次异常不该打死循环
                print(f"[{db.utc_sql()}] 扫描失败：{exc}", file=sys.stderr)
            else:
                stamp = db.utc_sql()
                for event in events or [NO_CHANGES_MESSAGE]:
                    print(f"[{stamp}] {event}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nwatcher 已停止（库内状态不受影响）。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
