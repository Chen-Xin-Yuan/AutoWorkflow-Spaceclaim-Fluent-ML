# -*- coding: utf-8 -*-
"""导弹外形与舵面翼型的全部定义：可调参数 + 解析式（01 批量 DATCOM 与 04 点云共用）。

**这是本项目唯一需要改参数的文件**。所有不进 ``data_lib/inputs/cases.csv`` 的
外形数据都集中在第 1、2 节的配置区（文件前部，一眼能看到）：

    1.1 鼻锥      头型（切线卵形 / 圆锥）、锥顶倒角比例、母线离散段数
    1.2 舵面翼型  截面类型（六边形 / NACA）与两套各三个参数
    1.3 舵面布局  前后缘对齐、滚转布局、片数、力矩参考点
    2   基准几何  11 个主尺寸（README「几何参数定义与范围」那套）

改一处，`01_batch_datcom.py`（DATCOM 卡片与系数）和 `04_pointnet_demo.py`
（点云母线、舵面截面）同时生效，不会出现两边参数不一致：

    NOSE_SHAPE = "ogive"    →  01 写 $AXIBOD TNOSE = OGIVE，04 按同一条卵形母线建点云
    NOSE_SHAPE = "cone"     →  01 写 TNOSE = CONE，04 按直线圆锥母线建点云
    SECTION_TYPE = "HEX"    →  01 写 SECTYP = HEX + LER/ZUPPER/LMAXU/LFLATU，04 按六边形
    SECTION_TYPE = "NACA"   →  01 写 SECTYP = NACA + 末尾 NACA-1-4-... 卡，04 按四位数翼型

第 3、4 节是解析式（两种鼻锥头型的母线 / 侧面积 / 倒角相切点，两种舵面翼型的
外形方程与 DATCOM 卡片文本），第 5 节把配置区的东西组装成可以直接用的对象
（``FIN_SECTION`` / ``FIN_AZIMUTH_DEG``，以及第 3 节末尾的 ``resolve_nose_shape``）。
这三节都是纯函数与类，正常改参数不需要动；翼型与头型都**不能**按算例覆盖。

本模块没有文件输入输出：DATCOM 的协议名、单位换算、11 个主尺寸的校验在
``datcom_spec.py``，路径由调用方给出。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

# =============================================================================
# 1. 参数配置区（唯一可改的地方；改一处 01 与 04 同时生效）
# =============================================================================
# ---- 1.1 鼻锥头型与锥顶倒角 ----
# 头型："ogive" = 切线卵形（DATCOM TNOSE = OGIVE，也是不写 TNOSE 时的默认头型）
#       "cone"  = 直线圆锥（DATCOM TNOSE = CONE）
# DATCOM 只认 LNOSE / DNOSE，头型靠这条卡片；点云脚本用同一个头型建母线。
NOSE_SHAPE = "cone"

# 锥顶倒角球冠半径 = 比例 × 弹长
# 只有点云 / CFD 用：DATCOM 的头锥是数学尖点，不建倒角。置 0 即退化成尖头。
NOSE_TIP_FILLET_RATIO = 0.002
# 直接给倒角半径（m）时覆盖上面的比例；None = 用比例。
NOSE_TIP_FILLET_RADIUS = None
# 头锥母线离散段数（网格与折线用）。
NOSE_SEGMENTS = 120

# ---- 1.2 舵面翼型（前舵后舵共用一副截面；外形方程见第 4 节）----
# 截面类型："HEX" = 对称六边形（DATCOM SECTYP = HEX）
#           "NACA" = 对称 NACA 四位数（DATCOM SECTYP = NACA，末尾另加控制卡）
SECTION_TYPE = "HEX"

# 六边形参数（SECTION_TYPE = "HEX" 时生效）
# 最大厚度，弦长百分数。DATCOM 卡里 ZUPPER = thickness_percent / 200。
HEX_THICKNESS_PERCENT = 4.462
# 平顶段长度，弦长百分数。LFLATU = flat_percent / 100，
# 对称六边形的前缘斜坡终点 LMAXU = (1 - LFLATU) / 2。
# 64.6 → 平顶 0.177..0.823。这个值由 SpaceClaim 侧实测反推：前舵根部 3 mm 交接圆角
# 要撑住，平顶终点必须 ≥ 0.822（拐点在 0.8220/0.8221 之间），60.995（终点 0.805）
# 会被压到 1.5 mm 下限并留下 24 个非圆角碎面。DATCOM 的六边形本就对称，
# 两边共用这一个值，否则同一个 geometry_id 在两侧表示不同的截面。
HEX_FLAT_PERCENT = 64.6
# DATCOM 的 LER 卡片值（前缘圆角参数），原样写入，只影响 DATCOM 的气动结果。
# 口径（绝对英寸还是弦长分数）没能从 DATCOM 手册确认；实测它只改变阻力：
# 0 -> CD 0.330，0.015 -> 0.349，0.05 -> 0.371，0.15 -> 0.451，0.5 以上饱和。
HEX_LE_RADIUS = 0.05

# NACA 四位数参数（SECTION_TYPE = "NACA" 时生效）
# 最大厚度，弦长的百分数，写卡时保留两位小数。
NACA_THICKNESS_PERCENT = 4.48
# 前缘半径指数，整数 0–9。0 是尖前缘，6 是标准圆前缘。
NACA_LE_RADIUS_INDEX = 6
# 最大厚度位置，单位是 1/10 弦长，只允许 2、3、4、5、6。
NACA_MAX_THICKNESS_TENTHS = 3

# ---- 1.3 舵面布局 ----
# 前后缘对齐方式：trailing_aligned（后缘垂直于弹轴，点云只支持这一种）/
# leading_aligned / symmetric。默认后缘对齐，与建模程序 spaceclaim 一致。
LE_MODE = "trailing_aligned"
# 舵面滚转布局："+"（十字，舵面在水平/竖直平面）或 "x"（X 形，错开 45°）
FIN_PHASE = "+"
# 每道舵面的片数（十字舵 = 4）
CRUCIFORM_COUNT = 4
# 力矩参考点轴向位置（m）。None = 取 L/2（弹身中点）。只有 01 用。
XCG_M = None

# =============================================================================
# 2. 基准几何（11 个主尺寸，单位 m，字段名与 README / 建模程序一致）
# =============================================================================
BASELINE_GEOMETRY = {
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

# =============================================================================
# 3. 鼻锥头型：DATCOM 卡片文本 + 母线 / 侧面积 / 相切点解析式（不用改）
# =============================================================================
# 几何口径（回转轴 = +X，x 自弹头顶点量起，单位 m）：
#   理想头型  r(x)，0 <= x <= L_N，r(0) = 0、r(L_N) = R_body
#   锥顶倒角  球心 (R_f, 0)、半径 R_f 的球冠，与理想母线在 x_t 处相切
#   实际母线  [0, x_t] 取球冠，x_t 之后取理想母线（两侧在 x_t 处斜率连续）
#
# 两种头型的差别只在"理想母线"：
#   圆锥      直线，r = m·x 平移后过 (L_N, R_body)
#   切线卵形  半径 ρ 的圆弧，圆心 (L_N, R_body − ρ)，在 x = L_N 处与圆柱相切
@dataclass(frozen=True)
class NoseShape:
    """一种鼻锥头型：DATCOM 的 TNOSE 名字 + 母线解析式工厂。"""

    key: str            # 规范名，与 NOSE_SHAPE 比较（"ogive" / "cone"）
    cn_name: str        # 中文名，报告与日志用
    datcom_token: str   # 写进 $AXIBOD 的 TNOSE 值（DATCOM 只认 OGIVE / CONE）

    def label(self) -> str:
        """一行中文说明，形如「切线卵形（TNOSE = OGIVE）」。"""
        return f"{self.cn_name}（TNOSE = {self.datcom_token}）"

    def card_line(self) -> str:
        """$AXIBOD 里的 TNOSE 行（DATCOM 卡片格式：行首空格、行尾逗号）。"""
        return f" TNOSE = {self.datcom_token},"

    def profile(self, nose_length, body_radius, fillet_radius=0.0) -> "NoseProfile":
        """按具体尺寸解析出一条母线（含锥顶倒角）。"""
        raise NotImplementedError


@dataclass(frozen=True)
class NoseProfile:
    """一条具体的头锥母线：长 L、弹身半径 R_body、锥顶倒角半径 R_f。

    Subclasses（``ConeProfile`` / ``OgiveProfile``）只实现「理想母线」与相切点，
    球冠、分段取值和侧面积由本类统一处理，两种头型共用同一套接口。
    """

    shape: NoseShape
    nose_length: float
    body_radius: float
    fillet_radius: float = 0.0

    # 理想母线是否弯曲：圆锥是直线（离散只用两站），卵形是圆弧（要细分）
    curved_body: bool = False

    def __post_init__(self):
        for name, value in (("头锥长度", self.nose_length), ("弹身半径", self.body_radius)):
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name}必须大于 0，收到 {value!r}。")
        R = self.fillet_radius
        if not math.isfinite(R) or R < 0.0:
            raise ValueError(f"锥顶倒角半径不能为负，收到 {R!r}。")
        if R > 0.0:
            limit = min(self.body_radius, self.nose_length / 2.0)
            if R >= limit:
                raise ValueError(
                    f"锥顶倒角半径 R={R:.6g} m 与头锥几何不相容：需 0 <= R < "
                    f"min(body_radius={self.body_radius:g}, "
                    f"nose_length/2={self.nose_length / 2:g})；"
                    f"请调小 nose_tip_fillet_ratio 或 nose_tip_fillet_radius"
                )
            self._check_tangent()

    # ---- 子类必须实现：理想母线与相切点 ----
    def _ideal_radius(self, x):
        raise NotImplementedError

    def _ideal_slope(self, x):
        raise NotImplementedError

    def _ideal_area(self, x0):
        """x0 → L_N 这一段的理想母线回转侧面积（解析式）。"""
        raise NotImplementedError

    def _tangent_point(self):
        """(x_t, r_t)：球冠与理想母线的切点。无倒角时不会被调用。"""
        raise NotImplementedError

    def _check_tangent(self):
        """有倒角时校验切点落在头锥内部（子类按需要补充判别式检查）。"""
        xt, rt = self._tangent_point()
        if not (0.0 < xt < self.nose_length and rt > 0.0):
            raise ValueError(
                f"锥顶倒角半径 R={self.fillet_radius:.6g} m 与"
                f"{self.shape.cn_name}头锥不相容：切点 x_t={xt:.6g} m 不在 (0, "
                f"{self.nose_length:g}) 内；请调小倒角半径"
            )

    # ---- 通用接口 ----
    @property
    def has_fillet(self) -> bool:
        return self.fillet_radius > 0.0

    @property
    def tangent_x(self) -> float:
        """球冠与理想母线的切点轴向坐标（无倒角时为 0，即从尖点开始）。"""
        return self._tangent_point()[0] if self.has_fillet else 0.0

    @property
    def tangent_r(self) -> float:
        """切点半径（无倒角时为 0）。"""
        return self._tangent_point()[1] if self.has_fillet else 0.0

    def radius(self, x):
        """母线：x → 半径（m）。倒角段取球冠，其余取理想母线。"""
        x = np.asarray(x, dtype=float)
        if not self.has_fillet:
            return np.asarray(self._ideal_radius(x), dtype=float)
        y = np.empty_like(x)
        cap = x <= self.tangent_x
        # 球冠：球心 (R_f, 0)、半径 R_f，r = sqrt(2·R_f·x − x²)
        y[cap] = np.sqrt(np.clip(2.0 * self.fillet_radius * x[cap] - x[cap] ** 2, 0.0, None))
        y[~cap] = self._ideal_radius(x[~cap])
        return y

    def slope(self, x):
        """母线斜率 dr/dx（解析式）。球冠在 x→0 处发散，用 eps 截断。"""
        x = np.asarray(x, dtype=float)
        if not self.has_fillet:
            return np.asarray(self._ideal_slope(x), dtype=float)
        y = np.empty_like(x)
        cap = x <= self.tangent_x
        denom = np.sqrt(np.clip(2.0 * self.fillet_radius * x[cap] - x[cap] ** 2, 1e-12, None))
        y[cap] = (self.fillet_radius - x[cap]) / denom
        y[~cap] = self._ideal_slope(x[~cap])
        return y

    def lateral_area(self) -> float:
        """头锥侧面积（m²，含球冠；球冠另按 2πR·x_t 解析给出）。"""
        if not self.has_fillet:
            return float(self._ideal_area(0.0))
        cap = 2.0 * math.pi * self.fillet_radius * self.tangent_x
        return float(cap + self._ideal_area(self.tangent_x))

    def stations(self, n_body: int = NOSE_SEGMENTS) -> list:
        """网格 / 折线用的头锥轴向站（不含顶点 x = 0，含 x = L_N）。"""
        xt = self.tangent_x
        stations = []
        if self.has_fillet:
            # 球冠段多切几刀，网格上能看出倒角
            stations += [0.25 * xt, 0.5 * xt, 0.75 * xt, xt]
        if self.curved_body:
            count = max(int(n_body), 1)
            stations += list(np.linspace(xt, self.nose_length, count + 1)[1:])
        elif self.nose_length > xt:
            stations.append(self.nose_length)
        return [float(x) for x in stations]


@dataclass(frozen=True)
class ConeProfile(NoseProfile):
    """直线圆锥母线（DATCOM TNOSE = CONE）。"""

    curved_body: bool = False

    @property
    def line_slope(self) -> float:
        """圆锥母线斜率 m = dr/dx。

        无倒角时就是 R_body / L_N。有倒角时母线还要与球冠（球心 (R, 0)、
        半径 R）相切，记 d = L_N - R、Rb = R_body，则 m 满足
            m²(d² - R²) - 2·Rb·d·m + (Rb² - R²) = 0
        取较小的根 = 与球冠前方相切的那条母线（比无倒角时略缓）。
        """
        R, Rb, Ln = self.fillet_radius, self.body_radius, self.nose_length
        if R <= 0.0:
            return Rb / Ln
        d = Ln - R
        disc = d * d + Rb * Rb - R * R        # > 0 由 R < min(Rb, Ln/2) 保证
        if disc <= 0.0:
            raise ValueError(f"锥顶倒角半径 R={R:.6g} m 与圆锥头锥不相容（判别式非正）")
        return (Rb * d - R * math.sqrt(disc)) / (d * d - R * R)

    def _ideal_radius(self, x):
        x = np.asarray(x, dtype=float)
        return self.body_radius - self.line_slope * (self.nose_length - x)

    def _ideal_slope(self, x):
        return np.full_like(np.asarray(x, dtype=float), self.line_slope)

    def _ideal_area(self, x0):
        # 圆台侧面积：π(r0 + R_body)·斜高
        r0 = float(self._ideal_radius(x0))
        return math.pi * (r0 + self.body_radius) * math.hypot(
            self.nose_length - x0, self.body_radius - r0)

    def _tangent_point(self):
        R = self.fillet_radius
        m = self.line_slope
        k = (self.nose_length - R + m * self.body_radius) / (1.0 + m * m)
        return self.nose_length - k, self.body_radius - m * k


@dataclass(frozen=True)
class OgiveProfile(NoseProfile):
    """切线卵形母线（DATCOM TNOSE = OGIVE，也是不写 TNOSE 时的默认）。"""

    curved_body: bool = True

    @property
    def arc_radius(self) -> float:
        """卵形母线所在圆弧的半径 ρ，圆心在 (L_N, R_body − ρ)。

        无倒角时（切线卵形）圆弧过 (0, 0) 与 (L_N, R_body)，ρ = (L_N² + R_body²) / (2·R_body)。
        有倒角时圆弧改由「与球冠内切」定出（|C_arc − C_cap| = ρ − R_f）：
            ρ = [(L_N − R_f)² + R_body² − R_f²] / [2 (R_body − R_f)]
        R_f → 0 时与上面的切线卵形一致。
        """
        R, Rb, Ln = self.fillet_radius, self.body_radius, self.nose_length
        if R <= 0.0:
            return (Ln * Ln + Rb * Rb) / (2.0 * Rb)
        return ((Ln - R) ** 2 + Rb * Rb - R * R) / (2.0 * (Rb - R))

    def _ideal_radius(self, x):
        x = np.asarray(x, dtype=float)
        rho, Rb, Ln = self.arc_radius, self.body_radius, self.nose_length
        return np.sqrt(np.clip(rho * rho - (x - Ln) ** 2, 0.0, None)) + Rb - rho

    def _ideal_slope(self, x):
        x = np.asarray(x, dtype=float)
        rho, Ln = self.arc_radius, self.nose_length
        denom = np.sqrt(np.clip(rho * rho - (x - Ln) ** 2, 1e-300, None))
        return (Ln - x) / denom

    def _ideal_area(self, x0):
        """圆弧段回转侧面积，解析积分。

        r ds = (ρ√(1−u²) + R_body − ρ) · ρ/√(1−u²) du·ρ，u = (x − L_N)/ρ，得
            A = −2πρ² [u + ((R_body − ρ)/ρ)·asin u]  （在 u0 处取负号，u = 0 处为 0）
        """
        rho, Rb, Ln = self.arc_radius, self.body_radius, self.nose_length
        u0 = (x0 - Ln) / rho
        f = u0 + ((Rb - rho) / rho) * math.asin(max(-1.0, min(1.0, u0)))
        return float(-2.0 * math.pi * rho * rho * f)

    def _tangent_point(self):
        # 两圆内切：切点落在连心线上，距圆心 C_arc 为 ρ
        R, rho = self.fillet_radius, self.arc_radius
        Ln, Rb = self.nose_length, self.body_radius
        denom = rho - R
        if denom <= 0.0:
            raise ValueError(f"锥顶倒角半径 R={R:.6g} m 大于卵形圆弧半径 ρ={rho:.6g} m")
        return Ln + rho * (R - Ln) / denom, R * (rho - Rb) / denom


# ---- 头型对象：只负责“名字 + 造母线” ----
@dataclass(frozen=True)
class OgiveShape(NoseShape):
    """切线卵形：DATCOM 不写 TNOSE 时的默认头型。"""

    def profile(self, nose_length, body_radius, fillet_radius=0.0) -> OgiveProfile:
        return OgiveProfile(self, float(nose_length), float(body_radius), float(fillet_radius))


@dataclass(frozen=True)
class ConeShape(NoseShape):
    """直线圆锥。"""

    def profile(self, nose_length, body_radius, fillet_radius=0.0) -> ConeProfile:
        return ConeProfile(self, float(nose_length), float(body_radius), float(fillet_radius))


# 规范名 → 头型对象。DATCOM 只认 OGIVE / CONE 两个名字（写成 CONIC 会 FATAL ERROR）。
NOSE_SHAPES = {
    "ogive": OgiveShape("ogive", "切线卵形", "OGIVE"),
    "cone": ConeShape("cone", "圆锥", "CONE"),
}


def resolve_nose_shape(value=None) -> NoseShape:
    """把 ``NOSE_SHAPE`` / 字符串 / ``NoseShape`` 实例统一成 ``NoseShape``。

    ``None`` 取本模块顶部的 ``NOSE_SHAPE``，因此调用方不用重复写默认值。
    """
    if isinstance(value, NoseShape):
        return value
    key = NOSE_SHAPE if value is None else str(value).strip().lower()
    if key not in NOSE_SHAPES:
        raise ValueError(
            f"不支持的头型 {value!r}，仅支持 " + " / ".join(sorted(NOSE_SHAPES)) + "。")
    return NOSE_SHAPES[key]


# =============================================================================
# 4. 舵面翼型方程（不用改）
# =============================================================================
# 参数在 1.2 节；这里只有两种截面的外形方程、校验和 DATCOM 卡片文本。
# 前舵和后舵共用同一副截面，根部和梢部按当地弦长等比缩放（等 t/c）。
# 上下对称，中弧与弦线重合，前缘和后缘都按所选截面的公式收成尖点。
#
# 两种截面怎么进 DATCOM：
#
#     NACA（SECTYP = NACA，截面写在文件末尾的控制卡上）
#         $FINSET1
#          SECTYP = NACA,
#          ...
#         $END
#         ...
#         NACA-1-4-0004.48-63
#         NACA-2-4-0004.48-63
#
#     HEX（SECTYP = HEX，截面参数直接进 $FINSET，根/梢各一个值）
#         $FINSET1
#          SECTYP = HEX,
#          ...
#          STA = 1.0,
#          LER = 0.50000,0.50000,
#          ZUPPER = 0.02230,0.02230,
#          LMAXU = 0.19500,0.19500,
#          LFLATU = 0.61000,0.61000,
#         $END
#
# 实测口径（本机 datcom.exe REV 03/11，用临时算例逐项验证）：
#     * ZUPPER 是半厚 / 弦长：乘 2 后 CD 由 0.659 升到 0.916，乘 10 后 CD 11.6、
#       CL 掉到 0.838；填 0.6（总厚 120% 弦长）直接算不出结果。
#     * LMAXU 是平顶起点，不是平顶中点：因为 (1 - 0.524) / 2 = 0.238 正好等于
#       历史算例给的 LMAXU，前缘斜坡与后缘斜坡等长。
#     * 必须满足 LMAXU + LFLATU <= 1：填 0.238 + 0.8 时没有任何静态气动页。
#     * 参数行漏写不会报错，DATCOM 会静默用默认截面，所以写完要核对 for005.dat。
#     * for022.dat 里画的舵面截面恒为 5% 菱形示意，不随参数变化，不能用来核对。
#     * LER 只改变阻力：0 -> CD 0.330，0.015 -> 0.349，0.05 -> 0.371，
#       0.15 -> 0.451，0.5 以上饱和。

# 面积均匀抽样用的弦向网格点数，只影响点云抽样的平滑度（内部常量，不是用户参数）。
_SAMPLING_GRID = 401


# =============================================================================
# 公共基类：外形方程之外的几何量、抽样与记录
# =============================================================================
class FinSection:
    """两种截面共用的接口。子类只需实现外形方程和卡片文本。"""

    # 写进 for005.dat 的卡片行（由 01 负责格式化成 5 位小数）。
    def sectyp_line(self) -> str:
        """$FINSET 里的 SECTYP 卡片行。"""
        raise NotImplementedError

    def finset_extra_items(self) -> list[tuple[str, list[float]]]:
        """$FINSET 里额外要写的数组，[(变量名, [根, 梢])]；NACA 返回空。"""
        return []

    def control_cards(self) -> list[str]:
        """文件末尾的翼型控制卡；六边形不需要，返回空。"""
        return []

    # ---- 派生量 ----
    @property
    def thickness_ratio(self) -> float:
        """最大厚度 / 弦长。"""
        raise NotImplementedError

    @property
    def max_thickness_fraction(self) -> float:
        """最大厚度所在弦向位置 / 弦长。"""
        raise NotImplementedError

    def max_offset_ratio(self) -> float:
        """表面偏离弦线的最大距离 / 弦长。"""
        return 0.5 * self.thickness_ratio

    def section_area_ratio(self) -> float:
        """翼型截面积 / 弦长²。"""
        x = np.linspace(0.0, 1.0, _SAMPLING_GRID)
        band = 2.0 * self.half_thickness_array(x)
        integrate = getattr(np, "trapezoid", None) or np.trapz
        return float(integrate(band, x))

    def leading_edge_radius_ratio(self) -> float:
        """前缘半径 / 弦长（六边形返回尖楔内切圆半径，仅用于点云加密带宽）。"""
        raise NotImplementedError

    def describe(self) -> str:
        """中文说明，写进几何核对报告和控制台摘要。"""
        raise NotImplementedError

    # ---- 外形方程 ----
    def half_thickness_array(self, x):
        """半厚度 y/c 的向量版，x 为 0..1 的数组或标量。"""
        raise NotImplementedError

    def half_thickness_ratio_at(self, x: float) -> float:
        """半厚度 y/c，x 从 0（前缘）到 1（后缘）。"""
        return float(self.half_thickness_array(np.asarray([x], dtype=float))[0])

    def surface_offsets(self, x: float) -> tuple[float, float, float, float]:
        """返回上下表面的 (x_u, y_u, x_l, y_l)，都相对弦长。"""
        yt = self.half_thickness_ratio_at(x)
        x = float(x)
        return x, yt, x, -yt

    def interior_xy(self, x: float, s: float) -> tuple[float, float]:
        """厚度方向参数 s∈[-1, 1]。+1 为上表面，-1 为下表面。返回 (x/c, y/c)。"""
        return float(x), s * self.half_thickness_ratio_at(x)

    def sample_interior(self, count: int, rng) -> tuple[np.ndarray, np.ndarray]:
        """按截面积均匀抽取截面内的点，返回 (x/c, y/c)。"""
        if count <= 0:
            empty = np.zeros(0)
            return empty, empty
        grid, cdf = _sampling_lut(self)
        pick = rng.random(count)
        index = np.searchsorted(cdf, pick, side="right") - 1
        index = np.clip(index, 0, len(grid) - 2)
        span = grid[index + 1] - grid[index]
        x = grid[index] + rng.random(count) * span
        s = rng.random(count) * 2.0 - 1.0
        return x, s * self.half_thickness_array(x)

    # ---- 记录 ----
    def as_dict(self) -> dict:
        raise NotImplementedError


@lru_cache(maxsize=16)
def _sampling_lut(section) -> tuple[np.ndarray, np.ndarray]:
    """按截面积积分的累积分布，供 sample_interior 反查弦向位置。"""
    grid = np.linspace(0.0, 1.0, _SAMPLING_GRID)
    half = np.asarray(section.half_thickness_array(grid), dtype=float)
    weights = 0.5 * (half[:-1] + half[1:]) * np.diff(grid)
    total = float(weights.sum())
    if total <= 0.0:
        cdf = np.linspace(0.0, 1.0, len(weights))
    else:
        cdf = np.cumsum(weights) / total
    return grid, cdf


# =============================================================================
# 六边形截面
# =============================================================================
@dataclass(frozen=True)
class HexSection(FinSection):
    """对称六边形截面。DATCOM 侧 SECTYP = HEX，参数直接写进 $FINSET。"""

    # 三个三个参数取本文件顶部 1.2 配置区（这里不带默认值，避免第二份来源）。
    thickness_percent: float
    flat_percent: float
    le_radius: float

    # 系数表里自证用的四列：(as_dict 的键, 中文表头)
    CSV_COLUMNS = (
        ("designation", "舵面翼型"),
        ("thickness_percent", "翼型相对厚度(%)"),
        ("flat_percent", "翼型平顶长度(%)"),
        ("le_radius", "翼型前缘LER"),
    )

    def __post_init__(self) -> None:
        thickness = round(float(self.thickness_percent) + 1e-12, 2)
        if not math.isfinite(thickness) or thickness <= 0.0 or thickness >= 40.0:
            raise ValueError("thickness_percent 必须是大于 0、小于 40 的有限数。")
        flat = round(float(self.flat_percent) + 1e-12, 2)
        if not math.isfinite(flat) or flat < 0.0 or flat >= 100.0:
            raise ValueError("flat_percent 必须大于等于 0、小于 100 的有限数。")
        le_radius = float(self.le_radius)
        if not math.isfinite(le_radius) or le_radius < 0.0:
            raise ValueError("le_radius 必须是大于等于 0 的有限数。")
        object.__setattr__(self, "thickness_percent", thickness)
        object.__setattr__(self, "flat_percent", flat)
        object.__setattr__(self, "le_radius", le_radius)

    # ---- 派生量 ----
    @property
    def thickness_ratio(self) -> float:
        return self.thickness_percent / 100.0

    @property
    def flat_fraction(self) -> float:
        """平顶段长度 / 弦长，即 DATCOM 的 LFLATU。"""
        return self.flat_percent / 100.0

    @property
    def max_thickness_position(self) -> float:
        """平顶起点 / 弦长，即 DATCOM 的 LMAXU；对称六边形等于 (1 - LFLATU)/2。"""
        return 0.5 * (1.0 - self.flat_fraction)

    @property
    def max_thickness_fraction(self) -> float:
        """最大厚度起点 / 弦长（平顶开头）。"""
        return self.max_thickness_position

    @property
    def half_thickness_ratio(self) -> float:
        """最大半厚 / 弦长，即 DATCOM 的 ZUPPER。"""
        return 0.5 * self.thickness_ratio

    @property
    def le_wedge_deg(self) -> float:
        """前缘半楔角（度）。tan(θ) = ZUPPER / LMAXU。"""
        return math.degrees(
            math.atan2(self.half_thickness_ratio, self.max_thickness_position)
        )

    def max_offset_ratio(self) -> float:
        return self.half_thickness_ratio

    def leading_edge_radius_ratio(self) -> float:
        """几何前缘的内切圆半径 / 弦长。

        六边形前缘在 CAD 里是尖点，这里给的是"楔尖里能塞下的最大圆"半径，
        只用于点云前缘加密带宽，不是 DATCOM 的 LER，也不代表真实倒圆。
        """
        sin_t = math.sin(math.radians(self.le_wedge_deg))
        cos_t = math.cos(math.radians(self.le_wedge_deg))
        return self.half_thickness_ratio * sin_t / (sin_t + cos_t)

    def describe(self) -> str:
        return (
            f"{self.designation()}（对称六边形）：最大厚度 {self.thickness_percent:g}% 弦长，"
            f"平顶 {self.flat_fraction:.3f} 弦长"
            f"（{self.max_thickness_position:.3f}→{self.max_thickness_position + self.flat_fraction:.3f}），"
            f"前缘半楔角 {self.le_wedge_deg:.2f}°，LER = {self.le_radius:g}。"
        )

    # ---- DATCOM 卡片 ----
    def sectyp_line(self) -> str:
        return " SECTYP = HEX,"

    def finset_extra_items(self) -> list[tuple[str, list[float]]]:
        """四个数组：根、梢同值（等 t/c）。"""
        values = {
            "LER": self.le_radius,
            "ZUPPER": self.half_thickness_ratio,
            "LMAXU": self.max_thickness_position,
            "LFLATU": self.flat_fraction,
        }
        return [(name, [value, value]) for name, value in values.items()]

    # ---- 外形方程 ----
    def half_thickness_array(self, x):
        x = np.asarray(x, dtype=float)
        lmax = self.max_thickness_position
        flat = self.flat_fraction
        aft = 1.0 - lmax - flat
        rising = np.clip(x / lmax, 0.0, 1.0)
        falling = np.clip((1.0 - x) / aft, 0.0, 1.0)
        y = np.where(x <= lmax, rising, np.where(x <= lmax + flat, 1.0, falling))
        return self.half_thickness_ratio * np.clip(y, 0.0, 1.0)

    def section_area_ratio(self) -> float:
        """解析值：ZUPPER × (1 + LFLATU)。"""
        return self.half_thickness_ratio * (1.0 + self.flat_fraction)

    # ---- 记录 ----
    def designation(self) -> str:
        """记录里用的截面编号，例如 ``HEX 4.48%-52.4%``。"""
        return f"HEX {self.thickness_percent:g}%-{self.flat_percent:g}%"

    def as_dict(self) -> dict:
        return {
            "type": "HEX",
            "designation": self.designation(),
            "thickness_percent": self.thickness_percent,
            "flat_percent": self.flat_percent,
            "le_radius": self.le_radius,
            "thickness_ratio": self.thickness_ratio,
            "flat_fraction": self.flat_fraction,
            "half_thickness_ratio": self.half_thickness_ratio,
            "max_thickness_position": self.max_thickness_position,
            "le_wedge_deg": self.le_wedge_deg,
            "section_area_ratio": self.section_area_ratio(),
            "leading_edge_radius_ratio": self.leading_edge_radius_ratio(),
        }


# =============================================================================
# NACA 四位数截面（可带修正前缘和最大厚度位置）
# =============================================================================
# 参考厚度 20% 弦长上的半厚度方程，最后再按实际厚度线性缩放。
# 后缘斜率和后缘半厚度取自 NACA 修正四位数的边界条件。
_REFERENCE_THICKNESS = 0.20
_REFERENCE_HALF_THICKNESS = 0.10
_REFERENCE_TE_HALF = 0.002
_REFERENCE_A0 = 0.2969
_TE_SLOPE_BY_TENTHS = {
    2: 0.200,
    3: 0.234,
    4: 0.315,
    5: 0.465,
    6: 0.700,
}


def _solve_linear(matrix, rhs):
    """实数线性方程组，高斯消元。matrix 是方阵。"""
    size = len(rhs)
    rows = [list(matrix[i]) + [rhs[i]] for i in range(size)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(rows[row][col]))
        if abs(rows[pivot][col]) < 1e-14:
            raise ValueError("NACA 厚度方程无解，请调整前缘半径指数或最大厚度位置。")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        scale = rows[col][col]
        for col_j in range(col, size + 1):
            rows[col][col_j] /= scale
        for row in range(size):
            if row == col:
                continue
            factor = rows[row][col]
            for col_j in range(col, size + 1):
                rows[row][col_j] -= factor * rows[col][col_j]
    return [rows[i][size] for i in range(size)]


@lru_cache(maxsize=16)
def _reference_coefficients(le_radius_index, max_thickness_tenths):
    """20% 厚度参考翼型的前后两段多项式系数。"""
    xm = max_thickness_tenths / 10.0
    a0 = _REFERENCE_A0 * (le_radius_index / 6.0)
    d0 = _REFERENCE_TE_HALF
    d1 = _TE_SLOPE_BY_TENTHS[max_thickness_tenths]
    z = 1.0 - xm
    d2, d3 = _solve_linear(
        [
            [z * z, z * z * z],
            [2.0 * z, 3.0 * z * z],
        ],
        [0.10 - d0 - d1 * z, -d1],
    )
    curvature = 2.0 * d2 + 6.0 * d3 * z
    root = math.sqrt(xm)
    a1, a2, a3 = _solve_linear(
        [
            [xm, xm * xm, xm ** 3],
            [1.0, 2.0 * xm, 3.0 * xm * xm],
            [0.0, 2.0, 6.0 * xm],
        ],
        [
            _REFERENCE_HALF_THICKNESS - a0 * root,
            -0.5 * a0 / root,
            curvature + 0.25 * a0 * xm ** -1.5,
        ],
    )
    return (a0, a1, a2, a3, d0, d1, d2, d3, xm)


def _reference_half_array(x, xm, a0, a1, a2, a3, d0, d1, d2, d3):
    x = np.asarray(x, dtype=float)
    y = np.empty(x.shape, dtype=float)
    forward = x <= xm
    xf = x[forward]
    y[forward] = a0 * np.sqrt(np.maximum(xf, 0.0)) + a1 * xf + a2 * xf ** 2 + a3 * xf ** 3
    z = 1.0 - x[~forward]
    y[~forward] = d0 + d1 * z + d2 * z ** 2 + d3 * z ** 3
    return y


@dataclass(frozen=True)
class NacaSection(FinSection):
    """对称 NACA 四位数翼型。DATCOM 侧 SECTYP = NACA，截面写在末尾控制卡。"""

    # 三个三个参数取本文件顶部 1.2 配置区（这里不带默认值，避免第二份来源）。
    thickness_percent: float
    le_radius_index: int
    max_thickness_tenths: int

    CSV_COLUMNS = (
        ("designation", "舵面翼型"),
        ("thickness_percent", "翼型相对厚度(%)"),
        ("le_radius_index", "翼型前缘半径指数"),
        ("max_thickness_tenths", "翼型最大厚度位置(1/10弦长)"),
    )

    def __post_init__(self) -> None:
        le_index = _exact_int("le_radius_index", self.le_radius_index, 0, 9)
        tenths = _exact_int("max_thickness_tenths", self.max_thickness_tenths, 2, 6)
        thickness = round(float(self.thickness_percent) + 1e-12, 2)
        if not math.isfinite(thickness) or thickness <= 0.0 or thickness >= 40.0:
            raise ValueError("thickness_percent 必须是大于 0、小于 40 的有限数。")
        if tenths not in _TE_SLOPE_BY_TENTHS:
            raise ValueError("max_thickness_tenths 只允许 2、3、4、5、6。")
        object.__setattr__(self, "le_radius_index", le_index)
        object.__setattr__(self, "max_thickness_tenths", tenths)
        object.__setattr__(self, "thickness_percent", thickness)

    # ---- 派生量 ----
    @property
    def thickness_ratio(self) -> float:
        return self.thickness_percent / 100.0

    @property
    def max_thickness_fraction(self) -> float:
        return self.max_thickness_tenths / 10.0

    def leading_edge_radius_ratio(self) -> float:
        """前缘半径 / 弦长。指数为 0 时半径为 0。"""
        scale = self.le_radius_index / 6.0
        return 1.1019 * (scale * self.thickness_ratio) ** 2

    def describe(self) -> str:
        return (
            f"NACA {self.designation_body()}（对称）：最大厚度 {self.thickness_percent:g}% 弦长，"
            f"位于 {self.max_thickness_fraction:.1f} 弦长；前缘半径指数 {self.le_radius_index}。"
        )

    # ---- DATCOM 卡片 ----
    def sectyp_line(self) -> str:
        return " SECTYP = NACA,"

    def control_cards(self) -> list[str]:
        return [self.control_card(1), self.control_card(2)]

    def designation_body(self) -> str:
        """控制卡里翼型名本身，不含组号。例如 ``0004.48-63``。"""
        thickness = f"{self.thickness_percent:05.2f}"
        return f"00{thickness}-{self.le_radius_index}{self.max_thickness_tenths}"

    def control_card(self, finset_index: int) -> str:
        """Missile DATCOM 的 NACA 控制卡。finset_index 为 1–4。"""
        if finset_index not in (1, 2, 3, 4):
            raise ValueError(f"舵面组号必须是 1–4，收到 {finset_index}。")
        return f"NACA-{finset_index}-4-{self.designation_body()}"

    # ---- 外形方程 ----
    def half_thickness_array(self, x):
        coeffs = _reference_coefficients(self.le_radius_index, self.max_thickness_tenths)
        a0, a1, a2, a3, d0, d1, d2, d3, xm = coeffs
        y = _reference_half_array(x, xm, a0, a1, a2, a3, d0, d1, d2, d3)
        return np.maximum(y, 0.0) * (self.thickness_ratio / _REFERENCE_THICKNESS)

    # ---- 记录 ----
    def designation(self) -> str:
        return f"NACA {self.designation_body()}"

    def as_dict(self) -> dict:
        return {
            "type": "NACA",
            "designation": self.designation_body(),
            "thickness_percent": self.thickness_percent,
            "le_radius_index": self.le_radius_index,
            "max_thickness_tenths": self.max_thickness_tenths,
            "thickness_ratio": self.thickness_ratio,
            "max_thickness_fraction": self.max_thickness_fraction,
            "section_area_ratio": self.section_area_ratio(),
            "leading_edge_radius_over_chord": self.leading_edge_radius_ratio(),
        }


def _exact_int(name, value, low, high):
    number = float(value)
    if not math.isfinite(number) or abs(number - round(number)) > 1e-9:
        raise ValueError(f"{name} 必须是整数，收到 {value!r}。")
    number = int(round(number))
    if number < low or number > high:
        raise ValueError(f"{name} 必须在 {low} 到 {high} 之间，收到 {number}。")
    return number


# =============================================================================
# 5. 由配置区派生的对象与解析入口（不用改）
# =============================================================================
def make_fin_section(section_type=None, *, thickness_percent=None, flat_percent=None,
                     le_radius=None, le_radius_index=None,
                     max_thickness_tenths=None) -> FinSection:
    """按种类与参数构造舵面截面；缺的参数取配置区默认值。

    `01_batch_datcom.py` 按输入表每一行的翼型种类调用它，所以同一批里可以六边形与
    NACA 混跑。模块顶部的 `FIN_SECTION` 只是"没给参数时"的默认值，不再是唯一来源。
    """
    kind = str(SECTION_TYPE if section_type is None else section_type).strip().upper()
    if kind == "HEX":
        return HexSection(
            HEX_THICKNESS_PERCENT if thickness_percent is None else thickness_percent,
            HEX_FLAT_PERCENT if flat_percent is None else flat_percent,
            HEX_LE_RADIUS if le_radius is None else le_radius,
        )
    if kind == "NACA":
        return NacaSection(
            NACA_THICKNESS_PERCENT if thickness_percent is None else thickness_percent,
            NACA_LE_RADIUS_INDEX if le_radius_index is None else le_radius_index,
            NACA_MAX_THICKNESS_TENTHS
            if max_thickness_tenths is None else max_thickness_tenths,
        )
    raise ValueError(f"不支持的 SECTION_TYPE {kind!r}，仅支持 HEX / NACA。")


def _build_fin_section() -> FinSection:
    """按配置区构造默认截面；01 与 04 共用这一份。"""
    return make_fin_section()


# 当前舵面截面（前舵后舵共用）。01 写进 $FINSET / 控制卡，04 按它建舵面外形。
FIN_SECTION = _build_fin_section()


def fin_roll_angles(fin_phase=FIN_PHASE, count=CRUCIFORM_COUNT):
    """舵面组的滚转角列表（度）：'+' 从 0° 起，'x' 从 45° 起，均分 360°。"""
    if not isinstance(count, int) or count < 2 or count % 2 != 0:
        raise ValueError(f"CRUCIFORM_COUNT 必须是大于等于 2 的偶数，收到 {count!r}。")
    if fin_phase == "+":
        start = 0.0
    elif fin_phase == "x":
        start = 45.0
    else:
        raise ValueError(f"不支持的 FIN_PHASE {fin_phase!r}，仅支持 '+' 或 'x'。")
    return [start + 360.0 * index / count for index in range(count)]


# 点云脚本按这个元组布 4 片舵；与 01 写进 PHIF 的角度是同一组数。
FIN_AZIMUTH_DEG = tuple(fin_roll_angles())
