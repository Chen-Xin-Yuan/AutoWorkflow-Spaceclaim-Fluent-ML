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
    9. 舵面翼型是对称截面，类型和三个参数定义在 scripts/naca_section.py：
       默认六边形（SECTYP = HEX，LER/ZUPPER/LMAXU/LFLATU 四个数组直接写进
       $FINSET，文件末尾不再有 NACA 卡）；也可以切成 NACA 四位数（SECTYP =
       NACA，截面写在末尾的 NACA-1-4-... 控制卡上）。两套舵面共用同一截面，
       点云脚本导入同一份参数。

运行产物（data_lib/runs/low/<geometry_id>/，工作目录里的协议文件名不留在这里）：
    <geometry_id>_for005.dat             本次实际喂给 DATCOM 的输入
    <geometry_id>_for006.dat             DATCOM 原始输出
    <geometry_id>_for022.dat             Tecplot ASCII 几何外形（英尺）
    <geometry_id>_aero_coefficients.csv  该几何唯一的系数表，各高度、各工况都合并在这里。
                                         每行 = 工况编号 + 来流 + 11 个主尺寸 +
                                         舵面翼型（截面编号与三个参数）+ 气动系数
    <geometry_id>_geometry_check.txt     几何模型核对报告
    <geometry_id>_geometry_check.png     几何三视图（脚本自绘，仅供速查）
    <geometry_id>_case_params.json       本算例的几何、来流与派生量快照
    <geometry_id>_datcom.log             datcom.exe 的标准输出和标准错误
    同一 geometry_id 的另一次高度会覆盖上面这些按几何命名的归档；
    系数总表里其它高度的行会保留，不会被覆盖。

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
import unicodedata
from datetime import datetime

import pandas as pd

from naca_section import FIN_SECTION as DEFAULT_FIN_SECTION, resolve_fin_section

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
WORK_SUBDIR = ".work"

# 外部程序约定的输入 / 输出名称，修改时必须与 DATCOM 协议保持一致。
FOR005_NAME = "for005.dat"        # DATCOM 输入文件名
INPUT_REDIRECT = "_input.txt"     # shell 重定向应答文件
FOR006_NAME = "for006.dat"        # DATCOM 输出（首选）
DATCOM_OUT_NAME = "datcom.out"    # DATCOM 输出（备用名）
FOR022_NAME = "for022.dat"        # DATCOM 写出的 Tecplot ASCII 几何外形（单位英尺）

# 归档文件的叶子名。算例目录里实际文件名是 <算例名>_<叶子名>。
# .work 里 datcom.exe 仍读写上面的协议原名。
CSV_NAME = "aero_coefficients.csv"
# 旧版本按工况拆出来的单行表后缀（<run_id>_datcom.csv）。现在不再生成，
# 这里只用来清理老结果目录里残留的这类文件，避免同一几何的数据继续分散。
LEGACY_CASE_CSV_SUFFIX = "_datcom.csv"
PARAMS_NAME = "case_params.json"
GEOMETRY_TXT_NAME = "geometry_check.txt"   # 几何模型核对报告
GEOMETRY_PNG_NAME = "geometry_check.png"   # 几何三视图（侧视 / 俯视 / 前视）
DATCOM_LOG_NAME = "datcom.log"


def archived_filename(case_name, leaf):
    """算例目录里的归档名。叶子名保持各程序原来的扩展名。"""
    return f"{case_name}_{leaf}"

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

# 舵面翼型。类型与三个默认值在 scripts/naca_section.py 顶部（SECTION_TYPE 选
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
M_TO_IN = 39.37007874015748      # 1 m = 39.3701 in
M_TO_FT = 3.280839895013123      # 1 m = 3.28084 ft（DATCOM 的 ALT 用英尺）

MIN_TIP_CHORD_M = 0.001          # 梢弦下限，与建模程序一致（0 会退化）

REQUIRED_GEOMETRY_KEYS = (
    "L_N", "L", "D",
    "L_W", "C_RW", "C_TW", "B_W",
    "L_F", "C_RF", "C_TF", "B_F",
)

# 11 个主尺寸的 CSV 表头（中文名 + 符号 + 单位）
GEOMETRY_CN_NAMES = {
    "L_N": "鼻锥长度L_N(m)",
    "L": "轴向总长L(m)",
    "D": "机身直径D(m)",
    "L_W": "前舵轴向位置L_W(m)",
    "C_RW": "前舵根弦C_RW(m)",
    "C_TW": "前舵梢弦C_TW(m)",
    "B_W": "前舵展长B_W(m)",
    "L_F": "后舵轴向位置L_F(m)",
    "C_RF": "后舵根弦C_RF(m)",
    "C_TF": "后舵梢弦C_TF(m)",
    "B_F": "后舵展长B_F(m)",
}

# 舵面翼型也进系数表：翼型由 scripts/naca_section.py 的三个参数（截面类型可选）
# 决定，同一个 geometry_id 的所有工况共用一副截面。表里带上这几列，才能直接
# 看出这批系数对应哪种截面，不必再去翻 case_params.json。
# 列名随截面类型变化，由截面类的 CSV_COLUMNS 给出（见 build_for005 写进 meta 的
# fin_section_columns），这里不再维护第二份表头。

