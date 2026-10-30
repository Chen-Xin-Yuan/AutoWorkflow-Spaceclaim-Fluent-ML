# -*- coding: utf-8 -*-
"""演示 / 冒烟算例：固定的一套几何与来流，不读写输入总表。

`01_batch_datcom.py --demo` 的唯一数据来源。正式运行不会导入本模块
（01 里是函数内懒导入），所以演示配置不会污染正式流程。

产物写到 `data_lib/runs/low/<算例名>/`，算例名不来自总表：
它不是正式归档，`data_lib/runs/low/README.md` 的目录表里也不会列出它，
验证完请手动删掉那次演示目录。

配置区（改演示几何 / 来流 / 算例只改这里）：
    DEMO_MACH      命令行没给 --mach 时的默认马赫数列表
    DEMO_ALPHA     命令行没给 --alpha 时的默认攻角列表（度）
    DEMO_ALTITUDE  命令行没给 --alt 时的默认飞行高度（m），0 = 海平面
    DEMO_CASES     算例列表；每项 name + 可选 geometry 增量 / mach / alpha / alt /
                   le_mode / fin_phase / cruciform_count / xcg
基准几何是 `missile_spec.BASELINE_GEOMETRY`，与工作区 README 的基准参数表同一份。
舵面翼型与鼻锥头型**不能**按算例覆盖：它们只来自 `missile_spec.py` 前部的配置区，
想换翼型 / 头型请改那里（改完两侧脚本同时生效）。
唯一的外部输入是 --params 指定的几何 JSON（11 个主尺寸，单位 m）。
"""

from __future__ import annotations

import json
import os
import re

from datcom_spec import (
    REQUIRED_GEOMETRY_KEYS,
    normalize_geometry,
)
from missile_spec import BASELINE_GEOMETRY

# =============================================================================
# 参数配置区
# =============================================================================
DEMO_MACH = [0.80, 0.84, 0.88, 0.92, 0.96, 1.00]
DEMO_ALPHA = [4.0, 4.5, 5.0, 5.5, 6.0]
DEMO_ALTITUDE = 0.0

# 演示算例。每一项 = 一个算例 = 结果根下的一个 <name>\ 目录。
# "geometry" 里只需写要改的项，其余沿用 BASELINE_GEOMETRY。
# 单算例自己写了 mach/alpha/alt 时，优先用它，命令行覆盖不到它。
DEMO_CASES = [
    {
        "name": "readme_baseline",   # README 基准几何 + 全部 30 个来流工况
        "geometry": BASELINE_GEOMETRY,
    },
]
# 示例（把下面注释放开即可做对比；改完记得验证完删掉演示归档）：
#     {"name": "longer_nose", "geometry": {"L_N": 0.60}},
#     {"name": "bigger_rear_fin", "geometry": {"B_F": 0.25, "C_TF": 0.22}},
#     {"name": "m12_alpha4", "geometry": {}, "mach": [1.2], "alpha": [4.0]},
# 注意：翼型（HEX / NACA 与三个参数）和鼻锥头型不在算例里，改 missile_spec.py。

# 每个算例允许出现的键（除 name / geometry 外的覆盖项）。
# 外形类的翼型 / 头型故意不在其中：它们属于 missile_spec.py，不能按算例改。
CASE_OVERRIDE_KEYS = (
    "mach", "alpha", "alt", "altitude_text", "xcg", "timeout",
    "le_mode", "fin_phase", "cruciform_count",
)


def build(args):
    """
    按命令行参数构造演示算例列表。

    args 是 `01_batch_datcom.py` 的 argparse 结果，用到其中演示专用开关：
    --params / --name / --mach / --alpha / --alt / --timeout。

    Returns
    -------
    list[dict] : 每项含 name / geometry（已校验）/ mach / alpha / alt / timeout，
                 以及 CASES 里给出的其它可选覆盖。
    """
    flow = _flow_overrides(args)
    cases = _cases_from_params(args, flow) if args.params else _cases_from_config(flow)
    _check_unique_names(cases)
    for case in cases:
        case.setdefault("mach", flow.get("mach") or list(DEMO_MACH))
        case.setdefault("alpha", flow.get("alpha") or list(DEMO_ALPHA))
        case.setdefault("alt", DEMO_ALTITUDE if flow.get("alt") is None else flow["alt"])
        case.setdefault("timeout", args.timeout)
    return cases


