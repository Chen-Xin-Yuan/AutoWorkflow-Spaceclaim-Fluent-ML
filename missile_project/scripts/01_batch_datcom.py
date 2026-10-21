# -*- coding: utf-8 -*-
"""
README 参数 → Missile DATCOM 气动计算自动化脚本
================================================
输入：README「参数化方式与来流条件」里的 11 个主尺寸（单位 m）与来流条件
      （马赫数列表 + 攻角列表 + 飞行高度）。
输出：runs\\<算例名>\\ 下的 DATCOM 原始输出与解析后的气动系数 CSV（路径不变）。

默认算例（改本文件顶部配置区即可）：
    几何 = 11 个主尺寸基准值：L_N / L / D / L_W / C_RW / C_TW / B_W /
           L_F / C_RF / C_TF / B_F
    来流 = MACH_LIST × ALPHA_LIST 的全部组合（README 为 6 个马赫 × 5 个攻角
           = 30 个工况，一次 DATCOM 调用全部算完，输出 30 行系数）

用法：
    python 01_batch_datcom.py                       # 跑 CASES 里配置的算例
    python 01_batch_datcom.py --list                # 只看算例与工况组合
    python 01_batch_datcom.py --dry-run             # 只生成 for005.dat 不调用 datcom
    python 01_batch_datcom.py --params a.json b.json  # 每个 JSON 一组几何 → 一个算例
    python 01_batch_datcom.py --mach 2.0 2.2 --alpha 4.0 6.0 --alt 5000

几何参数（11 项，单位 m，字段名与 README / 建模程序一致）：
    L_N  鼻锥长度            L   轴向总长          D   机身直径
    L_W  前舵轴向位置（弹尖到舵根前缘）  C_RW 前舵根弦  C_TW 前舵梢弦  B_W 前舵展长
    L_F  后舵轴向位置                  C_RF 后舵根弦  C_TF 后舵梢弦  B_F 后舵展长

DATCOM 建模约定（改动前务必读）：
    1. DATCOM 输入用英寸（DIM IN），脚本内部一律用 m 计算，写卡片时换算。
    2. 弹体 = 圆锥头部（DATCOM 默认头型）+ 等直径圆柱段：
       LNOSE = L_N, DNOSE = D, LCENTR = L - L_N, DCENTR = D。
    3. 每个十字舵建模为 4 片舵（NPANEL = 4），PHIF 在 "+" 布局下为
       0/90/180/270，"x" 布局为 45/135/225/315。
    4. 翼面梯形：SSPAN 为距弹轴的回转半径站（根部 = D/2，梢部 = D/2 + 展长），
       CHORD 为对应站的弦长，XLE 为舵根前缘轴向位置；前缘后掠角由
       后缘对齐（LE_MODE）的几何关系反算：atan((C_root - C_tip) / 展长)。
    5. 参考量按导弹惯例取机身横截面与直径：SREF = π(D/2)²，LREF = D，
       力矩参考点 XCG 默认取 L/2（可用 XCG_M 覆盖）。
    6. STA 固定写 1.0：本机 DATCOM 03/11 在第二个舵面组 STA=2.0 时会
       直接终止且不输出任何结果（已实测），所以两个舵面组都写 1.0。
    7. 翼型剖面沿用 datcom_bin/for005.dat 历史模板的 HEX 参数
       （默认按弦长归一的薄翼剖面），一般无需改动。

运行产物（runs\\<算例名>\\，产物不入 git）：
    for005.dat             本次实际喂给 DATCOM 的输入（可复查 / 复现）
    for006.dat             DATCOM 原始输出
    aero_coefficients.csv  解析出的气动系数（MACH / ALPHA + 各系数列）
    case_params.json       本算例的几何、来流与派生量快照
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

import pandas as pd

# =============================================================================
# 1. 用户配置区
# =============================================================================
# 项目根目录由本脚本所在位置(scripts\)自动推导 = scripts 的上一级。
# 整个项目文件夹可整体移动 / 改名，无需修改任何路径配置。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# datcom.exe 与历史输入模板同目录（模板只读，脚本从不改写它）
DATCOM_EXE = os.path.join(PROJECT_ROOT, "datcom_bin", "datcom.exe")

# 结果根目录：每个算例保存到 runs\<算例名>\（runs 目录位于项目根，产物不入 git）
RESULT_ROOT = os.path.join(PROJECT_ROOT, "runs")

# 单算例超时（秒）。一次 30 个工况通常几秒到几十秒。
TIMEOUT_SECONDS = 300

# -----------------------------------------------------------------------------
# 1.1 几何参数（README「几何参数定义与范围」，单位 m）
# -----------------------------------------------------------------------------
# 基准值即 README 表中的基准尺寸。直接改这里的数字即可换几何；
# 也可以 --params 传 JSON 文件，或用下方 CASES 做多组几何批量对比。
MISSILE_GEOMETRY = {
    "L_N": 0.49,    # 鼻锥长度
    "L": 3.69,      # 轴向总长
    "D": 0.18,      # 机身直径
    "L_W": 1.72,    # 前十字舵轴向位置（弹尖 → 舵根可见前缘）
    "C_RW": 0.23,   # 前十字舵根弦长
    "C_TW": 0.06,   # 前十字舵梢弦长
    "B_W": 0.23,    # 前十字舵展长（从弹体表面向外量）
    "L_F": 3.20,    # 后十字舵轴向位置
    "C_RF": 0.38,   # 后十字舵根弦长
    "C_TF": 0.19,   # 后十字舵梢弦长
    "B_F": 0.22,    # 后十字舵展长（从弹体表面向外量）
}

# -----------------------------------------------------------------------------
# 1.2 来流条件（README「来流参数与工况组合」）
# -----------------------------------------------------------------------------
# 马赫数与攻角取全部组合：len(MACH_LIST) × len(ALPHA_LIST) 个工况。
MACH_LIST = [2.00, 2.04, 2.08, 2.12, 2.16, 2.20]
ALPHA_LIST = [4.0, 4.5, 5.0, 5.5, 6.0]

# 飞行高度（m）。DATCOM 的 ALT 卡片按英尺读，脚本自动换算；0.0 = 海平面。
ALTITUDE_M = 0.0

# -----------------------------------------------------------------------------
# 1.3 可选建模约定（一般不用改）
# -----------------------------------------------------------------------------
# 舵面梯形的前后缘对齐方式：trailing_aligned（后缘对齐）/ leading_aligned /
# symmetric。与建模程序的 le_mode 一致，默认后缘对齐。
LE_MODE = "trailing_aligned"

# 舵面滚转布局："+"（十字，舵面在水平/竖直平面）或 "x"（X 形，错开 45°）
FIN_PHASE = "+"

# 每道舵面的片数（十字舵 = 4）
CRUCIFORM_COUNT = 4

# 力矩参考点轴向位置（m）。None = 取 L/2（弹身中点）。
XCG_M = None

# 舵面剖面参数（DATCOM $FINSET 的 HEX 剖面，沿用历史模板；数值为 DATCOM 原生约定）
FIN_SECTION = {
    "SECTYP": "HEX",
    "LER": 0.015,      # 前缘半径
    "ZUPPER": 0.02238,  # 上表面最大厚度
    "LMAXU": 0.238,    # 最大厚度位置
    "LFLATU": 0.524,   # 上表面平顶结束位置
}

# -----------------------------------------------------------------------------
# 1.4 算例批量配置（每个元素 = 一个算例 = 一个 runs\<name>\ 目录）
# -----------------------------------------------------------------------------
# "geometry" 里只需写要改的项，其余沿用 MISSILE_GEOMETRY；
# 可选 "mach" / "alpha" / "alt" 覆盖全局来流条件。
# 示例（把下面注释打开即可做几何对比）：
#     {"name": "longer_nose", "geometry": {**MISSILE_GEOMETRY, "L_N": 0.60}},
#     {"name": "bigger_rear_fin", "geometry": {"B_F": 0.25, "C_TF": 0.22}},
CASES = [
    {
        "name": "readme_baseline",   # README 基准几何 + 全部 30 个来流工况
        "geometry": MISSILE_GEOMETRY,
    },
]

# =============================================================================
# 2. 常量与内部命名
# =============================================================================
M_TO_IN = 39.37007874015748      # 1 m = 39.3701 in
M_TO_FT = 3.280839895013123      # 1 m = 3.28084 ft（DATCOM 的 ALT 用英尺）

MIN_TIP_CHORD_M = 0.001          # 梢弦下限，与建模程序一致（0 会退化）

REQUIRED_GEOMETRY_KEYS = (
    "L_N", "L", "D",
    "L_W", "C_RW", "C_TW", "B_W",
    "L_F", "C_RF", "C_TF", "B_F",
)

FOR005_NAME = "for005.dat"        # DATCOM 输入文件名
INPUT_REDIRECT = "_input.txt"     # shell 重定向应答文件
FOR006_NAME = "for006.dat"        # DATCOM 输出（首选）
DATCOM_OUT_NAME = "datcom.out"    # DATCOM 输出（备用名）

CSV_NAME = "aero_coefficients.csv"
PARAMS_NAME = "case_params.json"

# namelist 数组变量 -> 计数器变量 的自动联动（仅用于 overrides 高级改写）
ARRAY_COUNTERS = {
    "MACH": "NMACH",
    "ALPHA": "NALPHA",
    "ALT": "NALT",
}

# CSV 列的输出顺序；DATCOM 额外给出的列会追加在后面
PREFERRED_COLUMNS = [
    "MACH", "ALPHA",
    "CN", "CM", "CA", "CY", "CLN", "CLL",
    "CL", "CD", "CL_CD", "XCP",
    "CNA", "CMA", "CYB", "CLNB", "CLLB",
    "CNQ", "CMQ", "CAQ", "CNAD", "CMAD",
    "CYR", "CLNR", "CLLR", "CYP", "CLNP", "CLLP",
]

# DATCOM 输出表头 -> CSV 列名（其余按 [_norm_column] 规范化）
COLUMN_ALIASES = {
    "X-C.P.": "XCP",
    "XC.P.": "XCP",
    "CL/CD": "CL_CD",
}


# =============================================================================
# 3. 几何参数校验与 DATCOM 卡片生成
# =============================================================================

def _num(value):
    """
    把 Python 数值格式化成 DATCOM 自由格式文本。

    两点要求来自实测：
    1. DATCOM 03/11 对 ``ALPHA = 4,4.5,5`` 这类不带小数点的整数会报语法错误，
       所以所有数值都必须带小数点（4 -> 4.0）。
    2. 本机 datcom.exe 对过长的数字串（例如 ``3.543307087``）会在内部几何
       计算里报 ``run-time error M6201: sqrt DOMAIN`` 且完全不输出结果；
       统一保留 5 位小数（英寸下 0.25 µm，远小于建模精度）可稳定复现结果。
    """
    number = float(value)
    if number != 0.0 and abs(number) < 1e-5:
        # 极小的非零量保留有效数字，避免被截成 0
        return f"{number:.10f}".rstrip("0")
    return f"{number:.5f}"


def _num_list(values):
    """把数值序列格式化成 ``a,b,c`` 的 DATCOM 数组写法。"""
    return ",".join(_num(v) for v in values)


def normalize_geometry(raw):
    """
    校验并规整 11 个主尺寸（单位 m）。

    Returns
    -------
    (geometry, notes) : geometry 为 float 字典；notes 为自动修正 / 提示文本列表。

    Raises
    ------
    ValueError
        缺参数、非数值或几何不自洽时，一次性列出全部问题。
    """
    errors = []
    geometry = {}
    for key in REQUIRED_GEOMETRY_KEYS:
        if key not in raw or raw[key] is None or raw[key] == "":
            errors.append(f"缺少几何参数 {key}。")
            continue
        try:
            geometry[key] = float(raw[key])
        except (TypeError, ValueError):
            errors.append(f"几何参数 {key} 必须是数值，收到 {raw[key]!r}。")
    if errors:
        raise ValueError("\n".join(errors))

    notes = []
    # 与建模程序一致：非负但小于 1 mm 的梢弦抬到 1 mm，否则梢边退化、DATCOM 会算错
    for key in ("C_TW", "C_TF"):
        if 0 <= geometry[key] < MIN_TIP_CHORD_M:
            notes.append(
                f"{key} = {geometry[key]:g} m 小于 {MIN_TIP_CHORD_M} m，"
                f"按建模程序约定抬到 {MIN_TIP_CHORD_M} m。"
            )
            geometry[key] = MIN_TIP_CHORD_M

    def need(condition, message):
        if not condition:
            errors.append(message)

    g = geometry
    need(g["L_N"] > 0, "鼻锥长度 L_N 必须大于 0。")
    need(g["L"] > 0, "轴向总长 L 必须大于 0。")
    need(g["D"] > 0, "机身直径 D 必须大于 0。")
    need(g["L_N"] < g["L"], f"鼻锥长度 L_N ({g['L_N']:g}) 必须小于总长 L ({g['L']:g})。")
    need(g["D"] < g["L"], f"机身直径 D ({g['D']:g}) 必须小于总长 L ({g['L']:g})。")
    for root, tip, span in (("C_RW", "C_TW", "B_W"), ("C_RF", "C_TF", "B_F")):
        need(g[root] > 0, f"{root} 必须大于 0。")
        need(g[span] > 0, f"{span} 必须大于 0。")
        need(g[tip] >= 0, f"{tip} 不能为负。")
    need(g["L_W"] >= g["L_N"], f"前舵轴向位置 L_W ({g['L_W']:g}) 必须不早于锥柱交接处 L_N ({g['L_N']:g})。")
    need(g["L_W"] + g["C_RW"] <= g["L"], "前舵超出弹尾：L_W + C_RW 必须不大于 L。")
    need(g["L_F"] >= g["L_N"], f"后舵轴向位置 L_F ({g['L_F']:g}) 必须不早于锥柱交接处 L_N ({g['L_N']:g})。")
    need(g["L_F"] + g["C_RF"] <= g["L"], "后舵超出弹尾：L_F + C_RF 必须不大于 L。")
    need(
        g["L_W"] + g["C_RW"] <= g["L_F"],
        f"前后舵重叠：L_W + C_RW ({g['L_W'] + g['C_RW']:g}) 必须不大于 L_F ({g['L_F']:g})。",
    )
    if errors:
        raise ValueError("\n".join(errors))
    return geometry, notes


def _fin_sweep_deg(chord_root, chord_tip, span, le_mode):
    """按前后缘对齐方式，由根/梢弦与展长反算舵面前缘后掠角（度，正值后掠）。"""
    if le_mode == "trailing_aligned":
        offset = chord_root - chord_tip
    elif le_mode == "leading_aligned":
        offset = 0.0
    elif le_mode == "symmetric":
        offset = (chord_root - chord_tip) / 2.0
    else:
        raise ValueError(
            f"不支持的 le_mode {le_mode!r}，仅支持 trailing_aligned / leading_aligned / symmetric。"
        )
    return math.degrees(math.atan2(offset, span))


def _fin_roll_angles(fin_phase, count):
    """十字舵/舵面组的滚转角列表：'+' 从 0° 起，'x' 从 45° 起，均分 360°。"""
    if not isinstance(count, int) or count < 2 or count % 2 != 0:
        raise ValueError(f"CRUCIFORM_COUNT 必须是大于等于 2 的偶数，收到 {count!r}。")
    if fin_phase == "+":
        start = 0.0
    elif fin_phase == "x":
        start = 45.0
    else:
        raise ValueError(f"不支持的 FIN_PHASE {fin_phase!r}，仅支持 '+' 或 'x'。")
    return [start + 360.0 * index / count for index in range(count)]


def build_for005(geometry, mach_list, alpha_list, altitude_m,
                 xcg_m=None, le_mode=LE_MODE, fin_phase=FIN_PHASE,
                 cruciform_count=CRUCIFORM_COUNT, fin_section=None):
    """
    按几何参数与来流条件生成完整的 for005.dat 文本。

    Parameters
    ----------
    geometry : dict
        11 个主尺寸（m），已通过 normalize_geometry。
    mach_list, alpha_list : list[float]
        马赫数列表（无量纲）与攻角列表（度），取全部组合。
    altitude_m : float
        飞行高度（m），DATCOM 的 ALT 卡片按英尺写入。
    xcg_m : float | None
        力矩参考点（m），None 取 L/2。

    Returns
    -------
    (text, meta) : text 为 for005.dat 全文；meta 记录本次实际使用的换算结果。
    """
    if not mach_list:
        raise ValueError("MACH_LIST 不能为空。")
    if not alpha_list:
        raise ValueError("ALPHA_LIST 不能为空。")

    section = {"SECTYP": "HEX", "LER": 0.015, "ZUPPER": 0.02238,
               "LMAXU": 0.238, "LFLATU": 0.524}
    section.update(fin_section or {})

    g = geometry
    radius = g["D"] / 2.0
    xcg = g["L"] / 2.0 if xcg_m is None else float(xcg_m)
    if not 0.0 <= xcg <= g["L"]:
        raise ValueError(f"力矩参考点 XCG ({xcg:g} m) 必须在 0 与 L ({g['L']:g} m) 之间。")

    sref_m2 = math.pi * radius ** 2
    lref_m = g["D"]
    alt_ft = altitude_m * M_TO_FT
    phif = _fin_roll_angles(fin_phase, cruciform_count)

    lines = []
    # ---- $FLTCON：马赫数 / 攻角 / 高度 ----
    lines += [
        "$FLTCON",
        f" NALPHA = {_num(len(alpha_list))},",
        f" ALPHA = {_num_list(alpha_list)},",
        f" NMACH = {_num(len(mach_list))},",
        f" MACH = {_num_list(mach_list)},",
        # 每个马赫数必须配一组大气条件，否则 DATCOM 不输出结果
        f" ALT = {_num_list([alt_ft] * len(mach_list))},",
        "$END",
    ]
    # ---- $REFQ：参考面积 / 参考长度 / 力矩参考点 ----
    # LREF 是 Missile DATCOM 的参考长度变量名（CBAR 会被判为未知变量）
    lines += [
        "$REFQ",
        f" SREF = {_num(sref_m2 * M_TO_IN ** 2)},",
        f" LREF = {_num(lref_m * M_TO_IN)},",
        f" XCG = {_num(xcg * M_TO_IN)},",
        "$END",
    ]
    # ---- $AXIBOD：圆锥头部 + 等直径圆柱段 ----
    lines += [
        "$AXIBOD",
        f" LNOSE = {_num(g['L_N'] * M_TO_IN)},",
        f" DNOSE = {_num(g['D'] * M_TO_IN)},",
        f" LCENTR = {_num((g['L'] - g['L_N']) * M_TO_IN)},",
        f" DCENTR = {_num(g['D'] * M_TO_IN)},",
        "$END",
    ]

    def fin_block(index, le_m, chord_root, chord_tip, span_m):
        """生成一个舵面组（前十字舵 / 后十字舵）的 $FINSET 块。"""
        sweep = _fin_sweep_deg(chord_root, chord_tip, span_m, le_mode)
        block = [
            f"$FINSET{index}",
            f" SECTYP = {section['SECTYP']},",
            f" NPANEL = {_num(cruciform_count)},",
            f" PHIF = {_num_list(phif)},",
            f" XLE = {_num(le_m * M_TO_IN)},",
            f" SSPAN = {_num(radius * M_TO_IN)},{_num((radius + span_m) * M_TO_IN)},",
            f" CHORD = {_num(chord_root * M_TO_IN)},{_num(chord_tip * M_TO_IN)},",
            f" SWEEP = {_num(sweep)},",
            # 本机 DATCOM 对第二组 STA=2.0 会直接终止且无输出，固定写 1.0
            " STA = 1.0,",
            f" LER = {_num_list([section['LER']] * cruciform_count)},",
            f" ZUPPER = {_num_list([section['ZUPPER']] * cruciform_count)},",
            f" LMAXU = {_num_list([section['LMAXU']] * cruciform_count)},",
            f" LFLATU = {_num_list([section['LFLATU']] * cruciform_count)},",
            "$END",
        ]
        return block, sweep

    front_block, front_sweep = fin_block(
        1, g["L_W"], g["C_RW"], g["C_TW"], g["B_W"])
    rear_block, rear_sweep = fin_block(
        2, g["L_F"], g["C_RF"], g["C_TF"], g["B_F"])
    lines += front_block + rear_block
    # ---- $EXPR / 控制卡 ----
    lines += [
        "$EXPR",
        " CONF = BODY,",
        "$END",
        "DERIV DEG",   # 导数按“每度”输出
        "DIM IN",      # 长度单位英寸
        "DAMP",        # 输出动导数
        "SAVE",
    ]
    text = "\n".join(lines) + "\n"

    meta = {
        "geometry_m": dict(g),
        "flow": {
            "mach": [float(m) for m in mach_list],
            "alpha_deg": [float(a) for a in alpha_list],
            "altitude_m": float(altitude_m),
            "altitude_ft": alt_ft,
        },
        "reference": {
            "sref_in2": sref_m2 * M_TO_IN ** 2,
            "sref_m2": sref_m2,
            "lref_in": lref_m * M_TO_IN,
            "lref_m": lref_m,
            "xcg_m": xcg,
        },
        "derived": {
            "fin_phase": fin_phase,
            "panels_per_fin_set": cruciform_count,
            "phif_deg": phif,
            "le_mode": le_mode,
            "body_radius_m": radius,
            "front_tip_radius_m": radius + g["B_W"],
            "rear_tip_radius_m": radius + g["B_F"],
            "front_sweep_deg": front_sweep,
            "rear_sweep_deg": rear_sweep,
            "front_te_m": g["L_W"] + g["C_RW"],
            "rear_te_m": g["L_F"] + g["C_RF"],
        },
    }
    return text, meta


# =============================================================================
# 4. 输入文件的高级改写（overrides，可选）
# =============================================================================

def _value_str(value):
    """把 Python 值格式化成 DATCOM namelist 自由格式文本。"""
    if isinstance(value, (list, tuple)):
        return ",".join(_value_str(v) for v in value)
    if isinstance(value, bool):
        return "T" if value else "F"
    if isinstance(value, str):
        return value          # 原样写入（如 'HEX'、'BODY'）
    return _num(value)


def _find_namelist_region(lines, block_name):
    """
    在行列表中定位 $<BLOCK> 区域。

    Returns
    -------
    (start, end) : $<BLOCK> 所在行索引 start，闭合行（$ 或 $END）索引 end；
                   区域内容为 lines[start+1 : end]。找不到时返回 (None, None)。
    """
    target = block_name.strip().upper().lstrip("$")
    start = None
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s.startswith("$"):
            i += 1
            continue
        token = s[1:].strip().upper()
        if start is None:
            if token == target:
                start = i
            i += 1
            continue
        if token in ("END", ""):
            return start, i
        if token != target:
            return start, i
        i += 1
    if start is not None:
        return start, len(lines)
    return None, None


def _replace_or_insert(lines, start, end, var, value_str):
    """在块区域 lines[start+1:end] 中替换变量行；不存在则插入到闭合行前。"""
    pat = re.compile(r"^(\s*)" + re.escape(var) + r"(\s*=).*$", re.IGNORECASE)
    for idx in range(start + 1, end):
        m = pat.match(lines[idx])
        if m:
            lines[idx] = f"{m.group(1)}{var} = {value_str},"
            return True
    lines.insert(end, f" {var} = {value_str},")
    return False


def _sync_counter(lines, start, end, var, value):
    """数组变量给了多值时，若块内有计数器变量（NMACH/NALPHA/NALT）则同步其值。"""
    if not isinstance(value, (list, tuple)):
        return
    cnt = ARRAY_COUNTERS.get(var.upper())
    if not cnt:
        return
    pat = re.compile(r"^(\s*)" + re.escape(cnt) + r"(\s*=).*$", re.IGNORECASE)
    for idx in range(start + 1, end):
        m = pat.match(lines[idx])
        if m:
            lines[idx] = f"{m.group(1)}{cnt} = {_num(len(value))},"
            return


def render_input(template_text, overrides):
    """
    基于输入文本，按 overrides 改写后返回新的 for005 文本。

    overrides: {"块名": {"变量": 值或值列表}, ...}，块名不带 $，变量名不区分大小写。
    用于覆盖 11 个主尺寸没有建模的 DATCOM 关键字（高级用法，一般留空）。
    """
    lines = template_text.splitlines()
    for block_name, var_map in overrides.items():
        start, end = _find_namelist_region(lines, block_name)
        if start is None:
            existing = sorted({re.sub(r'^\s*\$', '', ln).strip().upper()
                               for ln in lines
                               if ln.strip().startswith("$")
                               and not ln.strip().startswith("$END")})
            raise ValueError(
                f"模板中找不到 namelist 块 '${block_name.upper()}'。"
                f"模板现有的块: {existing}"
            )
        for var, value in var_map.items():
            _replace_or_insert(lines, start, end, var, _value_str(value))
            _sync_counter(lines, start, end, var, value)
    return "\n".join(lines) + "\n"


def check_fltcon_warnings(for005_text):
    """
    对 $FLTCON 做经验性一致性检查，提前暴露会导致 0 结果的配置错误。

    Returns
    -------
    list[str]
    """
    warns = []
    lines = for005_text.splitlines()
    start, end = _find_namelist_region(lines, "FLTCON")
    if start is None:
        return warns

    def _row(var):
        pat = re.compile(r"^(\s*)" + re.escape(var) + r"\s*=(.*)$", re.IGNORECASE)
        for idx in range(start + 1, end):
            mt = pat.match(lines[idx])
            if mt:
                return mt.group(2)
        return None

    def _n_values(raw):
        if raw is None:
            return None
        vals = [x for x in raw.rstrip().rstrip(",").strip().split(",") if x.strip()]
        return len(vals) or None

    def _scalar(raw):
        if raw is None:
            return None
        try:
            return float(raw.rstrip().rstrip(",").strip())
        except ValueError:
            return None

    n_mach = _n_values(_row("MACH"))
    n_alt = _n_values(_row("ALT"))
    n_alpha = _n_values(_row("ALPHA"))
    nmach_v = _scalar(_row("NMACH"))
    nalpha_v = _scalar(_row("NALPHA"))

    if n_mach and nmach_v is not None and int(nmach_v) != n_mach:
        warns.append(f"FLTCON: NMACH={nmach_v:g} 与 MACH 实际值个数 {n_mach} 不一致。")
    if n_alpha and nalpha_v is not None and int(nalpha_v) != n_alpha:
        warns.append(f"FLTCON: NALPHA={nalpha_v:g} 与 ALPHA 实际值个数 {n_alpha} 不一致。")
    if n_mach and n_mach > 1 and (n_alt is None or n_alt < n_mach):
        warns.append(
            f"FLTCON: MACH 给了 {n_mach} 个值，但 ALT 只有 {n_alt or 0} 个。"
            "Missile DATCOM 要求每个马赫数对应一组大气条件（ALT 数组需与 MACH 等长），"
            "否则报 'ATMOSPHERIC CONDITIONS NOT SPECIFIED NMACH TIMES' 且不输出结果。"
        )
    return warns


def extract_error_hints(result_text, limit=6):
    """从 DATCOM 原始输出中抓取错误/警告行，用于 0 结果时的调试提示。"""
    hints = []
    for ln in result_text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if re.search(r"CASE ERROR|ERROR \*\*|^\s*\* WARNING \*|CONERR|INPUT ERROR|FATAL",
                     ln, re.IGNORECASE):
            hints.append(s[:160])
        if len(hints) >= limit:
            break
    return hints


# =============================================================================
# 5. DATCOM 调用
# =============================================================================

def write_input_redirect(case_dir, input_name=FOR005_NAME):
    """
    在运行目录生成临时 _input.txt（内容 b"for005.dat\\r\\n"），
    用于 shell 重定向，模拟人工在控制台输入文件名后回车。
    """
    input_path = os.path.join(case_dir, INPUT_REDIRECT)
    with open(input_path, "wb") as f:
        f.write(f"{input_name}\r\n".encode("ascii"))
    return input_path


def run_datcom(exe_path, work_dir, timeout):
    """
    以 work_dir 为工作目录调用 datcom.exe（shell 文件重定向）。

    老式 Fortran 程序必须用重定向，不能 text=True 或直接写 stdin。

    Returns
    -------
    subprocess.CompletedProcess
    """
    input_path = os.path.join(work_dir, INPUT_REDIRECT)
    cmd = f'"{exe_path}" < "{input_path}"'

    print(f"[INFO] 执行命令: {cmd}")
    print(f"[INFO] 工作目录: {work_dir}")

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"DATCOM 运行超时（>{timeout} 秒）") from exc

    # 老式 Windows Fortran 程序常用 gbk 编码；errors='ignore' 防解码崩溃
    stdout_text = result.stdout.decode("gbk", errors="ignore")
    stderr_text = result.stderr.decode("gbk", errors="ignore")

    if stderr_text.strip():
        print(f"[WARN] datcom stderr: {stderr_text.strip()[:300]}")
    elif result.returncode != 0:
        print(f"[WARN] datcom 返回码 {result.returncode}（老版本程序常见，以输出文件为准）")
    return result


def read_result_file(work_dir):
    """
    读取 DATCOM 结果文件：优先 for006.dat，不存在则回退 datcom.out。

    Returns
    -------
    tuple (file_path, text) 或 (None, None)
    """
    for cand in (os.path.join(work_dir, FOR006_NAME),
                 os.path.join(work_dir, DATCOM_OUT_NAME)):
        if os.path.exists(cand) and os.path.getsize(cand) > 0:
            with open(cand, "r", encoding="utf-8", errors="ignore") as f:
                return cand, f.read()
    return None, None


# =============================================================================
# 6. 结果解析
# =============================================================================

_HEADER_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_\-\./]*$")
_MACH_LINE = re.compile(r"MACH\s*(?:NO)?\s*=\s*([0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)",
                        re.IGNORECASE)


def _is_number(text):
    try:
        float(text)
        return True
    except (TypeError, ValueError):
        return False


def _norm_column(name):
    """DATCOM 列名规范化：'X-C.P.' -> 'XCP'，'CL/CD' -> 'CL_CD'。"""
    key = name.strip().upper()
    if key in COLUMN_ALIASES:
        return COLUMN_ALIASES[key]
    return re.sub(r"[^A-Za-z0-9]+", "_", key).strip("_")


def _looks_like_header(stripped):
    """判断一行是否是系数表表头（以 ALPHA 开头、其余为字母型列名）。"""
    tokens = stripped.split()
    if len(tokens) < 2 or tokens[0].upper() != "ALPHA":
        return None
    if not all(_HEADER_TOKEN.match(t) for t in tokens[1:]):
        return None
    return tokens


def _parse_number(token):
    return float(token) if _is_number(token) else float("nan")


def parse_datcom_output(text):
    """
    解析 DATCOM 输出，把每页表里的系数按 (MACH, ALPHA) 合并成一张表。

    输出里同一马赫数会分多页给出：静系数（CN/CM/CA/CY/CLN/CLL）、
    气动系数（CL/CD/CL-CD/X-C.P.）、静导数（CNA/CMA/...）、动导数（CNQ/...）；
    每页表头第一列都是 ALPHA，按列名合并即可得到完整的一行气动数据。

    Returns
    -------
    pandas.DataFrame
    """
    if not text or not text.strip():
        return pd.DataFrame()

    lines = text.splitlines()
    records = {}          # (mach, alpha) -> {列名: 值}
    current_mach = None
    i = 0
    while i < len(lines):
        line = lines[i]
        mach_match = _MACH_LINE.search(line)
        if mach_match:
            current_mach = float(mach_match.group(1))

        header = _looks_like_header(line.strip())
        if header is None:
            i += 1
            continue

        columns = [_norm_column(t) for t in header]
        n_cols = len(columns)
        alpha_col = columns.index("ALPHA") if "ALPHA" in columns else 0

        j = i + 1
        while j < len(lines):
            row = lines[j].strip()
            if not row:
                j += 1
                continue
            tokens = row.split()
            # 数据行：列数与表头一致，且首列是带小数点的数值。
            # （"PANEL DEFLECTION ANGLES" 那种 "1  0.00 ..." 行首列是整数，会被排除）
            if len(tokens) != n_cols or "." not in tokens[0] or not _is_number(tokens[0]):
                break
            values = [_parse_number(t) for t in tokens]
            alpha = values[alpha_col]
            if current_mach is None or math.isnan(alpha):
                j += 1
                continue
            key = (round(current_mach, 4), round(alpha, 4))
            record = records.setdefault(key, {})
            for name, value in zip(columns, values):
                record[name] = value
            j += 1
        i = max(j, i + 1)

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame([
        {"MACH": mach, "ALPHA": alpha, **values}
        for (mach, alpha), values in records.items()
    ])
    df = df.sort_values(by=["MACH", "ALPHA"]).reset_index(drop=True)

    ordered = [c for c in PREFERRED_COLUMNS if c in df.columns]
    ordered += [c for c in df.columns if c not in ordered]
    return df[ordered]


# =============================================================================
# 7. 单算例执行
# =============================================================================

def clear_dir(path):
    """清空目录内容（保留目录本身）。"""
    if os.path.exists(path):
        for item in os.listdir(path):
            target = os.path.join(path, item)
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
            else:
                os.remove(target)
    else:
        os.makedirs(path, exist_ok=True)


def run_one_case(case, work_dir):
    """
    渲染一个算例输入 → 在 work_dir 运行 datcom → 收集产物并解析。

    Returns
    -------
    dict
        含 name / n_records / csv_path / out_dir 等信息；失败时抛出异常。
    """
    name = case["name"]
    geometry = case["geometry"]
    mach_list = case.get("mach", MACH_LIST)
    alpha_list = case.get("alpha", ALPHA_LIST)
    altitude_m = case.get("alt", ALTITUDE_M)

    # 1) 生成 for005 文本（11 个主尺寸 + 来流条件 → DATCOM 卡片）
    for005_text, meta = build_for005(
        geometry, mach_list, alpha_list, altitude_m,
        xcg_m=case.get("xcg", XCG_M),
        le_mode=case.get("le_mode", LE_MODE),
        fin_phase=case.get("fin_phase", FIN_PHASE),
        cruciform_count=case.get("cruciform_count", CRUCIFORM_COUNT),
        fin_section=case.get("fin_section", FIN_SECTION),
    )

    # 2) 可选的高级改写
    overrides = case.get("overrides") or {}
    if overrides:
        for005_text = render_input(for005_text, overrides)

    # 2.1) 改写后一致性检查（提前暴露会 0 结果的配置问题）
    for warn in check_fltcon_warnings(for005_text):
        print(f"[WARN] 算例 '{name}': {warn}")

    # 3) 落盘并运行（工作目录先清空，保证每次从干净状态开始）
    clear_dir(work_dir)
    for005_path = os.path.join(work_dir, FOR005_NAME)
    with open(for005_path, "w", encoding="utf-8") as f:
        f.write(for005_text)
    write_input_redirect(work_dir, input_name=FOR005_NAME)
    run_datcom(DATCOM_EXE, work_dir, case.get("timeout", TIMEOUT_SECONDS))

    # 4) 读取原始结果
    result_path, result_text = read_result_file(work_dir)
    if result_text is None:
        raise RuntimeError(
            f"算例 '{name}' 未生成结果文件（{FOR006_NAME}/{DATCOM_OUT_NAME}），"
            f"请检查工作目录 {work_dir} 中的 for005.dat"
        )

    # 5) 解析
    df = parse_datcom_output(result_text)
    expected = len(mach_list) * len(alpha_list)
    if df.empty:
        hints = extract_error_hints(result_text)
        if hints:
            print(f"[WARN] 算例 '{name}' 解析到 0 条记录。DATCOM 输出中的错误/警告线索:")
            for h in hints:
                print(f"       > {h}")
        else:
            print(f"[WARN] 算例 '{name}' 解析到 0 条记录，且输出中未见明显错误行，"
                  f"请人工检查 {work_dir} 目录中的原始输出格式。")
    elif len(df) < expected:
        print(f"[WARN] 算例 '{name}': 预期 {expected} 个工况，实际解析到 {len(df)} 行。")

    # 6) 拷贝产物到 runs\<name>\
    out_dir = os.path.join(RESULT_ROOT, name)
    os.makedirs(out_dir, exist_ok=True)
    shutil.copyfile(for005_path, os.path.join(out_dir, FOR005_NAME))
    shutil.copyfile(result_path, os.path.join(out_dir, os.path.basename(result_path)))

    csv_path = os.path.join(out_dir, CSV_NAME)
    if not df.empty:
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # 7) 参数快照：以后想复现某个算例，看这个文件即可
    params = {
        "case": name,
        "script": os.path.basename(__file__),
        "created": datetime.now().isoformat(timespec="seconds"),
        "mass_unit": "m",
        "datcom_input_unit": "in",
        **meta,
        "overrides": overrides,
    }
    with open(os.path.join(out_dir, PARAMS_NAME), "w", encoding="utf-8") as f:
        json.dump(params, f, ensure_ascii=False, indent=2)

    return {
        "name": name,
        "n_records": len(df),
        "expected": expected,
        "out_dir": out_dir,
        "csv_path": csv_path if os.path.exists(csv_path) else None,
        "raw_result": os.path.basename(result_path),
    }


# =============================================================================
# 8. 命令行与主流程
# =============================================================================

def _case_from_geometry(name, geometry, case=None, strict=False):
    """
    构造一个算例。

    strict=False 时 geometry 只需写要改的项（其余沿用 MISSILE_GEOMETRY）；
    strict=True（--params 的 JSON 参数文件）要求 11 个主尺寸齐全，避免拼写错误
    被默认值悄悄兜住。
    """
    raw = dict(geometry or {})
    if strict:
        missing = [key for key in REQUIRED_GEOMETRY_KEYS if key not in raw]
        if missing:
            raise ValueError(f"参数文件缺少几何字段: {', '.join(missing)}")
    merged = dict(MISSILE_GEOMETRY)
    merged.update(raw)
    normalized, notes = normalize_geometry(merged)
    for note in notes:
        print(f"[INFO] 算例 '{name}': {note}")
    case = dict(case or {})
    case.update({"name": name, "geometry": normalized})
    return case


def build_cases(args):
    """
    汇总算例列表：--params 给的 JSON 文件优先（每个文件一个算例），
    否则用本文件顶部 CASES，并叠加命令行给出的来流条件。
    """
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

    cases = []
    if args.params:
        if args.name and len(args.params) != 1:
            raise ValueError("--name 只能和单个 --params 文件一起使用。")
        for path in args.params:
            # utf-8-sig：兼容 Windows 记事本 / PowerShell 写出的带 BOM 的 JSON
            with open(path, "r", encoding="utf-8-sig") as f:
                raw = json.load(f)
            unknown = [k for k in raw if k not in REQUIRED_GEOMETRY_KEYS]
            if unknown:
                print(f"[INFO] {os.path.basename(path)}: 忽略非几何字段 {', '.join(unknown)}")
            stem = os.path.splitext(os.path.basename(path))[0]
            name = args.name or re.sub(r"[^0-9A-Za-z._-]+", "_", stem).strip("_") or "case"
            case = _case_from_geometry(name, raw, {**flow, "params_file": path},
                                       strict=True)
            cases.append(case)
    else:
        for entry in CASES:
            if not isinstance(entry, dict) or "name" not in entry:
                raise ValueError("CASES 的每个元素必须是含 'name' 的字典。")
            case = _case_from_geometry(entry["name"], entry.get("geometry", {}),
                                       {k: v for k, v in entry.items()
                                        if k not in ("name", "geometry")})
            case.update({k: v for k, v in flow.items() if k not in case})
            cases.append(case)

    # 算例名唯一的校验：同名会写到同一个结果目录
    seen = set()
    for case in cases:
        if case["name"] in seen:
            raise ValueError(f"算例名重复: {case['name']}（结果会互相覆盖）")
        seen.add(case["name"])
        case.setdefault("mach", MACH_LIST)
        case.setdefault("alpha", ALPHA_LIST)
        case.setdefault("alt", ALTITUDE_M)
        case.setdefault("timeout", args.timeout)
    return cases


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="按 README 的几何参数与来流条件批量调用 Missile DATCOM 求气动参数。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="结果写入 runs/<算例名>/：for005.dat、for006.dat、"
               "aero_coefficients.csv、case_params.json。",
    )
    parser.add_argument("--params", nargs="+", metavar="JSON",
                        help="几何参数 JSON 文件（11 个主尺寸，单位 m），每个文件一个算例，"
                             "替代顶部 CASES 配置。")
    parser.add_argument("--name", help="算例名（仅配合单个 --params 文件使用）。")
    parser.add_argument("--mach", nargs="+", type=float,
                        help=f"覆盖马赫数列表，例如 --mach 2.0 2.2（默认 {MACH_LIST}）。")
    parser.add_argument("--alpha", nargs="+", type=float,
                        help=f"覆盖攻角列表（度），例如 --alpha 4.0 5.0 6.0（默认 {ALPHA_LIST}）。")
    parser.add_argument("--alt", type=float, help=f"覆盖飞行高度（m，默认 {ALTITUDE_M}）。")
    parser.add_argument("--dry-run", action="store_true",
                        help="只生成 for005.dat 到 runs/<算例名>/，不调用 datcom。")
    parser.add_argument("--list", action="store_true", help="列出算例与工况组合后退出。")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_SECONDS,
                        help=f"单算例超时秒数（默认 {TIMEOUT_SECONDS}）。")
    return parser.parse_args(argv)


def describe_cases(cases):
    """打印算例清单与工况数量，便于运行前核对。"""
    for case in cases:
        mach = case["mach"]
        alpha = case["alpha"]
        print(f"  算例 {case['name']}: "
              f"{len(mach)} 个马赫 × {len(alpha)} 个攻角 = {len(mach) * len(alpha)} 个工况")
        print(f"    马赫数: {', '.join(f'{m:g}' for m in mach)}")
        print(f"    攻角  : {', '.join(f'{a:g}' for a in alpha)} 度")
        print(f"    高度  : {case['alt']:g} m")
        geometry = case["geometry"]
        print("    几何  : " + ", ".join(f"{k}={v:g}" for k, v in geometry.items()))


def main(argv=None):
    """批量执行算例，结果集中保存到 RESULT_ROOT。"""
    args = parse_args(argv)

    if not os.path.exists(DATCOM_EXE):
        print(f"[ERROR] 找不到 DATCOM 可执行文件: {DATCOM_EXE}")
        return 1

    try:
        cases = build_cases(args)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"[ERROR] 算例配置有误:\n{exc}")
        return 1

    if not cases:
        print("[WARN] 没有可运行的算例。")
        return 1

    print(f"[INFO] 项目根目录 : {PROJECT_ROOT}")
    print(f"[INFO] DATCOM     : {DATCOM_EXE}")
    print(f"[INFO] 结果根目录 : {RESULT_ROOT}")
    print(f"[INFO] 待运行算例 : {len(cases)} 个")
    describe_cases(cases)
    print("=" * 70)

    if args.list:
        return 0

    os.makedirs(RESULT_ROOT, exist_ok=True)
    # 每个算例共用的临时工作目录（运行中间文件都在这，结束后删除）
    work_dir = os.path.join(RESULT_ROOT, ".work")
    clear_dir(work_dir)

    if args.dry_run:
        for case in cases:
            text, _meta = build_for005(
                case["geometry"], case["mach"], case["alpha"], case["alt"],
                xcg_m=case.get("xcg", XCG_M),
                le_mode=case.get("le_mode", LE_MODE),
                fin_phase=case.get("fin_phase", FIN_PHASE),
                cruciform_count=case.get("cruciform_count", CRUCIFORM_COUNT),
                fin_section=case.get("fin_section", FIN_SECTION),
            )
            out_dir = os.path.join(RESULT_ROOT, case["name"])
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, FOR005_NAME), "w", encoding="utf-8") as f:
                f.write(text)
            print(f"[DRY-RUN] 已生成 {os.path.join(out_dir, FOR005_NAME)}")
        return 0

    summary = []
    for idx, case in enumerate(cases, 1):
        name = case["name"]
        print(f"\n{'─' * 70}")
        print(f"[{idx}/{len(cases)}] 算例: {name}")
        print(f"{'─' * 70}")
        try:
            info = run_one_case(case, work_dir)
            print(f"[OK] {name}: {info['n_records']} 条气动系数记录 → {info['out_dir']}")
            summary.append((name, info["n_records"], info["expected"], info["raw_result"]))
        except Exception as exc:  # noqa: BLE001 —— 单算例失败不中断批量
            print(f"[ERROR] 算例 '{name}' 运行失败: {exc}")
            summary.append((name, None, len(case["mach"]) * len(case["alpha"]), "FAILED"))

    # 清理临时工作目录
    shutil.rmtree(work_dir, ignore_errors=True)

    print("\n" + "=" * 70)
    print("批量运行汇总:")
    print("=" * 70)
    failed = 0
    for name, n_records, expected, raw in summary:
        if n_records is None:
            failed += 1
            print(f"  [FAIL] {name:<24s} {'-':>6s} 条  原始输出: {raw}")
            continue
        ok = n_records == expected
        failed += 0 if ok else 1
        state = "OK  " if ok else "WARN"
        print(f"  [{state}] {name:<24s} {n_records:>6d} 条（预期 {expected}）  原始输出: {raw}")
    print(f"\n[INFO] 结果目录: {RESULT_ROOT}")
    print("[INFO] 每个算例一个子目录：for005.dat（实际输入）+ for006.dat（原始输出）"
          f" + {CSV_NAME} + {PARAMS_NAME}")
    return 0 if failed == 0 else 1


# =============================================================================
# 直接运行入口
# =============================================================================
if __name__ == "__main__":
    sys.exit(main())
