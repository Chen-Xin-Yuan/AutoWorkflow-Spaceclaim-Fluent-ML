# -*- coding: utf-8 -*-
"""DATCOM 协议的固定名称、归档叶子名、单位换算与 11 个主尺寸参数表。

`01_batch_datcom.py` 和 `report.py` 共用这一份，避免两份常量各自漂移：
协议文件名、归档命名、单位换算、参数中文名与卡片落点都在这里改一处即可。

外形设计数据（基准几何、鼻锥头型、舵面布局）不在这里，见 `missile_spec.py`；
本模块只管"怎么和 datcom.exe 说话"和"11 个主尺寸怎么校验"。

本模块只有常量与纯函数（参数校验）：没有文件读写，因此也没有输入 / 输出路径配置区。
路径（项目根、结果根、输入总表）由调用方在运行时按脚本位置推导。
"""

from __future__ import annotations

# =============================================================================
# 1. DATCOM 协议文件名（改这里必须与 datcom.exe 的约定一致）
# =============================================================================
FOR005_NAME = "for005.dat"        # DATCOM 输入文件名
INPUT_REDIRECT = "_input.txt"     # shell 重定向应答文件（DATCOM 会问输入文件名）
FOR006_NAME = "for006.dat"        # DATCOM 输出
FOR022_NAME = "for022.dat"        # DATCOM 写出的 Tecplot ASCII 几何外形（单位英尺）

# datcom.exe 运行当下的工作目录（结果根下），算完后删除。
WORK_SUBDIR = ".work"

# =============================================================================
# 2. 归档叶子名（算例目录里实际文件名是 <算例名>_<叶子名>）
# =============================================================================
CSV_NAME = "aero_coefficients.csv"
PARAMS_NAME = "case_params.json"
GEOMETRY_TXT_NAME = "geometry_check.txt"   # 几何模型核对报告
GEOMETRY_PNG_NAME = "geometry_check.png"   # 几何三视图（侧视 / 俯视 / 前视）
DATCOM_LOG_NAME = "datcom.log"


def archived_filename(case_name, leaf):
    """算例目录里的归档名。叶子名保持各程序原来的扩展名。"""
    return f"{case_name}_{leaf}"


# =============================================================================
# 3. 单位换算
# =============================================================================
M_TO_IN = 39.37007874015748      # 1 m = 39.3701 in
M_TO_FT = 3.280839895013123      # 1 m = 3.28084 ft（DATCOM 的 ALT 用英尺）

# =============================================================================
# 4. 11 个主尺寸（单位 m，字段名与 README / 建模程序一致）
# =============================================================================
# 梢弦下限：0 会让梢边退化，DATCOM 会算错，与建模程序一致抬到 1 mm。
MIN_TIP_CHORD_M = 0.001

REQUIRED_GEOMETRY_KEYS = (
    "L_N", "L", "D",
    "L_W", "C_RW", "C_TW", "B_W",
    "L_F", "C_RF", "C_TF", "B_F",
)

# 每项的中文表头（CSV 列名，带符号与单位）
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


# =============================================================================
# 5. 几何参数校验（11 个主尺寸）
# =============================================================================
# 基准几何（README「几何参数定义与范围」那套 11 个主尺寸）不在这里：
# 它属于外形设计数据，与头型、舵面布局一起放在 missile_spec.py。
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