def _flow_overrides(args):
    """命令行里的来流覆盖，顺带做演示专用取值校验。"""
    flow = {}
    if args.mach:
        flow["mach"] = [float(m) for m in args.mach]
    if args.alpha:
        flow["alpha"] = [float(a) for a in args.alpha]
    if args.alt is not None:
        flow["alt"] = float(args.alt)

    for key in ("mach", "alpha"):
        if key in flow and not flow[key]:
            raise ValueError(f"--{key} 不能为空。")
    if flow.get("mach") and min(flow["mach"]) <= 0:
        raise ValueError("马赫数必须大于 0。")
    if flow.get("alpha") and any(abs(a) >= 90 for a in flow["alpha"]):
        raise ValueError("攻角（度）的绝对值必须小于 90。")
    if flow.get("alt", 0.0) < 0:
        raise ValueError("飞行高度不能为负。")
    return flow


def _cases_from_params(args, flow):
    """--params 分支：每个 JSON 文件一个算例，11 个主尺寸必须齐全。"""
    if args.name and len(args.params) != 1:
        raise ValueError("--name 只能和单个 --params 文件一起使用。")
    cases = []
    for path in args.params:
        # utf-8-sig：兼容 Windows 记事本 / PowerShell 写出的带 BOM 的 JSON
        with open(path, "r", encoding="utf-8-sig") as f:
            raw = json.load(f)
        unknown = [k for k in raw if k not in REQUIRED_GEOMETRY_KEYS]
        if unknown:
            print(f"[INFO] {os.path.basename(path)}: 忽略非几何字段 {', '.join(unknown)}")
        stem = os.path.splitext(os.path.basename(path))[0]
        name = args.name or re.sub(r"[^0-9A-Za-z._-]+", "_", stem).strip("_") or "case"
        cases.append(_make_case(name, raw, dict(flow), strict=True))
    return cases


def _cases_from_config(flow):
    """DEMO_CASES 分支：算例自己写了的来流优先于命令行覆盖。"""
    cases = []
    for entry in DEMO_CASES:
        if not isinstance(entry, dict) or "name" not in entry:
            raise ValueError("DEMO_CASES 的每个元素必须是含 'name' 的字典。")
        unknown = [k for k in entry if k not in ("name", "geometry", *CASE_OVERRIDE_KEYS)]
        if unknown:
            raise ValueError(
                f"算例 '{entry['name']}' 含不能识别的键：{', '.join(unknown)}。"
                f"可用的覆盖键见 CASE_OVERRIDE_KEYS；"
                f"翼型与鼻锥头型不能按算例改，请改 missile_spec.py。"
            )
        extra = {k: v for k, v in entry.items() if k not in ("name", "geometry")}
        case = _make_case(entry["name"], entry.get("geometry", {}), extra)
        case.update({k: v for k, v in flow.items() if k not in case})
        cases.append(case)
    return cases


def _make_case(name, geometry, case=None, strict=False):
    """
    构造一个算例。

    strict=False 时 geometry 只需写要改的项（其余沿用 BASELINE_GEOMETRY）；
    strict=True（--params 的 JSON）要求 11 个主尺寸齐全，避免拼写错误被默认值
    悄悄兜住。
    """
    raw = dict(geometry or {})
    if strict:
        missing = [key for key in REQUIRED_GEOMETRY_KEYS if key not in raw]
        if missing:
            raise ValueError(f"参数文件缺少几何字段: {', '.join(missing)}")
    merged = dict(BASELINE_GEOMETRY)
    merged.update(raw)
    normalized, notes = normalize_geometry(merged)
    for note in notes:
        print(f"[INFO] 算例 '{name}': {note}")
    case = dict(case or {})
    case.update({"name": name, "geometry": normalized})
    return case


def _check_unique_names(cases):
    """同名会写到同一个结果目录，直接报错。"""
    seen = set()
    for case in cases:
        if case["name"] in seen:
            raise ValueError(f"算例名重复: {case['name']}（结果会互相覆盖）")
        seen.add(case["name"])
