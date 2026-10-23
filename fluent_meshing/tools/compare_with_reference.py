#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Compare a generated workflow file (*.wft) with the reference workflow of the
original ``missile.msh.h5`` (the file that ``fluent_meshing.md`` documents).

Usage
-----
    python tools/compare_with_reference.py                       # uses the default paths
    python tools/compare_with_reference.py <new.wft> [<ref.wft>]

Numbers and numeric strings are treated as equal, so only real parameter
differences are reported.  Bookkeeping entries (paths, "Complete*" helper
lists, legacy "Old*" fields) are ignored on purpose - see IGNORED_KEYS.
"""

from __future__ import annotations

import json
import os
import sys

# ---------------------------------------------------------------------------
# 输入 / 输出 / 参数配置
# ---------------------------------------------------------------------------
# 输入：第 1/2 个位置参数分别覆盖 DEFAULT_NEW / DEFAULT_REF；显式相对路径
# 按调用时工作目录解析，以下默认路径按本脚本所在仓库解析。
# 输出：仅向标准输出打印比较结果，不写报告文件、不修改输入文件。
# 以下默认为历史证据文件，不代表新运行验收；任务字段/别名表是比较协议。
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_NEW = os.path.join(
    os.path.dirname(PROJECT_DIR), "data_lib", "legacy", "fluent_meshing", "generated", "missile_mesh_workflow_files", "missile_mesh.wft"
)
DEFAULT_REF = os.path.join(os.path.dirname(PROJECT_DIR), "data_lib", "legacy", "fluent_meshing", "reference", "missile_workflow_files", "missile.wft")
DISPLAY_DIFF_LIMIT = 300
DISPLAY_ARGUMENT_LIMIT = 140

# Explicit task-scoped recording fields; actual calculation inputs stay strict.
TASK_RECORDING_FIELDS = {
    "Import Geometry": {"FileName", "NumParts"},
    "Generate the Surface Mesh": {"OriginalZones"},
    "Update Regions": {"OldRegionNameList", "OldRegionTypeList"},
    "Generate the Volume Mesh": {"OldRegionTetMaxCellLengthList", "OldRegionTetGrowthRateList"},
    "Add Boundary Layers": {"AddChild"},
    "last-ratio_1": {"AddChild", "CompleteBLRegionList", "CompleteBLZoneList", "CompleteRegionScope"},
}
for _name in ("missile_body", "missile_nose", "missile_nose_tip", "missile_fin", "boi_1"):
    TASK_RECORDING_FIELDS[_name] = {"CompleteFaceLabelList"}

# the documentation renames TaskObject14's control to missile_nose_tip
TASK_ALIASES = {"curvature_1": "missile_nose_tip"}


def norm(value):
    """Normalise a value so that 3 == "3" == 3.0."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return f"{float(value):.10g}"
    if isinstance(value, str):
        try:
            return f"{float(value):.10g}"
        except ValueError:
            return value
    if isinstance(value, list):
        return [norm(v) for v in value]
    if isinstance(value, dict):
        return {k: norm(v) for k, v in value.items()}
    return value


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        root = json.load(fh)["workflow"]["ROOT"]
    tasks = {}
    for key, task in root.items():
        if key.startswith("TaskObject"):
            tasks[TASK_ALIASES.get(task["_name_"], task["_name_"])] = task
    return tasks


def comparable_arguments(name, task):
    ignored = TASK_RECORDING_FIELDS.get(name, set())
    arguments = {key: value for key, value in (task.get("Arguments") or {}).items() if key not in ignored}
    if name == "missile_nose_tip" and arguments.get("BOIControlName") in ("curvature_1", "missile_nose_tip"):
        arguments["BOIControlName"] = "missile_nose_tip"
    return arguments


def task_differences(name, old_task, new_task):
    old_args = comparable_arguments(name, old_task)
    new_args = comparable_arguments(name, new_task)
    return [f"{key}: reference={old_args.get(key)!r} generated={new_args.get(key)!r}"
            for key in sorted(set(old_args) | set(new_args))
            if norm(old_args.get(key)) != norm(new_args.get(key))]


def main() -> int:
    new_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_NEW
    ref_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_REF
    for path in (new_path, ref_path):
        if not os.path.exists(path):
            print(f"file not found: {path}")
            return 2

    new, ref = load(new_path), load(ref_path)
    print(f"reference workflow : {ref_path}")
    print(f"generated workflow : {new_path}")
    print()
    print(f"{'task':<28} {'result':<10} details")
    print("-" * 100)

    differences = 0
    for name in sorted(set(new) | set(ref)):
        old_task, new_task = ref.get(name), new.get(name)
        if old_task is None or new_task is None:
            print(f"{name:<28} {'MISSING':<10} "
                  f"reference={bool(old_task)} generated={bool(new_task)}")
            differences += 1
            continue
        new_args = comparable_arguments(name, new_task)
        diffs = task_differences(name, old_task, new_task)
        if diffs:
            differences += 1
            print(f"{name:<28} {'DIFFER':<10} " + " | ".join(diffs)[:DISPLAY_DIFF_LIMIT])
        else:
            print(f"{name:<28} {'identical':<10} "
                  f"{json.dumps(norm(new_args), ensure_ascii=False)[:DISPLAY_ARGUMENT_LIMIT]}")

    print()
    print(f"tasks compared       : {len(set(new) | set(ref))}")
    print(f"tasks with real diffs: {differences}")
    return 0 if differences == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