# 每个主尺寸最终落到 DATCOM 的哪个卡片变量（核对报告里逐项标注，方便对照 for005.dat）
GEOMETRY_CARD_HINT = {
    "L_N": "$AXIBOD LNOSE",
    "L": "$AXIBOD LCENTR = L - L_N",
    "D": "$AXIBOD DNOSE/DCENTR、$REFQ LREF",
    "L_W": "$FINSET1 XLE",
    "C_RW": "$FINSET1 CHORD(根)",
    "C_TW": "$FINSET1 CHORD(梢)",
    "B_W": "$FINSET1 SSPAN(梢 - 根)",
    "L_F": "$FINSET2 XLE",
    "C_RF": "$FINSET2 CHORD(根)",
    "C_TF": "$FINSET2 CHORD(梢)",
    "B_F": "$FINSET2 SSPAN(梢 - 根)",
}

# 两道舵面的中文名（$FINSET1 = 前舵，$FINSET2 = 后舵）
FIN_DISPLAY_NAME = {1: "前舵", 2: "后舵"}

# namelist 数组变量 -> 计数器变量 的自动联动（仅用于 overrides 高级改写）
ARRAY_COUNTERS = {
    "MACH": "NMACH",
    "ALPHA": "NALPHA",
    "ALT": "NALT",
}

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
        return value          # 原样写入（如 'NACA'、'BODY'）
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
    return result, stdout_text, stderr_text


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
# 7. 产物输出（中文表头 CSV + 几何模型核对）
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


def remove_stale_case_csvs(out_dir, conditions):
    """删掉旧版本按工况拆出来的单行表。返回实际删除的文件路径。

    同一个 geometry_id 的数据只保留 <geometry_id>_aero_coefficients.csv 一张表，
    老结果目录里遗留的 <run_id>_datcom.csv 一并清掉，不再分散。
    """
    removed = []
    if not conditions:
        return removed
    for item in conditions:
        target = os.path.join(out_dir, f"{item['run_id']}{LEGACY_CASE_CSV_SUFFIX}")
        if os.path.isfile(target):
            try:
                os.remove(target)
            except OSError as exc:
                print(f"[WARN] 无法删除旧的单工况表 {os.path.basename(target)}（{exc}）")
                continue
            removed.append(target)
    return removed


_TECPLOT_ZONE = re.compile(r'ZONE\s+T\s*=\s*"([^"]*)"\s*(.*)$', re.IGNORECASE)
_TECPLOT_DIM = re.compile(r'([IJK])\s*=\s*(\d+)', re.IGNORECASE)


def parse_tecplot_geometry(text):
    """
    解析 DATCOM 写出的 Tecplot ASCII 几何文件（for022.dat）。

    实测结构（坐标单位英尺）::

        VARIABLES=X,Y,Z
        ZONE T="FUSELAGE" I=21 J=49 K=1 F=BLOCK
        <X 数据块 I*J*K 个，再 Y 块，再 Z 块>
        ZONE T="FINSET  1" I=21 J=2 K=1 F=BLOCK
        ...（每道舵 4 片 -> 4 个 zone）

    Returns
    -------
    list[dict] : 每项含 title / i / j / k / coords（{"X": [...], "Y": [...], "Z": [...]}）；
                 解析失败时返回空列表。
    """
    zones = []
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in "*#":
            continue
        upper = stripped.upper()
        if upper.startswith("VARIABLES") or upper.startswith("TITLE"):
            continue
        match = _TECPLOT_ZONE.match(stripped)
        if match:
            dims = {key.upper(): int(val) for key, val in _TECPLOT_DIM.findall(match.group(2))}
            current = {
                "title": re.sub(r"\s+", " ", match.group(1)).strip(),
                "i": dims.get("I", 1),
                "j": dims.get("J", 1),
                "k": dims.get("K", 1),
                "values": [],
            }
            zones.append(current)
            continue
        if current is None:
            continue
        for token in stripped.replace(",", " ").split():
            try:
                current["values"].append(float(token))
            except ValueError:
                pass

    for zone in zones:
        count = zone["i"] * zone["j"] * zone["k"]
        values = zone.pop("values")
        zone["coords"] = {
            axis: values[start * count:(start + 1) * count]
            for start, axis in enumerate(("X", "Y", "Z"))
        }
    return zones


def _zone_sections(zone):
    """把 zone 的数据按 J 方向切成截面（每个截面 I 个点），返回 [(截面序号, {X,Y,Z 列表})]。"""
    i_count = zone["i"]
    sections = []
    for j in range(zone["j"]):
        chunk = slice(j * i_count, (j + 1) * i_count)
        sections.append((j, {axis: zone["coords"][axis][chunk] for axis in ("X", "Y", "Z")}))
    return sections


