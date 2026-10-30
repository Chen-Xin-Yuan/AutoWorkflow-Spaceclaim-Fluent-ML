# -*- coding: utf-8 -*-
"""导弹外形的不公开设计数据 + 鼻锥头型解析式（01 批量 DATCOM 与 04 点云共用）。

这里集中放**不进** ``data_lib/inputs/cases.csv`` 的那部分数据 —— 它们属于外形
设计本身，不该由调用方每次填写：

    1. 基准几何 BASELINE_GEOMETRY：README「几何参数定义与范围」那套 11 个主尺寸，
       既是演示算例的默认值，也是 04 点云的默认外形、报告里「与基准几何差异」的参照；
    2. 鼻锥：NOSE_SHAPE（切线卵形 / 圆锥）、锥顶倒角比例、母线离散段数；
    3. 舵面布局：LE_MODE / FIN_PHASE / CRUCIFORM_COUNT 与派生出的方位角。

舵面翼型不在这里：它自成一套（含公式）放在 ``fin_section.py``，本模块只把当前
选择 ``FIN_SECTION`` 原样转出，避免维护第二份。DATCOM 的协议文件名、单位换算、
11 个参数的校验放在 ``datcom_spec.py``（那是"怎么和 datcom.exe 说话"，不是外形）。

改一处两边同时生效：

    NOSE_SHAPE = "ogive"    →  01 写 $AXIBOD TNOSE = OGIVE，04 按同一条卵形母线建点云
    NOSE_SHAPE = "cone"     →  01 写 TNOSE = CONE，04 按直线圆锥母线建点云

本模块没有文件输入输出：只有常量、纯函数与解析式，路径由调用方给出。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from fin_section import FIN_SECTION  # 转出给 01/04，翼型参数的唯一来源仍在 fin_section.py

# =============================================================================
# 1. 参数配置区（不公开的设计数据；改一处 01 与 04 同时生效）
# =============================================================================
# ---- 鼻锥头型 ----
# "ogive" = 切线卵形（DATCOM TNOSE = OGIVE，也是不写 TNOSE 时的默认头型）
# "cone"  = 直线圆锥（DATCOM TNOSE = CONE）
# 注意：DATCOM 只认 LNOSE / DNOSE，头型靠这条卡片；点云脚本用同一个头型建母线。
NOSE_SHAPE = "ogive"

# 锥顶倒角球冠半径 = 比例 × 弹长（弹长的 0.4%，约 1.5 cm）。
# 只有点云 / CFD 用：DATCOM 的头锥是数学尖点，不建倒角。置 0 即退化成尖头。
NOSE_TIP_FILLET_RATIO = 0.004
# 直接给倒角半径（m）时覆盖上面的比例；None = 用比例。
NOSE_TIP_FILLET_RADIUS = None
# 头锥母线离散段数（网格与折线用）。
NOSE_SEGMENTS = 120

# ---- 舵面布局 ----
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
# 3. 舵面方位角（由 FIN_PHASE / CRUCIFORM_COUNT 派生，01 与 04 共用）
# =============================================================================
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


# =============================================================================
# 4. 鼻锥头型：DATCOM 卡片文本 + 母线 / 侧面积 / 相切点解析式
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
