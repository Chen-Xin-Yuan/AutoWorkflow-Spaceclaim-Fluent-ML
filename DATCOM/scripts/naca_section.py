# -*- coding: utf-8 -*-
"""舵面翼型：对称截面，可切 NACA 四位数（``NACA-1-4-...``）或六边形（``SECTYP = HEX``）。

批量 DATCOM 和点云脚本都从这里取同一副截面，避免两套舵面外形不一致。
前舵和后舵共用同一副截面，根部和梢部按当地弦长等比缩放（等 t/c）。
上下对称，中弧与弦线重合，前缘和后缘都按所选截面的公式收成尖点。

两种截面怎么进 DATCOM：

    NACA（SECTYP = NACA，截面写在文件末尾的控制卡上）
        $FINSET1
         SECTYP = NACA,
         ...
        $END
        ...
        NACA-1-4-0004.48-63
        NACA-2-4-0004.48-63

    HEX（SECTYP = HEX，截面参数直接进 $FINSET，根/梢各一个值）
        $FINSET1
         SECTYP = HEX,
         ...
         STA = 1.0,
         LER = 0.01500,0.01500,
         ZUPPER = 0.02240,0.02240,
         LMAXU = 0.23800,0.23800,
         LFLATU = 0.52400,0.52400,
        $END

``datcom_bin/for005.dat`` 是 DATCOM 自带的历史六边形算例，六边形的默认值
（0.02238 / 0.238 / 0.524 / 0.015）就照它取的。

改翼型只改下面「参数配置区」：先选 ``SECTION_TYPE``，再改该类型对应的三个默认值。
01/04 两个脚本都读这一份，不需要改别处。

六边形参数的实测口径（本机 datcom.exe REV 03/11，用临时算例逐项验证）：
    * ZUPPER 是半厚 / 弦长：乘 2 后 CD 由 0.659 升到 0.916，乘 10 后 CD 11.6、
      CL 掉到 0.838；填 0.6（总厚 120% 弦长）直接算不出结果。
    * LMAXU 是平顶起点，不是平顶中点：因为 (1 - 0.524) / 2 = 0.238 正好等于
      历史算例给的 LMAXU，前缘斜坡与后缘斜坡等长。
    * 必须满足 LMAXU + LFLATU <= 1：填 0.238 + 0.8 时没有任何静态气动页。
    * 参数行漏写不会报错，DATCOM 会静默用默认截面，所以写完要核对 for005.dat。
    * for022.dat 里画的舵面截面恒为 5% 菱形示意，不随参数变化，不能用来核对。

无文件输入输出：本模块只提供截面定义、外形方程和卡片文本，路径与产物由 01/04 负责。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

# =============================================================================
# 参数配置区（默认翼型；没有文件读写，可调项只有下面这几个）
# =============================================================================
# 截面类型："HEX"（对称六边形，DATCOM SECTYP = HEX）或 "NACA"（对称四位翼型）。
SECTION_TYPE = "HEX"

# ---- 六边形参数（SECTION_TYPE = "HEX" 时生效）---
# 最大厚度，弦长百分数。DATCOM 卡里 ZUPPER = thickness_percent / 200。
HEX_THICKNESS_PERCENT = 4.48
# 平顶段长度，弦长百分数。LFLATU = flat_percent / 100，
# 对称六边形的前缘斜坡终点 LMAXU = (1 - LFLATU) / 2。
HEX_FLAT_PERCENT = 52.4
# DATCOM 的 LER 卡片值（前缘圆角参数），原样写入，只影响 DATCOM 的气动结果。
# 口径（绝对英寸还是弦长分数）没能从 DATCOM 手册确认；实测它只改变阻力：
# 0 -> CD 0.330，0.015 -> 0.349，0.05 -> 0.371，0.15 -> 0.451，0.5 以上饱和。
HEX_LE_RADIUS = 0.015

# ---- NACA 四位数参数（SECTION_TYPE = "NACA" 时生效）----
# 最大厚度，弦长的百分数，写卡时保留两位小数。
NACA_THICKNESS_PERCENT = 4.48
# 前缘半径指数，整数 0–9。0 是尖前缘，6 是标准圆前缘。
NACA_LE_RADIUS_INDEX = 6
# 最大厚度位置，单位是 1/10 弦长，只允许 2、3、4、5、6。
NACA_MAX_THICKNESS_TENTHS = 3

# 面积均匀抽样用的弦向网格点数，只影响点云抽样的平滑度。
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

    thickness_percent: float = HEX_THICKNESS_PERCENT
    flat_percent: float = HEX_FLAT_PERCENT
    le_radius: float = HEX_LE_RADIUS

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

    thickness_percent: float = NACA_THICKNESS_PERCENT
    le_radius_index: int = NACA_LE_RADIUS_INDEX
    max_thickness_tenths: int = NACA_MAX_THICKNESS_TENTHS

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
# 默认截面与覆盖入口
# =============================================================================
# 两套舵面共用这一份。点云和 DATCOM 都导入它。
FIN_SECTION = HexSection() if SECTION_TYPE.upper() == "HEX" else NacaSection()


def resolve_fin_section(value=None):
    """None 用默认截面；字典只覆盖本类型的字段（01 的 --demo CASES 用它）。"""
    if value is None or value is FIN_SECTION:
        return FIN_SECTION
    if isinstance(value, FinSection):
        return value
    if isinstance(value, dict):
        fields = tuple(section_field for section_field, _cn in FIN_SECTION.CSV_COLUMNS[1:])
        fields = tuple(name for name in fields if name != "designation")
        unknown = [key for key in value if key not in fields]
        if unknown:
            raise ValueError("fin_section 含不能识别的翼型参数：" + ", ".join(unknown))
        data = {key: getattr(FIN_SECTION, key) for key in fields}
        data.update(value)
        return type(FIN_SECTION)(**data)
    raise TypeError(
        f"fin_section 必须是 {type(FIN_SECTION).__name__} 或字典，收到 {type(value).__name__}。"
    )