def check_tecplot_geometry(meta, zones, tol=0.003):
    """
    用 DATCOM 自己写出的几何文件（for022.dat）反查"建模是否正确"。

    这是最硬的核对：文件里画出来的就是 DATCOM 真正拿去算的表面。
    文件坐标单位是英尺，这里统一换成米再比对。

    Returns
    -------
    (checks, rows) : checks 为 [(bool, str)]；rows 为每个区的外形摘要（报告用）。
    """
    ft = 0.3048
    g = meta["geometry_m"]
    d = meta["derived"]
    radius = d["body_radius_m"]
    checks = []
    rows = []

    if not zones:
        return [(False, "DATCOM 没有给出可解析的 Tecplot 几何内容")], rows

    def near(label, expected, actual):
        ok = actual is not None and abs(actual - expected) <= tol
        text = f"{label}：期望 {expected:.5f} m，外形文件 {actual:.5f} m" \
            if actual is not None else f"{label}：外形文件里没有可用的数据"
        checks.append((ok, text if ok else text + "  ← 不一致"))

    # 1) 弹体
    body_zones = [z for z in zones if "FUSELAGE" in z["title"].upper()]
    if not body_zones:
        checks.append((False, "外形文件里没有 FUSELAGE 区"))
    for zone in body_zones:
        xs = [value * ft for value in zone["coords"]["X"]]
        radii = [math.hypot(y, z) * ft
                 for y, z in zip(zone["coords"]["Y"], zone["coords"]["Z"])]
        rows.append(f"FUSELAGE（{zone['i']}×{zone['j']}）: X {min(xs):.5f} ~ {max(xs):.5f} m，"
                    f"最大半径 {max(radii):.5f} m")
        near("FUSELAGE 头部起点 X", 0.0, min(xs))
        near("FUSELAGE 尾端 X（= L）", g["L"], max(xs))
        near("FUSELAGE 最大半径（= D/2）", radius, max(radii))

    # 2) 舵面：每道舵应有 NPANEL 个 zone
    groups = {}
    for zone in zones:
        match = re.search(r"FINSET\s*(\d+)", zone["title"], re.IGNORECASE)
        if match:
            groups.setdefault(int(match.group(1)), []).append(zone)
    expected_panels = d["panels_per_fin_set"]

    for fin in d["fins"]:
        index = fin["set_index"]
        fin_zones = groups.get(index, [])
        checks.append((
            len(fin_zones) == expected_panels,
            f"$FINSET{index}（{fin['name']}）片数：外形文件 {len(fin_zones)} 片，"
            f"期望 {expected_panels} 片",
        ))
        signatures = []
        for zone in fin_zones:
            sections = _zone_sections(zone)
            root = sections[0][1]
            tip = sections[-1][1]
            root_x = [value * ft for value in root["X"]]
            tip_x = [value * ft for value in tip["X"]]
            root_r = [math.hypot(y, z) * ft for y, z in zip(root["Y"], root["Z"])]
            tip_r = [math.hypot(y, z) * ft for y, z in zip(tip["Y"], tip["Z"])]
            signatures.append((min(root_x), max(root_x), min(root_r),
                               min(tip_x), max(tip_x), max(tip_r)))
        if not signatures:
            continue

        root_le, root_te, root_radius, tip_le, tip_te, tip_radius = signatures[0]
        rows.append(
            f"$FINSET{index}（{fin['name']}，{len(fin_zones)} 片）: "
            f"根部 X {root_le:.5f} ~ {root_te:.5f} m（半径 {root_radius:.5f}），"
            f"梢部 X {tip_le:.5f} ~ {tip_te:.5f} m（半径 {tip_radius:.5f}）")
        near(f"$FINSET{index} 根部前缘 X", fin["root_le_m"], root_le)
        near(f"$FINSET{index} 根部后缘 X", fin["root_te_m"], root_te)
        near(f"$FINSET{index} 根部回转半径", fin["root_radius_m"], root_radius)
        near(f"$FINSET{index} 梢部前缘 X", fin["tip_le_m"], tip_le)
        near(f"$FINSET{index} 梢部后缘 X", fin["tip_te_m"], tip_te)
        near(f"$FINSET{index} 梢部回转半径", fin["tip_radius_m"], tip_radius)
        max_dev = max(max(abs(a - b) for a, b in zip(sig, signatures[0]))
                      for sig in signatures)
        checks.append((
            max_dev <= tol,
            f"$FINSET{index} 各片几何一致（{len(signatures)} 片互差 {max_dev:.5f} m，"
            f"即十字布局对称）",
        ))
    return checks, rows


def append_tecplot_geometry_check(txt_path, checks, rows):
    """把 for022.dat（DATCOM 原生 Tecplot 几何输出）的反查结果追加到核对报告。"""
    bad = [msg for ok, msg in checks if not ok]
    state = "OK" if not bad else "NG"
    section = ["七、DATCOM 原生几何外形核对（for022.dat，Tecplot ASCII，坐标单位英尺）"]
    section += [f"  · {row}" for row in rows]
    section += [f"  [{('OK' if ok else 'NG')}] {msg}" for ok, msg in checks]
    section += [f"  合计 {len(checks) - len(bad)}/{len(checks)} 项通过"
                + ("" if not bad else "，外形与输入不一致，先查上面的 NG 项"),
                ""]
    try:
        with open(txt_path, "a", encoding="utf-8") as f:
            f.write("\n".join(section) + "\n")
    except OSError as exc:
        print(f"[WARN] 追加 for022.dat 核对段落失败：{exc}")
    return f"外形反查 [{state}] {len(checks) - len(bad)}/{len(checks)} 项通过"


