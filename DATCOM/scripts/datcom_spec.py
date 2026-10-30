# -*- coding: utf-8 -*-
"""DATCOM 协议的固定名称、归档叶子名、单位换算与 11 个主尺寸参数表。

`01_batch_datcom.py` 和 `report.py` 共用这一份，避免两份常量各自漂移：
协议文件名、归档命名、单位换算、参数中文名与卡片落点都在这里改一处即可。

本模块只有常量和一个纯函数：没有文件读写，因此也没有输入 / 输出路径配置区。
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
