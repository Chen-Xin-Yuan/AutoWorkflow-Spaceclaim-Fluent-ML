# -*- coding: utf-8 -*-
"""
README 参数 → Missile DATCOM 气动计算自动化脚本
================================================
输入：data_lib/inputs/cases.csv（输入总表）。同一个 geometry_id、同一个
      altitude_m 的各行合成一次 DATCOM 调用；马赫数和攻角只取表里写出的组合。
      文件顶部的 MISSILE_GEOMETRY、MACH_LIST、ALPHA_LIST、CASES，以及
      --params / --mach / --alpha / --alt，只在 --demo 时使用，用来演示单组基准几何。
输出：data_lib/runs/low/<geometry_id>/。datcom.exe 运行时的工作目录是
      data_lib/runs/low/.work，里面仍用 for005.dat、for006.dat、for022.dat。
      算例写完后，这些文件改名为 <geometry_id>_for005.dat 等。气动系数只写一张
      总表 <geometry_id>_aero_coefficients.csv：同一个 geometry_id 的所有工况
      （含不同高度）都在这一张表里，不再按工况拆成单行 CSV。

用法：
    python 01_batch_datcom.py                       # 读取输入总表并计算
    python 01_batch_datcom.py --list                # 只看总表里的算例与工况
    python 01_batch_datcom.py --dry-run             # 只生成 for005.dat 不调用 datcom
    python 01_batch_datcom.py --demo                 # 用顶部演示参数，不读总表
    python 01_batch_datcom.py --demo --mach 2.0 2.2 --alpha 4.0 6.0 --alt 5000

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
       CHORD 为对应站的弦长，XLE 为舵根前缘轴向位置。
    5. SWEEP 卡片**以后缘为基准**（实测，用 for022.dat 反推）：梢部后缘 =
       根部后缘 + 展长 × tan(SWEEP)，梢弦长度不变。所以后缘对齐
       （LE_MODE = trailing_aligned）时 SWEEP 写 0；前缘对齐 / 对称写负角度，
       由 _fin_sweep_card_deg 自动换算。
    6. DATCOM 会写出 Tecplot ASCII 的几何外形文件 for022.dat（单位英尺，含
       FUSELAGE 与每个舵面 4 片的 FINSET 区）。脚本把它拷进算例目录，并用它
       反查建模是否正确 —— 这比只看输入卡片更硬。
    7. 参考量按导弹惯例取机身横截面与直径：SREF = π(D/2)²，LREF = D，
       力矩参考点 XCG 默认取 L/2（可用 XCG_M 覆盖）。
    8. STA 固定写 1.0：本机 DATCOM 03/11 在第二个舵面组 STA=2.0 时会
       直接终止且不输出任何结果（已实测），所以两个舵面组都写 1.0。
    9. 舵面翼型是对称截面，类型和三个参数定义在 scripts/fin_section.py：
       默认六边形（SECTYP = HEX，LER/ZUPPER/LMAXU/LFLATU 四个数组直接写进
       $FINSET，文件末尾不再有 NACA 卡）；也可以切成 NACA 四位数（SECTYP =
       NACA，截面写在末尾的 NACA-1-4-... 控制卡上）。两套舵面共用同一截面，
       点云脚本导入同一份参数。

运行产物（`data_lib/runs/low/<geometry_id>/`）逐个文件列在 README「一、核心工作目录」的
表里，这里不再重复维护第二份清单；本脚本只负责生成卡片、调用 datcom.exe、解析系数、
写系数表与参数快照，几何核对产物由 scripts/report.py 生成。
同一 geometry_id 的另一次高度会覆盖按几何命名的归档，系数总表里其它高度的行会保留。
输出哪几个系数由顶部 OUTPUT_COEFFICIENTS 决定（默认只要 CL / CD / CM）。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

import pandas as pd

from datcom_spec import (
    CSV_NAME, DATCOM_LOG_NAME, FIN_DISPLAY_NAME, FOR005_NAME, FOR006_NAME,
    FOR022_NAME, GEOMETRY_CN_NAMES, INPUT_REDIRECT, M_TO_FT, M_TO_IN,
    PARAMS_NAME, REQUIRED_GEOMETRY_KEYS, WORK_SUBDIR, archived_filename,
)
from fin_section import FIN_SECTION as DEFAULT_FIN_SECTION, resolve_fin_section
from report import (
    append_coefficient_check, append_tecplot_geometry_check, check_tecplot_geometry,
    parse_tecplot_geometry, print_geometry_brief, write_geometry_outputs,
)

# =============================================================================
# 1. 用户配置区
# =============================================================================
# 项目根目录由本脚本所在位置(scripts\)自动推导 = scripts 的上一级。
# 整个项目文件夹可整体移动 / 改名，无需修改任何路径配置。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# datcom.exe 与历史输入模板同目录（模板只读，脚本从不改写它）
DATCOM_EXE = os.path.join(PROJECT_ROOT, "datcom_bin", "datcom.exe")

# 结果根目录：每个几何保存到 <结果根>\<geometry_id>\。
# datcom.exe 运行当下的协议文件放在 <结果根>\.work\，算完后不留在算例目录。
# 默认 = 工作区 data_lib/runs/low。换位置只改这一行。
WORKSPACE_ROOT = os.path.dirname(PROJECT_ROOT)
RESULT_ROOT = os.path.join(WORKSPACE_ROOT, "data_lib", "runs", "low")

# 输入总表：正式运行只读这一张表。路径相对工作区根。
# 同一个 geometry_id 加同一个 altitude_m 单元格合成一次调用。
# 输出：RESULT_ROOT/<geometry_id>/；中间文件：RESULT_ROOT/WORK_SUBDIR/。
# 下方 MISSILE_GEOMETRY / MACH_LIST / ALPHA_LIST / CASES 与 --params / --mach /
# --alpha / --alt 都是演示入口，只有加上 --demo 才会使用。
INPUT_TABLE = os.path.join(WORKSPACE_ROOT, "data_lib", "inputs", "cases.csv")
MASTER_TABLE_COLUMNS = (
    "geometry_id", "condition_id",
    "L_N", "L", "D", "L_W", "C_RW", "C_TW", "B_W",
    "L_F", "C_RF", "C_TF", "B_F",
    "Ma", "alpha_deg", "altitude_m",
)


# 单算例超时（秒）。一次 30 个工况通常几秒到几十秒。
TIMEOUT_SECONDS = 300

# -----------------------------------------------------------------------------
# 1.1 几何参数（README「几何参数定义与范围」，单位 m）
# -----------------------------------------------------------------------------
# 演示用基准几何。正式计算不读这里，只在 --demo 时使用。
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
# 演示用来流。正式计算的马赫数和攻角来自输入总表，不读这两份列表。
MACH_LIST = [0.80, 0.84, 0.88, 0.92, 0.96, 1.00]
ALPHA_LIST = [4.0, 4.5, 5.0, 5.5, 6.0]

# 飞行高度（m）。DATCOM 的 ALT 卡片按英尺读，脚本自动换算；
# 5000 m 会同时改变大气密度 / 音速与雷诺数，进而影响各系数。
# 0.0 = 海平面。
ALTITUDE_M = 000.0

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

# 舵面翼型。类型与三个默认值在 scripts/fin_section.py 顶部（SECTION_TYPE 选
# "HEX" 或 "NACA"），点云脚本使用同一份。演示算例可在 CASES 里用 "fin_section"
# 字典覆盖其中几项，键名与所选截面类型的字段相同。
FIN_SECTION = DEFAULT_FIN_SECTION

# -----------------------------------------------------------------------------
# 1.4 算例批量配置（每个元素 = 一个算例 = 结果根下的一个 <name>\ 目录）
# -----------------------------------------------------------------------------
# 演示算例，仅 --demo 使用。正式运行按输入总表的 geometry_id 分组。
# "geometry" 里只需写要改的项，其余沿用 MISSILE_GEOMETRY；
# 可选 "mach" / "alpha" / "alt" 覆盖演示来流。
# 示例（把下面注释打开即可做几何对比）：
#     {"name": "longer_nose", "geometry": {**MISSILE_GEOMETRY, "L_N": 0.60}},
#     {"name": "bigger_rear_fin", "geometry": {"B_F": 0.25, "C_TF": 0.22}},
CASES = [
    {
        "name": "readme_baseline",   # README 基准几何 + 全部 30 个来流工况
        "geometry": MISSILE_GEOMETRY,
    },
]

# -----------------------------------------------------------------------------
# 1.5 输出内容配置（CSV 表头用中文，方便直接看懂）
# -----------------------------------------------------------------------------
# 需要输出到 aero_coefficients.csv 的气动系数：(DATCOM 输出列名, CSV 中文表头)。
# 默认只要升力 / 阻力 / 俯仰力矩；想要更多系数在这里加一行即可（列名见 for006.dat
# 表头），例如 ("CNA", "升力系数斜率CNA")、("XCP", "压心位置XCP")。
# 注：滚转力矩 CLL、偏航力矩 CLN 在无侧滑的对称来流下恒为 0，故默认不输出。
OUTPUT_COEFFICIENTS = (
    ("CL", "升力系数CL"),
    ("CD", "阻力系数CD"),
    ("CM", "俯仰力矩系数CM"),
)

# 来流输入参数在 CSV 里的表头
CSV_FLOW_NAMES = {
    "mach": "马赫数",
    "alpha": "攻角(度)",
    "alt": "飞行高度(m)",
}

# =============================================================================
# 2. 常量与内部命名
# =============================================================================

MIN_TIP_CHORD_M = 0.001          # 梢弦下限，与建模程序一致（0 会退化）


# 舵面翼型也进系数表：翼型由 scripts/fin_section.py 的三个参数（截面类型可选）
# 决定，同一个 geometry_id 的所有工况共用一副截面。表里带上这几列，才能直接
# 看出这批系数对应哪种截面，不必再去翻 case_params.json。
# 列名随截面类型变化，由截面类的 CSV_COLUMNS 给出（见 build_for005 写进 meta 的
# fin_section_columns），这里不再维护第二份表头。


# 解析结果的内部列顺序（CSV 只输出 OUTPUT_COEFFICIENTS 里列出的系数）
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


def _fin_le_offset(chord_root, chord_tip, le_mode):
    """按前后缘对齐方式，返回梢弦前缘相对根弦前缘的轴向偏移（m）。"""
    if le_mode == "trailing_aligned":
        return chord_root - chord_tip
    if le_mode == "leading_aligned":
        return 0.0
    if le_mode == "symmetric":
        return (chord_root - chord_tip) / 2.0
    raise ValueError(
        f"不支持的 le_mode {le_mode!r}，仅支持 trailing_aligned / leading_aligned / symmetric。"
    )


def _fin_le_sweep_deg(chord_root, chord_tip, span, le_mode):
    """舵面真实前缘后掠角（度，正值后掠）—— 报告与示意图用。"""
    offset = _fin_le_offset(chord_root, chord_tip, le_mode)
    return math.degrees(math.atan2(offset, span))


def _fin_sweep_card_deg(chord_root, chord_tip, span, le_mode):
    """
    计算 $FINSET 的 SWEEP 卡片值（度）。

    实测约定（本机 DATCOM REV 03/11，用 for022.dat 的 Tecplot 几何输出反推）：
    DATCOM 的 SWEEP 是**以后缘为基准**的旋转量 ——
        梢部后缘 = 根部后缘 + 展长 × tan(SWEEP)，梢弦长度不变，
    也就是说 SWEEP = 0 得到的是"后缘对齐"的梯形舵面，而不是"前缘对齐"。

    设 le_mode 要求的梢前缘相对根前缘轴向偏移为 offset，DATCOM 实际画出来的偏移是
        (C_root - C_tip) + 展长 × tan(SWEEP)，
    因此
        tan(SWEEP) = (offset - (C_root - C_tip)) / 展长。

    实测数据（C_root = 0.23 m, C_tip = 0.06 m, 展长 = 0.23 m, 根部后缘 1.95 m）：
        SWEEP = 0      -> 梢部 X = 1.89 ~ 1.95 m（后缘对齐，符合预期）
        SWEEP = 36.47  -> 梢部 X = 2.06 ~ 2.12 m（多扫了一段）
        SWEEP = 45     -> 梢部 X = 2.12 ~ 2.18 m
        SWEEP = 60     -> 梢部 X = 2.29 ~ 2.35 m
    """
    base_offset = chord_root - chord_tip          # SWEEP = 0 时的梢前缘偏移
    desired = _fin_le_offset(chord_root, chord_tip, le_mode)
    return math.degrees(math.atan2(desired - base_offset, span))


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

    section = resolve_fin_section(FIN_SECTION if fin_section is None else fin_section)

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
        """
        生成一个舵面组（前十字舵 / 后十字舵）的 $FINSET 块。

        同时返回该组的派生几何 detail，供几何核对报告 / 三视图使用
        （全部为 m / 度，与输入口径一致）。
        """
        sweep = _fin_sweep_card_deg(chord_root, chord_tip, span_m, le_mode)
        le_sweep = _fin_le_sweep_deg(chord_root, chord_tip, span_m, le_mode)
        tip_le = le_m + _fin_le_offset(chord_root, chord_tip, le_mode)
        block = [
            f"$FINSET{index}",
            section.sectyp_line(),
            f" NPANEL = {_num(cruciform_count)},",
            f" PHIF = {_num_list(phif)},",
            f" XLE = {_num(le_m * M_TO_IN)},",
            f" SSPAN = {_num(radius * M_TO_IN)},{_num((radius + span_m) * M_TO_IN)},",
            f" CHORD = {_num(chord_root * M_TO_IN)},{_num(chord_tip * M_TO_IN)},",
            f" SWEEP = {_num(sweep)},",
            # 本机 DATCOM 对第二组 STA=2.0 会直接终止且无输出，固定写 1.0
            " STA = 1.0,",
        ]
        # 六边形截面的四个参数数组写在这里；NACA 截面返回空，截面走文件末尾的控制卡。
        for name, values in section.finset_extra_items():
            block.append(f" {name} = {_num_list(values)},")
        block.append("$END")
        detail = {
            "name": FIN_DISPLAY_NAME.get(index, f"舵面{index}"),
            "set_index": index,
            "xle_m": le_m,
            "root_le_m": le_m,
            "root_te_m": le_m + chord_root,
            "tip_le_m": tip_le,
            "tip_te_m": tip_le + chord_tip,
            "root_chord_m": chord_root,
            "tip_chord_m": chord_tip,
            "span_m": span_m,
            "sweep_deg": le_sweep,           # 真实前缘后掠角（几何量）
            "sweep_card_deg": sweep,         # 写进 SWEEP 卡片的值（后缘为基准）
            "root_radius_m": radius,
            "tip_radius_m": radius + span_m,
            # 单片外露平面面积（梯形），四片合计算 4 倍
            "panel_area_m2": (chord_root + chord_tip) / 2.0 * span_m,
        }
        return block, detail

    front_block, front_fin = fin_block(
        1, g["L_W"], g["C_RW"], g["C_TW"], g["B_W"])
    rear_block, rear_fin = fin_block(
        2, g["L_F"], g["C_RF"], g["C_TF"], g["B_F"])
    front_fin.update({"pos_key": "L_W", "root_key": "C_RW",
                      "tip_key": "C_TW", "span_key": "B_W"})
    rear_fin.update({"pos_key": "L_F", "root_key": "C_RF",
                     "tip_key": "C_TF", "span_key": "B_F"})
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
    # NACA 截面的翼型控制卡放在所有控制卡之后；六边形截面的参数已经写进 $FINSET，
    # 这一步返回空列表。
    lines += section.control_cards()
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
            "fin_section": section.as_dict(),
            # 供核对报告 / 控制台摘要直接引用的一句中文说明。
            "fin_section_text": section.describe(),
            # 翼型列随截面类型变化（六边形是平顶长度+LER，NACA 是指数+厚度位置），
            # 由截面自己给出列名，CSV 表头才不会张冠李戴。
            "fin_section_columns": [list(item) for item in section.CSV_COLUMNS],
            "fins": [front_fin, rear_fin],
        },
    }
    return text, meta


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
# 4. DATCOM 调用
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
    return result, stdout_text, stderr_text


def read_result_file(work_dir):
    """
    读取 DATCOM 结果文件 for006.dat。

    Returns
    -------
    tuple (file_path, text) 或 (None, None)
    """
    path = os.path.join(work_dir, FOR006_NAME)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return path, f.read()
    return None, None


# =============================================================================
# 5. 结果解析
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
# 6. 产物输出（系数表）
# =============================================================================

def build_output_table(df, meta):
    """
    把解析结果整理成最终输出的 CSV 表（一行一个工况）。

    列 = 来流输入（马赫数 / 攻角 / 高度）+ 11 个几何输入 + 舵面翼型（截面编号
    与三个参数）+ OUTPUT_COEFFICIENTS 里的气动系数。输入参数一律用中文表头，
    便于直接对照 README 参数；翼型几列让每行都能看出算的是哪种舵面截面。

    Returns
    -------
    pandas.DataFrame
    """
    geometry = meta["geometry_m"]
    altitude_m = meta["flow"]["altitude_m"]
    # 翼型参数随几何一起记在 derived 里（build_for005 写入截面对象的 as_dict()），
    # 列名也由截面类型给出：六边形是「平顶长度 + LER」，NACA 是「指数 + 厚度位置」。
    derived = meta.get("derived") or {}
    section = derived.get("fin_section") or {}
    section_columns = [tuple(item) for item in derived.get("fin_section_columns") or ()]

    rows = []
    for _, row in df.iterrows():
        record = {
            CSV_FLOW_NAMES["mach"]: row.get("MACH"),
            CSV_FLOW_NAMES["alpha"]: row.get("ALPHA"),
            CSV_FLOW_NAMES["alt"]: altitude_m,
        }
        for key in REQUIRED_GEOMETRY_KEYS:
            record[GEOMETRY_CN_NAMES[key]] = geometry[key]
        for key, cn_name in section_columns:
            record[cn_name] = _fin_section_cell(section, key)
        for datcom_name, cn_name in OUTPUT_COEFFICIENTS:
            record[cn_name] = row.get(datcom_name, float("nan"))
        rows.append(record)

    columns = [CSV_FLOW_NAMES[key] for key in ("mach", "alpha", "alt")]
    columns += [GEOMETRY_CN_NAMES[key] for key in REQUIRED_GEOMETRY_KEYS]
    columns += [cn_name for _key, cn_name in section_columns]
    columns += [cn_name for _datcom_name, cn_name in OUTPUT_COEFFICIENTS]
    return pd.DataFrame(rows, columns=columns)


def _fin_section_cell(section, key):
    """翼型列的值。截面编号本身就带类型前缀（NACA ... / HEX ...），其余原样给出。"""
    value = section.get(key)
    if value is None:
        return float("nan")
    return value


def _flow_matches(row, mach, alpha):
    """DATCOM 解析值与总表浮点按四位小数对齐。"""
    try:
        got_mach = float(row[CSV_FLOW_NAMES["mach"]])
        got_alpha = float(row[CSV_FLOW_NAMES["alpha"]])
    except (TypeError, ValueError):
        return False
    return abs(got_mach - mach) <= 5e-4 and abs(got_alpha - alpha) <= 5e-4


def attach_case_identity(table, case):
    """给系数表加上能回到总表的三列。演示算例没有工况清单时原样返回。"""
    conditions = case.get("conditions")
    if not conditions or table.empty:
        return table
    geometry_ids = []
    condition_ids = []
    altitude_texts = []
    for _, row in table.iterrows():
        match = next((item for item in conditions if _flow_matches(row, item["mach"], item["alpha"])), None)
        geometry_ids.append(case["name"] if match else "")
        condition_ids.append(match["condition_id"] if match else "")
        altitude_texts.append(match["altitude_text"] if match else "")
    tagged = table.copy()
    tagged.insert(0, "altitude_m", altitude_texts)
    tagged.insert(0, "condition_id", condition_ids)
    tagged.insert(0, "geometry_id", geometry_ids)
    return tagged


def write_summary_csv(path, table):
    """写入系数总表。同一几何再次运行时，只替换本次工况，保留其它高度的行。

    保留的旧行如果缺这次新增的列（例如后来才加的翼型四列），这些单元格留空，
    不去猜旧行当时用的是哪副翼型。
    """
    identity = ["geometry_id", "condition_id", "altitude_m"]
    if os.path.isfile(path) and set(identity) <= set(table.columns):
        old = pd.read_csv(path, encoding="utf-8-sig")
        if set(identity) <= set(old.columns):
            fresh = {
                (str(row.geometry_id), str(row.condition_id), str(row.altitude_m))
                for row in table.itertuples(index=False)
            }
            keep = [
                index for index, row in old.iterrows()
                if (str(row["geometry_id"]), str(row["condition_id"]), str(row["altitude_m"])) not in fresh
            ]
            merged = pd.concat([old.iloc[keep], table], ignore_index=True, sort=False)
            # 以本次的列顺序为准；旧表里多出来的列（如果换过 OUTPUT_COEFFICIENTS）放最后。
            order = list(table.columns) + [c for c in merged.columns if c not in table.columns]
            table = merged.reindex(columns=order)
    table.to_csv(path, index=False, encoding="utf-8-sig", float_format="%.6g")


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

    # 2) 几何核对产物先落盘：即使后面 DATCOM 跑失败，也能据此确认几何建对没有
    out_dir = os.path.join(RESULT_ROOT, name)
    os.makedirs(out_dir, exist_ok=True)
    print_geometry_brief(meta)
    geom_txt, geom_png, checks = write_geometry_outputs(
        out_dir, name, meta, for005_text, baseline=MISSILE_GEOMETRY)
    bad = [msg for ok, msg in checks if not ok]
    print(f"    几何自检: {len(checks) - len(bad)}/{len(checks)} 项通过 → "
          f"{os.path.basename(geom_txt)}" + (f" + {os.path.basename(geom_png)}" if geom_png else ""))
    for msg in bad:
        print(f"    [NG] {msg}")

    # 3) 落盘并运行（工作目录先清空，保证每次从干净状态开始）
    clear_dir(work_dir)
    for005_path = os.path.join(work_dir, FOR005_NAME)
    with open(for005_path, "w", encoding="utf-8") as f:
        f.write(for005_text)
    write_input_redirect(work_dir, input_name=FOR005_NAME)
    _completed, stdout_text, stderr_text = run_datcom(
        DATCOM_EXE, work_dir, case.get("timeout", TIMEOUT_SECONDS))
    log_path = os.path.join(out_dir, archived_filename(name, DATCOM_LOG_NAME))
    with open(log_path, "w", encoding="utf-8") as log_file:
        log_file.write(stdout_text)
        if stderr_text:
            if stdout_text and not stdout_text.endswith("\n"):
                log_file.write("\n")
            log_file.write(stderr_text)

    # 4) 读取原始结果
    result_path, result_text = read_result_file(work_dir)
    if result_text is None:
        raise RuntimeError(
            f"算例 '{name}' 未生成结果文件 {FOR006_NAME}，"
            f"请检查工作目录 {work_dir} 中的 for005.dat"
        )

    # 4.1) DATCOM 原生几何外形（Tecplot ASCII）：拷进算例目录，并反过来核对建模
    tecplot_path = None
    tecplot_src = os.path.join(work_dir, FOR022_NAME)
    if os.path.exists(tecplot_src):
        tecplot_path = os.path.join(out_dir, archived_filename(name, FOR022_NAME))
        shutil.copyfile(tecplot_src, tecplot_path)
        try:
            with open(tecplot_src, "r", encoding="utf-8", errors="replace") as f:
                zones = parse_tecplot_geometry(f.read())
        except OSError as exc:
            print(f"[WARN] 算例 '{name}': 读取 {FOR022_NAME} 失败（{exc}）")
            zones = []
        geo_checks, geo_rows = check_tecplot_geometry(meta, zones)
        print(f"    {append_tecplot_geometry_check(geom_txt, geo_checks, geo_rows)}"
              f" → {os.path.basename(tecplot_path)}（可拖进 Tecplot 360）")
        for ok, msg in geo_checks:
            if not ok:
                print(f"    [NG] {msg}")
    else:
        print(f"[WARN] 算例 '{name}': DATCOM 没有写出 {FOR022_NAME}（Tecplot 几何外形）")

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

    # 6) 拷贝原始产物。算例目录用带几何标识的归档名，.work 里仍是协议原名。
    archived_input = archived_filename(name, FOR005_NAME)
    archived_raw = archived_filename(name, os.path.basename(result_path))
    shutil.copyfile(for005_path, os.path.join(out_dir, archived_input))
    shutil.copyfile(result_path, os.path.join(out_dir, archived_raw))

    # 6.1) 系数表：输入参数（中文表头）+ 只要的三个系数。整个几何只写这一张表。
    csv_path = os.path.join(out_dir, archived_filename(name, CSV_NAME))
    csv_written = False
    if not df.empty:
        table = attach_case_identity(build_output_table(df, meta), case)
        try:
            write_summary_csv(csv_path, table)
            csv_written = True
        except OSError as exc:
            # 常见原因：CSV 正被 Excel 等程序打开，Windows 上会拒绝写入
            print(f"[WARN] 算例 '{name}': 写 {os.path.basename(csv_path)} 失败（{exc}）。"
                  f"若该文件正被 Excel 打开，关闭后重跑本脚本即可。")
        missing = [cn for datcom_name, cn in OUTPUT_COEFFICIENTS
                   if datcom_name not in df.columns]
        if missing:
            print(f"[WARN] 算例 '{name}': DATCOM 输出里没有这些系数，CSV 对应列留空: "
                  + "、".join(missing))
        status = append_coefficient_check(geom_txt, df)
        if status:
            print(f"    {status}")

    # 7) 参数快照：以后想复现某个算例，看这个文件即可
    params = {
        "case": name,
        "script": os.path.basename(__file__),
        "created": datetime.now().isoformat(timespec="seconds"),
        "mass_unit": "m",
        "datcom_input_unit": "in",
        **meta,
    }
    with open(os.path.join(out_dir, archived_filename(name, PARAMS_NAME)), "w", encoding="utf-8") as f:
        json.dump(params, f, ensure_ascii=False, indent=2)

    return {
        "name": name,
        "n_records": len(df),
        "expected": expected,
        "out_dir": out_dir,
        "csv_path": csv_path if csv_written else None,
        "geometry_txt": geom_txt,
        "geometry_png": geom_png,
        "geometry_tecplot": tecplot_path,
        "checks_failed": len(bad),
        "raw_result": os.path.basename(result_path),
    }


# =============================================================================
# 8. 命令行与主流程
# =============================================================================

def _parse_table_float(text, row_number, column):
    """把总表单元格解析成有限浮点数。空单元格直接报错，不用演示默认值填上。"""
    raw = (text or "").strip()
    if raw == "":
        raise ValueError(f"输入总表第 {row_number} 行的 {column} 是空的。")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"输入总表第 {row_number} 行的 {column} 不是数字：{raw}") from exc
    if not math.isfinite(value):
        raise ValueError(f"输入总表第 {row_number} 行的 {column} 不是有限数字：{raw}")
    return value


def _reject_run_id_separator(value, row_number, column):
    """run_id 从右侧两处 __ 拆开，标识本身不能再含有 __。"""
    if "__" in value:
        raise ValueError(
            f"输入总表第 {row_number} 行的 {column} 含有 __，"
            f"无法从 run_id 右侧拆开：{value!r}"
        )


def _case_name_from_geometry_id(geometry_id, row_number):
    """算例目录名使用总表第一列。拒绝路径分隔符，避免写出总表目录之外。"""
    name = geometry_id.strip()
    if not name or name in {".", ".."} or any(sep in name for sep in ("/", "\\", ":")):
        raise ValueError(f"输入总表第 {row_number} 行的 geometry_id 不能作为目录名：{geometry_id!r}")
    _reject_run_id_separator(name, row_number, "geometry_id")
    return name


def load_cases_from_master_table(path):
    """
    从输入总表生成算例。

    同一个 geometry_id 的 11 个几何尺寸必须一致。同一个 geometry_id 加同一个
    altitude_m 单元格合成一次调用。这一组里的马赫数与攻角必须刚好构成完整的
    矩形组合，因为一次 DATCOM 调用会计算这两个列表的全部搭配。
    altitude_m 保留单元格原文，用来拼 run_id，不另做小数格式化。
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"找不到输入总表：{path}\n"
            "正式运行只读这张表。顶部的几何、马赫数、攻角和 --params 只用于 --demo。"
        )

    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        missing = [column for column in MASTER_TABLE_COLUMNS if column not in header]
        if missing:
            raise ValueError(
                "输入总表缺少列：" + ", ".join(missing)
                + "。需要的表头是：" + ",".join(MASTER_TABLE_COLUMNS)
            )
        raw_rows = list(reader)

    if not raw_rows:
        raise ValueError(f"输入总表没有数据行：{path}")

    grouped = {}
    order = []
    seen_triples = set()
    for index, row in enumerate(raw_rows, start=2):
        geometry_id = _case_name_from_geometry_id(row.get("geometry_id", ""), index)
        condition_id = (row.get("condition_id") or "").strip()
        if not condition_id:
            raise ValueError(f"输入总表第 {index} 行的 condition_id 是空的。")
        _reject_run_id_separator(condition_id, index, "condition_id")
        altitude_text = (row.get("altitude_m") or "").strip()
        _reject_run_id_separator(altitude_text, index, "altitude_m")
        altitude_m = _parse_table_float(altitude_text, index, "altitude_m")
        if altitude_m < 0:
            raise ValueError(f"输入总表第 {index} 行的 altitude_m 不能为负。")
        geometry = {
            key: _parse_table_float(row.get(key), index, key)
            for key in REQUIRED_GEOMETRY_KEYS
        }
        mach = _parse_table_float(row.get("Ma"), index, "Ma")
        alpha = _parse_table_float(row.get("alpha_deg"), index, "alpha_deg")
        if mach <= 0:
            raise ValueError(f"输入总表第 {index} 行的 Ma 必须大于 0。")
        if abs(alpha) >= 90:
            raise ValueError(f"输入总表第 {index} 行的 alpha_deg 绝对值必须小于 90。")
        triple = (geometry_id, condition_id, altitude_text)
        if triple in seen_triples:
            raise ValueError(
                f"输入总表第 {index} 行与已有行重复："
                f"{geometry_id}，{condition_id}，altitude_m={altitude_text}。"
            )
        seen_triples.add(triple)

        bucket = grouped.get(geometry_id)
        if bucket is None:
            bucket = {"geometry": geometry, "row": index, "altitudes": {}}
            grouped[geometry_id] = bucket
            order.append(geometry_id)
        elif any(abs(bucket["geometry"][key] - geometry[key]) > 1e-9 for key in REQUIRED_GEOMETRY_KEYS):
            raise ValueError(
                f"geometry_id {geometry_id} 在第 {bucket['row']} 行和第 {index} 行的几何尺寸不一致。"
            )
        altitude_bucket = bucket["altitudes"].get(altitude_text)
        if altitude_bucket is None:
            altitude_bucket = {"alt": altitude_m, "conditions": [], "row": index}
            bucket["altitudes"][altitude_text] = altitude_bucket
        elif abs(altitude_bucket["alt"] - altitude_m) > 1e-9:
            raise ValueError(
                f"geometry_id {geometry_id} 的 altitude_m 单元格 {altitude_text!r} "
                f"不能同时对应两个数值。"
            )
        for old in altitude_bucket["conditions"]:
            if old["condition_id"] == condition_id:
                raise ValueError(
                    f"geometry_id {geometry_id}、altitude_m={altitude_text} "
                    f"的 condition_id {condition_id} 重复。"
                )
            if abs(old["mach"] - mach) <= 1e-9 and abs(old["alpha"] - alpha) <= 1e-9:
                raise ValueError(
                    f"输入总表第 {index} 行与第 {old['row']} 行的马赫数和攻角重复："
                    f"{geometry_id}，altitude_m={altitude_text}。"
                )
        altitude_bucket["conditions"].append({
            "condition_id": condition_id,
            "mach": mach,
            "alpha": alpha,
            "altitude_text": altitude_text,
            "row": index,
            "run_id": f"{geometry_id}__{condition_id}__{altitude_text}",
        })

    cases = []
    for geometry_id in order:
        bucket = grouped[geometry_id]
        normalized, notes = normalize_geometry(bucket["geometry"])
        for note in notes:
            print(f"[INFO] 算例 '{geometry_id}': {note}")
        for altitude_text, altitude_bucket in bucket["altitudes"].items():
            conditions = altitude_bucket["conditions"]
            mach_list = sorted({item["mach"] for item in conditions})
            alpha_list = sorted({item["alpha"] for item in conditions})
            present = {(item["mach"], item["alpha"]) for item in conditions}
            expected = {(mach, alpha) for mach in mach_list for alpha in alpha_list}
            if present != expected:
                missing_pairs = ", ".join(
                    f"(Ma={mach:g}, alpha={alpha:g})" for mach, alpha in sorted(expected - present)
                )
                raise ValueError(
                    f"geometry_id {geometry_id}、altitude_m={altitude_text} 的来流不是完整组合，"
                    f"一次 DATCOM 调用仍会算完全部搭配。总表还缺：{missing_pairs}。"
                )
            cases.append({
                "name": geometry_id,
                "geometry": normalized,
                "mach": mach_list,
                "alpha": alpha_list,
                "alt": altitude_bucket["alt"],
                "altitude_text": altitude_text,
                "conditions": conditions,
            })
    return cases


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
    构造算例列表。每一项是一个 dict，键的含义就是全脚本对「算例」的定义：

        name              算例名 = 结果目录名（总表里就是 geometry_id）
        geometry          11 个主尺寸（m），已过 normalize_geometry
        mach/alpha/alt    来流：马赫数列表、攻角列表（度）、高度（m）
        altitude_text     总表里的高度原值（打印用，可能是字符串）
        conditions        总表这一格的全部 (mach, alpha) 行，CSV 合并时按它对齐
        le_mode / fin_phase / cruciform_count / xcg / timeout / fin_section
                          可选覆盖，缺项走文件顶部的默认值

    正式运行只读输入总表。

    --demo 才使用顶部 CASES、MISSILE_GEOMETRY 和命令行里的几何、马赫数、攻角、高度。
    """
    if not args.demo:
        demo_switches = []
        if args.params:
            demo_switches.append("--params")
        if args.mach:
            demo_switches.append("--mach")
        if args.alpha:
            demo_switches.append("--alpha")
        if args.alt is not None:
            demo_switches.append("--alt")
        if args.name:
            demo_switches.append("--name")
        if demo_switches:
            raise ValueError(
                "、".join(demo_switches) + " 只在 --demo 下有效。"
                "正式运行请把几何和来流写进输入总表：" + INPUT_TABLE
            )
        cases = load_cases_from_master_table(INPUT_TABLE)
        for case in cases:
            case.setdefault("timeout", args.timeout)
        return cases

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
            case = _case_from_geometry(name, raw, dict(flow), strict=True)
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
        epilog="结果写入 data_lib/runs/low/<geometry_id>/："
               "<geometry_id>_for005.dat、<geometry_id>_for006.dat、<geometry_id>_for022.dat、"
               "<geometry_id>_aero_coefficients.csv、"
               "<geometry_id>_geometry_check.txt、<geometry_id>_geometry_check.png、"
               "<geometry_id>_case_params.json、<geometry_id>_datcom.log。"
               "datcom.exe 运行时使用 data_lib/runs/low/.work 里的 for005.dat。",
    )
    parser.add_argument("--demo", action="store_true",
                        help="使用文件顶部的演示几何、来流和 CASES，不读取输入总表。"
                             "--params、--mach、--alpha、--alt、--name 只在此模式下有效。")
    parser.add_argument("--params", nargs="+", metavar="JSON",
                        help="仅 --demo：几何参数 JSON（11 个主尺寸，单位 m），每个文件一个算例。")
    parser.add_argument("--name", help="仅 --demo：算例名，只能和单个 --params 文件一起使用。")
    parser.add_argument("--mach", nargs="+", type=float,
                        help=f"仅 --demo：覆盖马赫数列表（演示默认 {MACH_LIST}）。")
    parser.add_argument("--alpha", nargs="+", type=float,
                        help=f"仅 --demo：覆盖攻角列表（度，演示默认 {ALPHA_LIST}）。")
    parser.add_argument("--alt", type=float, help=f"仅 --demo：覆盖飞行高度（m，默认 {ALTITUDE_M}）。")
    parser.add_argument("--dry-run", action="store_true",
                        help="只生成输入卡片到 data_lib/runs/low/<算例名>/，不调用 datcom。")
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
        alt_label = case.get("altitude_text")
        if alt_label is None:
            alt_label = f"{case['alt']:g}"
        print(f"    高度  : {alt_label} m")
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
    print(f"[INFO] 输入总表   : {INPUT_TABLE}")
    if args.demo:
        print("[INFO] 输入来源   : 演示参数（--demo），未读取输入总表")
    print(f"[INFO] DATCOM     : {DATCOM_EXE}")
    print(f"[INFO] 结果根目录 : {RESULT_ROOT}")
    print(f"[INFO] 待运行算例 : {len(cases)} 个")
    describe_cases(cases)
    print("=" * 70)

    if args.list:
        return 0

    os.makedirs(RESULT_ROOT, exist_ok=True)
    # 每个算例共用的临时工作目录（运行中间文件都在这，结束后删除）
    work_dir = os.path.join(RESULT_ROOT, WORK_SUBDIR)
    clear_dir(work_dir)

    if args.dry_run:
        for case in cases:
            text, meta = build_for005(
                case["geometry"], case["mach"], case["alpha"], case["alt"],
                xcg_m=case.get("xcg", XCG_M),
                le_mode=case.get("le_mode", LE_MODE),
                fin_phase=case.get("fin_phase", FIN_PHASE),
                cruciform_count=case.get("cruciform_count", CRUCIFORM_COUNT),
                fin_section=case.get("fin_section", FIN_SECTION),
            )
            out_dir = os.path.join(RESULT_ROOT, case["name"])
            os.makedirs(out_dir, exist_ok=True)
            archived_input = archived_filename(case["name"], FOR005_NAME)
            with open(os.path.join(out_dir, archived_input), "w", encoding="utf-8") as f:
                f.write(text)
            print_geometry_brief(meta)
            geom_txt, geom_png, checks = write_geometry_outputs(
                out_dir, case["name"], meta, text, baseline=MISSILE_GEOMETRY)
            bad = [msg for ok, msg in checks if not ok]
            print(f"    几何自检: {len(checks) - len(bad)}/{len(checks)} 项通过")
            for msg in bad:
                print(f"    [NG] {msg}")
            print(f"[DRY-RUN] 已生成 {os.path.join(out_dir, archived_input)}"
                  f" + {os.path.basename(geom_txt)}"
                  + (f" + {os.path.basename(geom_png)}" if geom_png else ""))
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
            summary.append((name, info["n_records"], info["expected"],
                            info["raw_result"], bool(info.get("csv_path"))))
        except Exception as exc:  # noqa: BLE001 —— 单算例失败不中断批量
            print(f"[ERROR] 算例 '{name}' 运行失败: {exc}")
            summary.append((name, None, len(case["mach"]) * len(case["alpha"]),
                            "FAILED", False))

    # 清理临时工作目录
    shutil.rmtree(work_dir, ignore_errors=True)

    print("\n" + "=" * 70)
    print("批量运行汇总:")
    print("=" * 70)
    failed = 0
    for name, n_records, expected, raw, csv_ok in summary:
        if n_records is None:
            failed += 1
            print(f"  [FAIL] {name:<24s} {'-':>6s} 条  原始输出: {raw}")
            continue
        ok = n_records == expected
        failed += 0 if ok else 1
        state = "OK  " if ok else "WARN"
        note = "" if csv_ok else f"  ← {archived_filename(name, CSV_NAME)} 未更新（文件被其它程序占用？）"
        print(f"  [{state}] {name:<24s} {n_records:>6d} 条（预期 {expected}）"
              f"  原始输出: {raw}{note}")
    print(f"\n[INFO] 结果目录: {RESULT_ROOT}")
    print(f"[INFO] 每个算例一个子目录：{archived_filename('<geometry_id>', FOR005_NAME)}（实际输入）"
          f" + {archived_filename('<geometry_id>', FOR006_NAME)}（原始输出）"
          f" + {archived_filename('<geometry_id>', FOR022_NAME)}（Tecplot 几何外形）")
    print(f"[INFO] 每个几何只写一张系数总表 {archived_filename('<geometry_id>', CSV_NAME)}，"
          f"各高度、各工况都在同一张表里；几何核对、参数快照和 datcom 日志"
          f"同样以算例名开头。工作目录 {WORK_SUBDIR} 在结束后删除。")
    return 0 if failed == 0 else 1


# =============================================================================
# 直接运行入口
# =============================================================================
if __name__ == "__main__":
    sys.exit(main())