def _card_value(lines, block, var, index=0):
    """读 for005 文本里某个 namelist 块内数值变量的第 index 个值；读不到返回 None。"""
    start, end = _find_namelist_region(lines, block)
    if start is None:
        return None
    pattern = re.compile(r"^(\s*)" + re.escape(var) + r"\s*=(.*)$", re.IGNORECASE)
    for idx in range(start + 1, end):
        match = pattern.match(lines[idx])
        if not match:
            continue
        values = []
        for token in match.group(2).replace("'", " ").split(","):
            token = token.strip()
            if re.fullmatch(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", token):
                values.append(float(token))
        return values[index] if index < len(values) else None
    return None


def geometry_self_checks(meta, for005_text):
    """
    逐项比对"实际写进 for005.dat 的卡片值"与"由 11 个主尺寸算出的期望值"。

    卡片值 = DATCOM 真正读到的几何，期望值 = README 参数按建模口径换算的结果，
    两者一致才算建模没错；另外复核展长为正、梢弦落在弹体范围内。

    Returns
    -------
    list[(bool, str)] : (是否通过, 说明)
    """
    g = meta["geometry_m"]
    d = meta["derived"]
    ref = meta["reference"]
    lines = for005_text.splitlines()
    checks = []

    def check(label, expected, actual, tol=0.002):
        if actual is None:
            checks.append((False, f"{label}：卡片里没读到该变量（期望 {expected:.5f}）"))
            return
        ok = abs(actual - expected) <= tol
        text = f"{label}：期望 {expected:.5f}，卡片 {actual:.5f}"
        checks.append((ok, text if ok else text + "  ← 不一致"))

    inch = M_TO_IN

    # 1) 弹体：圆锥头部 + 等直径圆柱段
    check("$AXIBOD LNOSE", g["L_N"] * inch, _card_value(lines, "AXIBOD", "LNOSE"))
    check("$AXIBOD DNOSE", g["D"] * inch, _card_value(lines, "AXIBOD", "DNOSE"))
    check("$AXIBOD LCENTR", (g["L"] - g["L_N"]) * inch,
          _card_value(lines, "AXIBOD", "LCENTR"))
    check("$AXIBOD DCENTR", g["D"] * inch, _card_value(lines, "AXIBOD", "DCENTR"))
    # 2) 参考量
    check("$REFQ SREF (in²)", ref["sref_in2"], _card_value(lines, "REFQ", "SREF"))
    check("$REFQ LREF (= D)", ref["lref_in"], _card_value(lines, "REFQ", "LREF"))
    check("$REFQ XCG", ref["xcg_m"] * inch, _card_value(lines, "REFQ", "XCG"))
    # 3) 舵面
    for fin in d["fins"]:
        block = f"FINSET{fin['set_index']}"
        check(f"${block} XLE", fin["xle_m"] * inch, _card_value(lines, block, "XLE"))
        check(f"${block} CHORD(根)", fin["root_chord_m"] * inch,
              _card_value(lines, block, "CHORD", 0))
        check(f"${block} CHORD(梢)", fin["tip_chord_m"] * inch,
              _card_value(lines, block, "CHORD", 1))
        check(f"${block} SSPAN(根)", fin["root_radius_m"] * inch,
              _card_value(lines, block, "SSPAN", 0))
        check(f"${block} SSPAN(梢)", fin["tip_radius_m"] * inch,
              _card_value(lines, block, "SSPAN", 1))
        check(f"${block} SWEEP", fin["sweep_card_deg"], _card_value(lines, block, "SWEEP"))
        checks.append((
            fin["tip_radius_m"] > fin["root_radius_m"] + 1e-6,
            f"${block} 展长：梢部半径 {fin['tip_radius_m']:.5f} m > 根部半径 "
            f"{fin['root_radius_m']:.5f} m（{fin['span_key']} = {fin['span_m']:.5f} m）",
        ))
        checks.append((
            0.0 <= fin["tip_le_m"] <= fin["tip_te_m"] <= g["L"] + 1e-6,
            f"${block} 梢弦落在弹体范围内：{fin['tip_le_m']:.5f} … {fin['tip_te_m']:.5f} m"
            f"（总长 {g['L']:.5f} m）",
        ))
    return checks


def _pad_display(text, width):
    """按"显示宽度"左对齐补空格（中文占 2 列），让核对报告里的文字列对得齐。"""
    used = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(width - used, 0)


def render_geometry_report(case_name, meta, for005_text, checks):
    """生成几何模型核对报告（纯文本），供人工确认建模是否正确。"""
    g = meta["geometry_m"]
    d = meta["derived"]
    ref = meta["reference"]
    flow = meta["flow"]
    lines = for005_text.splitlines()
    out = []
    add = out.append
    rule = "=" * 78

    add(rule)
    add(f"几何模型核对报告：算例 {case_name}")
    add(f"脚本 {os.path.basename(__file__)}    "
        f"生成时间 {datetime.now().isoformat(timespec='seconds')}")
    add(rule)
    add("")
    add("【建模口径】与建模程序 spaceclaim/lib/missile-geometry.mjs 的定义一致")
    add("  · 几何单位 m，轴向坐标 x 自弹尖(0)指向弹尾；DATCOM 卡片按英寸写入(DIM IN)。")
    add("  · L_W / L_F = 弹尖 → 舵根前缘（弹体表面）的轴向距离。")
    add("  · C_RW / C_RF = 舵根弦长（弹体表面）；C_TW / C_TF = 舵梢弦长。")
    add("  · B_W / B_F = 舵面外露展长，从弹体表面向外量，不是全展长。")
    add(f"  · 前后缘对齐 {d['le_mode']}；滚转布局 {d['fin_phase']}，"
        f"PHIF = {', '.join(f'{a:g}' for a in d['phif_deg'])} 度。")
    add("  · 弹体 = 圆锥头部 + 等直径圆柱段；每道舵按 NPANEL = "
        f"{d['panels_per_fin_set']} 片建在弹体表面（根部回转半径 = D/2）。")
    add(f"  · 舵面翼型 {d['fin_section_text']}")
    add("")
    add("一、输入的 11 个主尺寸（README 参数，单位 m）")
    for key in REQUIRED_GEOMETRY_KEYS:
        add(f"  {key:<4s} "
            + _pad_display(GEOMETRY_CN_NAMES[key], 20)
            + f"{g[key]:>9.5f} m ={g[key] * M_TO_IN:>11.5f} in   → {GEOMETRY_CARD_HINT[key]}")
    add("")
    add("二、脚本派生的几何量（人工复核用，单位 m）")
    add("  " + _pad_display("弹体：头部 / 圆柱段 / 半径", 28)
        + f"{g['L_N']:.5f} / {g['L'] - g['L_N']:.5f} / {d['body_radius_m']:.5f}"
        "（半径 = D/2）")
    for fin in d["fins"]:
        add(f"  {fin['name']}（$FINSET{fin['set_index']}）")
        add("    " + _pad_display("舵根前缘站位", 18)
            + f"{fin['root_le_m']:>9.5f}   ← {fin['pos_key']}")
        add("    " + _pad_display("舵根后缘站位", 18)
            + f"{fin['root_te_m']:>9.5f}   ← {fin['pos_key']} + {fin['root_key']}")
        add("    " + _pad_display("舵梢前缘站位", 18)
            + f"{fin['tip_le_m']:>9.5f}   ← 由前后缘对齐方式决定")
        add("    " + _pad_display("舵梢后缘站位", 18)
            + f"{fin['tip_te_m']:>9.5f}")
        add("    " + _pad_display("根部回转半径", 18)
            + f"{fin['root_radius_m']:>9.5f}   ← D/2")
        add("    " + _pad_display("梢部回转半径", 18)
            + f"{fin['tip_radius_m']:>9.5f}   ← D/2 + {fin['span_key']}")
        add("    " + _pad_display("前缘后掠角", 18)
            + f"{fin['sweep_deg']:>9.5f} 度  ← atan2(("
            f"{fin['root_key']} - {fin['tip_key']}) / {fin['span_key']})")
        add("    " + _pad_display("$FINSET SWEEP 卡片", 18)
            + f"{fin['sweep_card_deg']:>9.5f} 度  ← DATCOM 的 SWEEP 以后缘为基准，"
            "后缘对齐时写 0")
        add("    " + _pad_display("单片外露平面面积", 18)
            + f"{fin['panel_area_m2']:>9.6f} m² ← (("
            f"{fin['root_key']} + {fin['tip_key']}) / 2) × {fin['span_key']}")
    add("  " + _pad_display("参考面积 SREF = π(D/2)²", 26)
        + f"{ref['sref_m2']:.7f} m² = {ref['sref_in2']:.5f} in²")
    add("  " + _pad_display("参考长度 LREF = D", 26)
        + f"{ref['lref_m']:.5f} m = {ref['lref_in']:.5f} in")
    add("  " + _pad_display("力矩参考点 XCG", 26)
        + f"{ref['xcg_m']:.5f} m（默认弹身中点 L/2，可用 XCG_M 覆盖）")
    add("  " + _pad_display("飞行高度 ALT", 26)
        + f"{flow['altitude_m']:g} m = {flow['altitude_ft']:.5f} ft")
    add("")
    add("三、实际写入 for005.dat 的几何 / 参考量卡片（DATCOM 读到的就是这些数）")
    for block in ("REFQ", "AXIBOD", "FINSET1", "FINSET2"):
        start, end = _find_namelist_region(lines, block)
        if start is None:
            continue
        add(f"  ${block}")
        for idx in range(start + 1, end):
            add(f"  {lines[idx].strip()}")
        add("  $END")
    add("")
    add("四、几何自检（卡片值 vs 11 个主尺寸的期望值）")
    for ok, msg in checks:
        add(f"  [{'OK' if ok else 'NG'}] {msg}")
    bad = [msg for ok, msg in checks if not ok]
    add(f"  合计 {len(checks) - len(bad)}/{len(checks)} 项通过"
        + ("" if not bad else "，存在不一致，请先检查上面的 NG 项"))
    add("")
    add("五、与 README 基准几何（脚本顶部 MISSILE_GEOMETRY）的差异")
    changed = [f"{key}：基准 {float(MISSILE_GEOMETRY[key]):g} m → 本算例 {g[key]:g} m"
               for key in REQUIRED_GEOMETRY_KEYS
               if abs(g[key] - float(MISSILE_GEOMETRY[key])) > 1e-9]
    if changed:
        for item in changed:
            add(f"  · {item}")
    else:
        add("  · 无（本算例就是 README 基准几何）")
    add("")
    add("六、外形文件与图形")
    add(f"  · {FOR022_NAME}：DATCOM 自己写出的 Tecplot ASCII 几何外形（FUSELAGE + 每道舵 4 片），")
    add("    坐标单位英尺。可直接用 Tecplot 360 打开（Load Data → 选该文件），"
        "即得到真实外表面；")
    add("    本报告第七节就是拿这个文件反查建模正确性的。")
    add(f"  · {GEOMETRY_PNG_NAME}：脚本按同一套参数自绘的三视图，只用于速查，"
        "权威外形以 for022.dat 为准。")
    add("")
    return "\n".join(out) + "\n"


def append_coefficient_check(txt_path, df):
    """
    把"CL / CD 与 CN / CA 的三角关系复核"追加到核对报告末尾。

    DATCOM 的 CL / CD 是 CN / CA 按攻角旋转后的结果：
        CL = CN·cosα − CA·sinα，CD = CA·cosα + CN·sinα
    逐行复算即可确认解析出的系数列没有串位（同时校验系数定义口径）。

    Returns
    -------
    str | None : 一行结论（也打印到控制台）；数据不足时返回 None。
    """
    needed = {"CN", "CA", "CL", "CD"}
    if df.empty or not needed.issubset(df.columns):
        return None

    worst, worst_row = 0.0, None
    for _, row in df.iterrows():
        angle = math.radians(float(row["ALPHA"]))
        cl = float(row["CN"]) * math.cos(angle) - float(row["CA"]) * math.sin(angle)
        cd = float(row["CA"]) * math.cos(angle) + float(row["CN"]) * math.sin(angle)
        err = max(abs(cl - float(row["CL"])), abs(cd - float(row["CD"])))
        if err > worst:
            worst, worst_row = err, row
    scale = max(abs(float(worst_row["CL"])), abs(float(worst_row["CD"])), 1.0)
    tol = max(0.01, 0.03 * scale)
    ok = worst <= tol
    state = "OK" if ok else "NG"
    status = (f"系数换算复核 [{state}] {len(df)} 行，CL/CD 与 CN/CA 最大偏差 "
              f"{worst:.5f}（容差 {tol:.5f}）")

    section = [
        "八、系数换算复核（CL / CD 由 CN / CA 按攻角旋转）",
        "  CL = CN·cosα − CA·sinα，CD = CA·cosα + CN·sinα",
        f"  [{state}] {len(df)} 行最大偏差 {worst:.5f}（容差 {tol:.5f}）"
        + ("" if ok else "，请检查解析结果与 DATCOM 原始输出"),
        "",
    ]
    try:
        with open(txt_path, "a", encoding="utf-8") as f:
            f.write("\n".join(section) + "\n")
    except OSError as exc:
        print(f"[WARN] 追加系数复核段落失败：{exc}")
    return status


def plot_geometry(meta, path):
    """
    按 meta 里的几何画导弹三视图（侧视 / 俯视 / 前视），存成 PNG 供人工核对。

    matplotlib 不可用或绘图出错时只打印警告并返回 None —— 绘图只是辅助产物，
    不影响 DATCOM 计算与 CSV 输出。
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle, Polygon
    except Exception as exc:  # noqa: BLE001 —— 缺绘图库不影响主流程
        print(f"[WARN] 无法使用 matplotlib 画几何示意图（{exc}），跳过 {GEOMETRY_PNG_NAME}")
        return None

    g = meta["geometry_m"]
    d = meta["derived"]
    ref = meta["reference"]
    radius = d["body_radius_m"]
    length = g["L"]
    fins = d["fins"]
    colors = ("#1f77b4", "#d62728")
    fig = None
    try:
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False

        fig = plt.figure(figsize=(13.0, 11.0))
        grid = fig.add_gridspec(3, 2, height_ratios=(1.0, 1.0, 1.1),
                                hspace=0.45, wspace=0.22)
        ax_side = fig.add_subplot(grid[0, :])
        ax_top = fig.add_subplot(grid[1, :])
        ax_front = [fig.add_subplot(grid[2, 0]), fig.add_subplot(grid[2, 1])]
        tip_max = max(fin["tip_radius_m"] for fin in fins)
        arrow = dict(arrowstyle="<->", color="black", lw=0.9)
        body_color = "#e8e8e8"
        # 标注统一加白底，避免压在舵面填充色或尺寸线上看不清
        bbox_label = dict(facecolor="white", edgecolor="none", alpha=0.85, pad=0.8)

        # ---- 侧视图（X-Z）：竖直的两片舵可见，水平的两片与弹体轮廓重合 ----
        ax_side.add_patch(Polygon(
            [(0.0, 0.0), (g["L_N"], radius), (length, radius),
             (length, -radius), (g["L_N"], -radius)],
            closed=True, facecolor=body_color, edgecolor="black", lw=1.2, zorder=1))
        for idx, fin in enumerate(fins):
            color = colors[idx % len(colors)]
            for sign in (1.0, -1.0):
                ax_side.add_patch(Polygon(
                    [(fin["root_le_m"], sign * fin["root_radius_m"]),
                     (fin["root_te_m"], sign * fin["root_radius_m"]),
                     (fin["tip_te_m"], sign * fin["tip_radius_m"]),
                     (fin["tip_le_m"], sign * fin["tip_radius_m"])],
                    closed=True, facecolor=color, edgecolor=color,
                    alpha=0.8, lw=1.0, zorder=3))
            for station in (fin["root_le_m"], fin["root_te_m"]):
                ax_side.plot([station, station],
                             [-fin["tip_radius_m"], fin["tip_radius_m"]],
                             ls=":", color=color, lw=0.8, zorder=2)
            ax_side.text(
                fin["root_te_m"] if fin["root_le_m"] > length / 2.0 else fin["root_le_m"],
                fin["tip_radius_m"] + (0.06 + 0.85 * idx) * tip_max,
                f"{fin['name']}  {fin['pos_key']} = {g[fin['pos_key']]:.3f} m\n"
                f"展长 {fin['span_key']} = {fin['span_m']:.3f} m，"
                f"后掠 {fin['sweep_deg']:.2f}°",
                color=color, fontsize=8, va="bottom", bbox=bbox_label,
                ha="right" if fin["root_le_m"] > length / 2.0 else "left")

        ax_side.annotate("", xy=(0.0, tip_max * 1.75), xytext=(g["L_N"], tip_max * 1.75),
                         arrowprops=arrow)
        ax_side.text(g["L_N"] / 2.0, tip_max * 1.75, f" L_N = {g['L_N']:.3f} m ",
                     ha="center", va="bottom", fontsize=8, bbox=bbox_label)
        ax_side.annotate("", xy=(0.0, -tip_max * 1.18), xytext=(length, -tip_max * 1.18),
                         arrowprops=arrow)
        ax_side.text(length / 2.0, -tip_max * 1.18, f" L = {g['L']:.3f} m ",
                     ha="center", va="top", fontsize=8, bbox=bbox_label)
        ax_side.annotate("", xy=(-0.06 * length, -radius), xytext=(-0.06 * length, radius),
                         arrowprops=arrow)
        ax_side.text(-0.06 * length, 0.0, f"D = {g['D']:.3f} m ", rotation=90,
                     ha="right", va="center", fontsize=8, bbox=bbox_label)
        ax_side.axvline(ref["xcg_m"], color="#2ca02c", ls="--", lw=1.0, zorder=0)
        ax_side.text(ref["xcg_m"], -tip_max * 1.55, f"XCG = {ref['xcg_m']:.3f} m",
                     color="#2ca02c", fontsize=8, ha="center", va="top",
                     bbox=bbox_label)
        ax_side.set_xlim(-0.14 * length, 1.04 * length)
        ax_side.set_ylim(-tip_max * 1.75, tip_max * 2.25)
        ax_side.set_aspect("equal")
        ax_side.set_title("侧视图（X-Z，单位 m）：竖直两片舵面的投影，水平两片与弹体重合",
                          fontsize=10)
        ax_side.set_xlabel("x：自弹尖(0)向弹尾 (m)")
        ax_side.set_ylabel("z (m)")

        # ---- 俯视图（X-Y）：水平的两片舵可见 ----
        ax_top.add_patch(Polygon(
            [(0.0, 0.0), (g["L_N"], radius), (length, radius),
             (length, -radius), (g["L_N"], -radius)],
            closed=True, facecolor=body_color, edgecolor="black", lw=1.2, zorder=1))
        for idx, fin in enumerate(fins):
            color = colors[idx % len(colors)]
            for sign in (1.0, -1.0):
                ax_top.add_patch(Polygon(
                    [(fin["root_le_m"], sign * fin["root_radius_m"]),
                     (fin["root_te_m"], sign * fin["root_radius_m"]),
                     (fin["tip_te_m"], sign * fin["tip_radius_m"]),
                     (fin["tip_le_m"], sign * fin["tip_radius_m"])],
                    closed=True, facecolor=color, edgecolor=color,
                    alpha=0.8, lw=1.0, zorder=3))
            band = fin["tip_radius_m"] - fin["root_radius_m"]
            ax_top.annotate("", xy=(fin["root_te_m"], fin["root_radius_m"]),
                            xytext=(fin["root_te_m"], fin["tip_radius_m"]),
                            arrowprops=dict(arrowstyle="<->", color=color, lw=0.9))
            ax_top.text(fin["root_te_m"], fin["tip_radius_m"] + 0.12 * tip_max,
                        f"{fin['span_key']} = {fin['span_m']:.3f} m", color=color,
                        fontsize=8, ha="center", va="bottom", bbox=bbox_label)
            ax_top.text((fin["root_le_m"] + fin["root_te_m"]) / 2.0,
                        fin["root_radius_m"] + 0.30 * band,
                        f"{fin['root_key']} = {fin['root_chord_m']:.3f}",
                        color=color, fontsize=7, ha="center", va="center",
                        bbox=bbox_label)
            ax_top.text((fin["tip_le_m"] + fin["tip_te_m"]) / 2.0,
                        fin["tip_radius_m"] - 0.10 * band,
                        f"{fin['tip_key']} = {fin['tip_chord_m']:.3f}",
                        color=color, fontsize=7, ha="center", va="center",
                        bbox=bbox_label)
        ax_top.annotate("", xy=(0.0, -tip_max * 1.2), xytext=(length, -tip_max * 1.2),
                        arrowprops=arrow)
        ax_top.text(length / 2.0, -tip_max * 1.2, f" L = {g['L']:.3f} m ",
                    ha="center", va="top", fontsize=8, bbox=bbox_label)
        ax_top.set_xlim(-0.04 * length, 1.04 * length)
        ax_top.set_ylim(-tip_max * 1.55, tip_max * 1.55)
        ax_top.set_aspect("equal")
        ax_top.set_title("俯视图（X-Y，单位 m）：水平两片舵面的平面形状", fontsize=10)
        ax_top.set_xlabel("x：自弹尖(0)向弹尾 (m)")
        ax_top.set_ylabel("y (m)")

        # ---- 前视图（Y-Z）：十字布局与"展长自弹体表面量起" ----
        for ax_front_ax, fin, color in zip(ax_front, fins, colors):
            ax_front_ax.add_patch(Circle((0.0, 0.0), radius, facecolor=body_color,
                                        edgecolor="black", lw=1.2, zorder=2))
            for angle in d["phif_deg"]:
                rad = math.radians(angle)
                ax_front_ax.plot(
                    [radius * math.cos(rad), fin["tip_radius_m"] * math.cos(rad)],
                    [radius * math.sin(rad), fin["tip_radius_m"] * math.sin(rad)],
                    color=color, lw=5, solid_capstyle="butt", zorder=3)
            ax_front_ax.plot([0.0], [0.0], marker="+", color="black", ms=8)
            span_lim = fin["tip_radius_m"] * 1.25
            ax_front_ax.annotate(
                "", xy=(radius, 0.12 * span_lim),
                xytext=(fin["tip_radius_m"], 0.12 * span_lim),
                arrowprops=dict(arrowstyle="<->", color="#555555", lw=0.9))
            ax_front_ax.text((radius + fin["tip_radius_m"]) / 2.0, 0.19 * span_lim,
                             f"{fin['span_key']} = {fin['span_m']:.3f} m",
                             fontsize=8, ha="center", va="bottom", color="#333333",
                             bbox=bbox_label)
            ax_front_ax.set_xlim(-span_lim, span_lim)
            ax_front_ax.set_ylim(-span_lim, span_lim)
            ax_front_ax.set_aspect("equal")
            ax_front_ax.set_title(
                f"{fin['name']}前视图：{d['panels_per_fin_set']} 片，PHIF = "
                + "/".join(f"{a:g}°" for a in d["phif_deg"]), fontsize=10)
            ax_front_ax.set_xlabel("y (m)")
            ax_front_ax.set_ylabel("z (m)")

        fig.suptitle(
            f"几何模型核对：L = {g['L']:.3f} m，D = {g['D']:.3f} m，"
            f"L_N = {g['L_N']:.3f} m，SREF = {ref['sref_m2']:.6f} m²，"
            f"LREF = {ref['lref_m']:.3f} m，XCG = {ref['xcg_m']:.3f} m",
            fontsize=11)
        fig.savefig(path, dpi=140, bbox_inches="tight", facecolor="white")
        return path
    except Exception as exc:  # noqa: BLE001 —— 绘图失败不影响计算
        print(f"[WARN] 绘制几何示意图失败（{exc}），已跳过 {GEOMETRY_PNG_NAME}")
        return None
    finally:
        if fig is not None:
            plt.close(fig)


def print_geometry_brief(meta):
    """在控制台打印几何摘要（详细核对见 geometry_check.txt / .png）。"""
    g = meta["geometry_m"]
    d = meta["derived"]
    ref = meta["reference"]
    print(f"    几何: L = {g['L']:.3f} m, D = {g['D']:.3f} m, L_N = {g['L_N']:.3f} m, "
          f"SREF = {ref['sref_m2']:.6f} m², LREF = {ref['lref_m']:.3f} m, "
          f"XCG = {ref['xcg_m']:.3f} m")
    print(f"    翼型: {d['fin_section_text']}")
    for fin in d["fins"]:
        print(f"    {fin['name']}: XLE = {fin['root_le_m']:.3f} m, "
              f"根弦 {fin['root_chord_m']:.3f} m, 梢弦 {fin['tip_chord_m']:.3f} m, "
              f"展长 {fin['span_m']:.3f} m, 后掠 {fin['sweep_deg']:.2f}°, "
              f"梢部半径 {fin['tip_radius_m']:.3f} m")


def write_geometry_outputs(out_dir, case_name, meta, for005_text):
    """
    把几何核对产物写到算例目录：geometry_check.txt + geometry_check.png。

    Returns
    -------
    (txt_path, png_path, checks) : png_path 在无法绘图时为 None。
    """
    os.makedirs(out_dir, exist_ok=True)
    checks = geometry_self_checks(meta, for005_text)
    txt_path = os.path.join(out_dir, archived_filename(case_name, GEOMETRY_TXT_NAME))
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(render_geometry_report(case_name, meta, for005_text, checks))
    png_path = plot_geometry(meta, os.path.join(out_dir, archived_filename(case_name, GEOMETRY_PNG_NAME)))
    return txt_path, png_path, checks


# =============================================================================
# 8. 单算例执行
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

    # 2.2) 几何核对产物先落盘：即使后面 DATCOM 跑失败，也能据此确认几何建对没有
    out_dir = os.path.join(RESULT_ROOT, name)
    os.makedirs(out_dir, exist_ok=True)
    print_geometry_brief(meta)
    geom_txt, geom_png, checks = write_geometry_outputs(out_dir, name, meta, for005_text)
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
            f"算例 '{name}' 未生成结果文件（{FOR006_NAME}/{DATCOM_OUT_NAME}），"
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
    stale_case_csvs = []
    csv_written = False
    if not df.empty:
        table = attach_case_identity(build_output_table(df, meta), case)
        try:
            write_summary_csv(csv_path, table)
            stale_case_csvs = remove_stale_case_csvs(out_dir, case.get("conditions") or [])
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
        if stale_case_csvs:
            print(f"    已清理旧版单工况表 {len(stale_case_csvs)} 个，"
                  f"该几何的系数只保留 {os.path.basename(csv_path)}")

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
    with open(os.path.join(out_dir, archived_filename(name, PARAMS_NAME)), "w", encoding="utf-8") as f:
        json.dump(params, f, ensure_ascii=False, indent=2)

    return {
        "name": name,
        "n_records": len(df),
        "expected": expected,
        "out_dir": out_dir,
        "csv_path": csv_path if csv_written else None,
        "stale_case_csvs": stale_case_csvs,
        "geometry_txt": geom_txt,
        "geometry_png": geom_png,
        "geometry_tecplot": tecplot_path,
        "checks_failed": len(bad),
        "raw_result": os.path.basename(result_path),
    }


# =============================================================================
# 9. 命令行与主流程
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
                "source": path,
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
        case["source"] = "demo"
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
                out_dir, case["name"], meta, text)
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
