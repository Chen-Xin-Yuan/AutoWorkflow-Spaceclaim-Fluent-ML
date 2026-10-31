# -*- coding: utf-8 -*-
"""启停开关：主智能体（或人）对工作流、阶段、单条任务执行 `start` / `pause` / `stop`。

三种状态与优先级：

    running   正常领取并执行
    paused    不再领新任务，**当前任务允许跑完**（排空式暂停）
    stopped   不再领新任务，并终止 `task_process` 里在跑的进程树；任务置 `cancelled`

优先级 `task` > `stage` > `workflow`。`stopped` / `cancelled` **不会自动恢复**，
必须显式 `start`。

终止进程树不走 `taskkill`：某些会话里 `tasklist` / `taskkill` 会被系统层拒绝
（返回 `Access denied`），而 `OpenProcess(TERMINATE)` + `TerminateProcess` 与
`Toolhelp32` 快照仍然可用。这里用后者，`stop` 才能在任何会话里自己收尾。

-------------------------------------------------------------------------------
输入 / 输出 / 参数配置
-------------------------------------------------------------------------------
输入
    数据库      命令行 `--database` > 环境变量 `MISSILE_DB` > `workspace.json` 的
                `database` 字段（与 `scripts/db.py` 共用同一套解析）。
    `control`   表：本文件写入；`watch.py` / `dispatch.py` 读取生效状态。
    `task_process` 表：要终止的进程。
输出
    只改库（`control` / `task_process` 以及任务状态列），不写文件、不删产物。
    `stop` 之后产物留在 `runs/` 里，不入库也不删除，人工判断是否保留。
参数配置
    KILL_TIMEOUT_SECONDS   终止进程树后等它消失的上限（验收要求 5 秒内消失）
    KILL_POLL_SECONDS      轮询间隔
    KILL_EXIT_CODE         强杀时写进目标进程的退出码
    STAGE_WORKFLOW         阶段 → 工作流，决定按工作流停哪些阶段
    PROCESS_* / TH32CS_* / STILL_ACTIVE / ERROR_*（见 §1）
                           Win32 固定常量，不是可调参数，不要按业务需要改
覆盖优先级
    命令行 > `workspace.json`。理由文本原样写进 `control.reason`，便于事后追责。
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
KILL_TIMEOUT_SECONDS = 5.0
KILL_POLL_SECONDS = 0.2
KILL_EXIT_CODE = 1
STAGE_WORKFLOW = {
    "spaceclaim": "high",
    "fluent_meshing": "high",
    "fluent_solution": "high",
    "datcom": "low",
}

# Win32 固定常量（协议值，不是可调参数）
PROCESS_TERMINATE = 0x0001
TH32CS_SNAPPROCESS = 0x00000002
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87


# =============================================================================
# 2. 进程终止
# =============================================================================
_KERNEL32 = None
_PROCESSENTRY32W = None


def _kernel32():
    """惰性绑定 kernel32，返回 `(ctypes, wintypes, kernel32)`。

    惰性绑定是为了不把 Win32 加载塞进 import 期：没有进程要杀时（例如 `status`）
    完全不需要碰这些 API。
    """
    global _KERNEL32, _PROCESSENTRY32W
    if _KERNEL32 is not None:
        return _KERNEL32

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    _PROCESSENTRY32W = PROCESSENTRY32W
    _KERNEL32 = (ctypes, wintypes, kernel32)
    return _KERNEL32


def _snapshot_parents() -> dict[int, int]:
    """当前全部进程的 `pid → 父 pid` 快照；拿不到时返回空字典（退化为只杀单进程）。"""
    ctypes, wintypes, kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return {}
    parents: dict[int, int] = {}
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return parents


def _process_tree(root: int) -> list[int]:
    """以 `root` 为根的整棵进程树，**叶子在前**。

    先杀子进程再杀父进程：父进程先死的话，子进程会变孤儿、父 pid 也被回收，
    再想找它们就没有线索了。ANSYS / datcom.exe 都会再叉子进程，所以这一步必要。
    """
    parents = _snapshot_parents()
    if not parents:
        return [root]
    children: dict[int, list[int]] = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)
    order: list[int] = []
    stack = [root]
    while stack:
        current = stack.pop()
        if current in order:
            continue
        order.append(current)
        stack.extend(children.get(current, ()))
    order.reverse()
    return order


def _terminate(pid: int) -> bool:
    """对单个 pid 调 `TerminateProcess`；进程已经不在了也算成功。"""
    ctypes, _wintypes, kernel32 = _kernel32()
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == ERROR_INVALID_PARAMETER:
            return True                     # 这个 pid 已经不存在了
        print(f"打开 pid {pid} 失败（Win32 错误 {error}），无权终止。", file=sys.stderr)
        return False
    try:
        if kernel32.TerminateProcess(handle, KILL_EXIT_CODE):
            return True
        error = ctypes.get_last_error()
        reason = "权限不足" if error == ERROR_ACCESS_DENIED else f"Win32 错误 {error}"
        print(f"终止 pid {pid} 失败：{reason}。", file=sys.stderr)
        return False
    finally:
        kernel32.CloseHandle(handle)


def kill_tree(pid: int) -> bool:
    """终止整棵进程树（ANSYS / datcom.exe 都会再叉子进程，只杀父进程会留孤儿）。"""
    try:
        targets = _process_tree(pid)
    except (OSError, AttributeError) as exc:
        print(f"枚举 pid {pid} 的进程树失败：{exc}", file=sys.stderr)
        targets = [pid]
    targets = targets or [pid]
    failed = [target for target in targets if not _terminate(target)]
    if failed:
        print("未能终止的 pid：" + "、".join(str(item) for item in failed), file=sys.stderr)
        return False
    return True


def wait_gone(pid: int, timeout: float = KILL_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not db.pid_alive(pid):
            return True
        time.sleep(KILL_POLL_SECONDS)
    return not db.pid_alive(pid)


def in_scope(kind: str, name: str, run: dict | None, run_id: str) -> bool:
    if kind == "task":
        return run_id == name
    if run is None:
        return False
    if kind == "stage":
        return run["stage"] == name
    return STAGE_WORKFLOW.get(run["stage"]) == name


# =============================================================================
# 3. 命令
# =============================================================================
def cmd_set(args, state: str) -> int:
    conn = db.connect(db.resolve_database(args.database))
    try:
        db.set_control(conn, args.target, state, reason=args.reason, updated_by=args.by)
        print(f"{args.target} → {state}" + (f"（{args.reason}）" if args.reason else ""))
        if state != "stopped":
            state_now, source = db.effective_control(conn, **_scope_of(args.target))
            print(f"生效状态：{state_now}" + (f"（来自 {source}）" if source else "（默认）"))
            return 0

        # ---- stopped：终止范围内的进程树 ----
        kind, name = db.parse_target(args.target)
        killed, cancelled, failed = [], [], []
        for row in conn.execute("SELECT run_id, pid FROM task_process").fetchall():
            run_id, pid = row["run_id"], int(row["pid"])
            run = db.resolve_run(conn, run_id)
            if not in_scope(kind, name, run, run_id):
                continue
            if db.pid_alive(pid):
                kill_tree(pid)
            if not wait_gone(pid):
                # 进程没死就绝不标 cancelled、也不删 pid 登记：
                # 否则"已叫停"是假话，而且再也找不到它去补刀。
                failed.append(f"pid {pid}（{run_id}）")
                continue
            if run is not None:
                db.mark_task(conn, run, "cancelled")
                cancelled.append(f"{run['stage']}:{run_id}")
            conn.execute("DELETE FROM task_process WHERE run_id=?", (run_id,))
            conn.commit()
            killed.append(f"pid {pid}（已退出）")
        if killed:
            print("已终止：" + "、".join(killed))
        if cancelled:
            print("已置 cancelled：" + "、".join(cancelled) + "（产物保留在 runs/，不入库）")
        if failed:
            print("终止失败，任务仍为 running，pid 登记保留：" + "、".join(failed))
            print("  常见原因是当前进程权限不足；请在有权限的终端重跑同一条命令，"
                  "或手工结束该进程后再执行一次 stop。", file=sys.stderr)
            return 1
        if not killed:
            print("范围内没有在跑的进程；后续领取已停止。")
        return 0
    finally:
        conn.close()


def _scope_of(target: str) -> dict:
    kind, name = db.parse_target(target)
    if kind == "workflow":
        return {"workflow": name}
    if kind == "stage":
        return {"workflow": STAGE_WORKFLOW.get(name), "stage": name}
    return {"run_id": name}


def cmd_status(args) -> int:
    conn = db.connect(db.resolve_database(args.database))
    try:
        if args.target:
            kind, name = db.parse_target(args.target)
            state, source = db.effective_control(conn, **_scope_of(args.target))
            print(f"{args.target} → {state}" + (f"（来自 {source}）" if source else "（默认 running）"))
            return 0

        print("== 启停开关 ==")
        rows = conn.execute("SELECT * FROM control ORDER BY target").fetchall()
        if not rows:
            print("  （空：所有工作流与阶段都是默认的 running）")
        for row in rows:
            reason = f" / {row['reason']}" if row["reason"] else ""
            print(f"  {row['target'].ljust(28)} {row['state'].ljust(9)} "
                  f"{row['updated_by']} / {row['updated_at']}{reason}")

        print("\n== 生效状态 ==")
        for workflow in db.WORKFLOWS:
            state, source = db.effective_control(conn, workflow=workflow)
            print(f"  workflow:{workflow}".ljust(30) + state
                  + (f"（来自 {source}）" if source else ""))
        for stage in db.STAGES:
            state, source = db.effective_control(
                conn, workflow=STAGE_WORKFLOW.get(stage), stage=stage)
            print(f"  stage:{stage}".ljust(30) + state
                  + (f"（来自 {source}）" if source else ""))

        print("\n== 任务计数 ==")
        counts = [
            ("建模", "geometry_state", "model_status"),
            ("网格", "geometry_state", "mesh_status"),
            ("求解", "cfd_task", "status"),
            ("低保真", "datcom_task", "status"),
        ]
        header = f"  {'':10}" + "".join(f"{status:>11}" for status in db.TASK_STATUSES)
        print(header)
        for label, table, column in counts:
            row = {status: 0 for status in db.TASK_STATUSES}
            for item in conn.execute(
                f"SELECT {column} AS status, COUNT(*) AS n FROM {table} GROUP BY {column}"
            ):
                row[item["status"]] = item["n"]
            print(f"  {label:10}" + "".join(f"{row[s]:>11}" for s in db.TASK_STATUSES))
        print(f"\n  在跑的进程：{conn.execute('SELECT COUNT(*) FROM task_process').fetchone()[0]}")
        return 0
    finally:
        conn.close()


# =============================================================================
# 4. 命令行
# =============================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="control.py",
        description="启停开关：start / pause / stop / status。优先级 task > stage > workflow。",
    )
    parser.add_argument("action", choices=["start", "pause", "stop", "status"])
    parser.add_argument("--target", help="workflow:high | stage:fluent_meshing | task:<run_id>")
    parser.add_argument("--reason", help="理由，写进 control.reason")
    parser.add_argument("--by", default="main-agent", help="谁改的，写进 control.updated_by")
    parser.add_argument("--database", help="库文件路径（默认取 workspace.json）")
    return parser


def main(argv: list[str] | None = None) -> int:
    db._configure_output()
    args = build_parser().parse_args(argv)
    if args.action != "status" and not args.target:
        print("start / pause / stop 必须给 --target。", file=sys.stderr)
        return 2
    if args.action == "status":
        return cmd_status(args)
    return cmd_set(args, {"start": "running", "pause": "paused",
                          "stop": "stopped"}[args.action])


if __name__ == "__main__":
    raise SystemExit(main())
