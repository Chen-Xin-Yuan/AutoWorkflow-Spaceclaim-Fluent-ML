# -*- coding: utf-8 -*-
"""NACA 四位数翼型（可带修正前缘和最大厚度位置）。

批量 DATCOM 和点云脚本都从这里读默认值，避免两套舵面外形不一致。
要改翼型，只改下面五个默认值。

DATCOM 控制卡写成 ``NACA-<组号>-4-<弯度><位置><厚度>-<前缘指数><厚度位置>``。
例如默认值对应 ``NACA-1-4-0004.48-63``：无弯度、厚度为弦长的 4.48%、
标准圆前缘、最大厚度在 30% 弦长、后缘按 NACA 公式收到尖点。

五个参数：
    camber_percent            最大中弧，弦长的百分数，整数 0–9。0 表示上下对称。
    camber_location_tenths    最大中弧距前缘的位置，单位是 1/10 弦长。
                              弯度为 0 时必须为 0；有弯度时为 1–9。
    thickness_percent         最大厚度，弦长的百分数，写卡时四舍五入到 0.01。
    le_radius_index           前缘半径指数，整数 0–9。0 是尖前缘，6 是标准圆前缘。
                              半径按 (指数/6)² 相对标准圆前缘缩放。
    max_thickness_tenths      最大厚度距前缘的位置，单位是 1/10 弦长，
                              只允许 2、3、4、5、6。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

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
    """20% 厚度参考翼型的前后两段多项式系数，以及弦向采样用的累积分布。"""
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
    grid = np.linspace(0.0, 1.0, 401)
    half = _reference_half_array(grid, xm, a0, a1, a2, a3, d0, d1, d2, d3)
    half = np.maximum(half, 0.0)
    weights = 0.5 * (half[:-1] + half[1:]) * np.diff(grid)
    total = float(weights.sum())
    if total <= 0.0:
        cdf = np.linspace(0.0, 1.0, len(weights))
    else:
        cdf = np.cumsum(weights) / total
    return (a0, a1, a2, a3, d0, d1, d2, d3, xm, grid, cdf)


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
class NacaSection:
    """一组舵面共用的 NACA 四位数翼型。"""

    camber_percent: int = 0
    camber_location_tenths: int = 0
    thickness_percent: float = 4.48
    le_radius_index: int = 6
    max_thickness_tenths: int = 3

    def __post_init__(self):
        camber = _exact_int("camber_percent", self.camber_percent, 0, 9)
        location = _exact_int(
            "camber_location_tenths", self.camber_location_tenths, 0, 9
        )
        le_index = _exact_int("le_radius_index", self.le_radius_index, 0, 9)
        station = _exact_int("max_thickness_tenths", self.max_thickness_tenths, 2, 6)
        thickness = round(float(self.thickness_percent) + 1e-12, 2)
        if not math.isfinite(thickness) or thickness <= 0.0 or thickness >= 40.0:
            raise ValueError("thickness_percent 必须是大于 0、小于 40 的有限数。")
        if station not in _TE_SLOPE_BY_TENTHS:
            raise ValueError("max_thickness_tenths 只允许 2、3、4、5、6。")
        if camber == 0 and location != 0:
            raise ValueError("弯度为 0 时，camber_location_tenths 必须为 0。")
        if camber != 0 and location == 0:
            raise ValueError("有弯度时，camber_location_tenths 必须是 1–9。")
        object.__setattr__(self, "camber_percent", camber)
        object.__setattr__(self, "camber_location_tenths", location)
        object.__setattr__(self, "le_radius_index", le_index)
        object.__setattr__(self, "max_thickness_tenths", station)
        object.__setattr__(self, "thickness_percent", thickness)

    @property
    def thickness_ratio(self) -> float:
        """最大厚度 / 弦长。"""
        return self.thickness_percent / 100.0

    @property
    def camber_ratio(self) -> float:
        return self.camber_percent / 100.0

    @property
    def camber_location(self) -> float:
        return self.camber_location_tenths / 10.0

    @property
    def max_thickness_fraction(self) -> float:
        return self.max_thickness_tenths / 10.0

    def designation_body(self) -> str:
        """控制卡里翼型名本身，不含组号。例如 ``0004.48-63``。"""
        thickness = f"{self.thickness_percent:05.2f}"
        return (
            f"{self.camber_percent}{self.camber_location_tenths}{thickness}"
            f"-{self.le_radius_index}{self.max_thickness_tenths}"
        )

    def control_card(self, finset_index: int) -> str:
        """Missile DATCOM 的 NACA 控制卡。finset_index 为 1–4。"""
        if finset_index not in (1, 2, 3, 4):
            raise ValueError(f"舵面组号必须是 1–4，收到 {finset_index}。")
        return f"NACA-{finset_index}-4-{self.designation_body()}"

    def leading_edge_radius_ratio(self) -> float:
        """前缘半径 / 弦长。指数为 0 时半径为 0。"""
        scale = self.le_radius_index / 6.0
        return 1.1019 * (scale * self.thickness_ratio) ** 2

    def _scaled_half_array(self, x):
        coeffs = _reference_coefficients(self.le_radius_index, self.max_thickness_tenths)
        a0, a1, a2, a3, d0, d1, d2, d3, xm = coeffs[:9]
        y = _reference_half_array(x, xm, a0, a1, a2, a3, d0, d1, d2, d3)
        return np.maximum(y, 0.0) * (self.thickness_ratio / _REFERENCE_THICKNESS)

    def half_thickness_ratio(self, x: float) -> float:
        """对称厚度分布的半厚度 y/c。x 是平均弧线参数，0 为前缘，1 为后缘。"""
        return float(self._scaled_half_array(np.asarray([x], dtype=float))[0])

    def mean_line(self, x: float) -> tuple[float, float]:
        """返回 (中弧 y/c, dy/dx)。无弯度时都是 0。"""
        camber = self.camber_ratio
        location = self.camber_location
        if camber == 0.0:
            return 0.0, 0.0
        x = min(max(float(x), 0.0), 1.0)
        if x <= location:
            yc = camber / location ** 2 * (2.0 * location * x - x * x)
            slope = 2.0 * camber / location ** 2 * (location - x)
        else:
            tail = (1.0 - location) ** 2
            yc = camber / tail * ((1.0 - 2.0 * location) + 2.0 * location * x - x * x)
            slope = 2.0 * camber / tail * (location - x)
        return yc, slope

    def surface_offsets(self, x: float) -> tuple[float, float, float, float]:
        """给定弧线参数 x，返回上下表面的 (x_u, y_u, x_l, y_l)，都相对弦长。"""
        yt = self.half_thickness_ratio(x)
        yc, slope = self.mean_line(x)
        if slope == 0.0:
            return float(x), yc + yt, float(x), yc - yt
        theta = math.atan(slope)
        sine, cosine = math.sin(theta), math.cos(theta)
        return (
            x - yt * sine,
            yc + yt * cosine,
            x + yt * sine,
            yc - yt * cosine,
        )

    def interior_xy(self, x: float, s: float) -> tuple[float, float]:
        """厚度方向参数 s∈[-1, 1]。+1 为上表面，-1 为下表面。返回 (x/c, y/c)。"""
        yt = self.half_thickness_ratio(x)
        yc, slope = self.mean_line(x)
        theta = math.atan(slope) if slope else 0.0
        return (
            x - s * yt * math.sin(theta),
            yc + s * yt * math.cos(theta),
        )

    def sample_interior(self, count: int, rng) -> tuple[np.ndarray, np.ndarray]:
        """按截面积均匀抽取截面内的点，返回 (x/c, y/c)。"""
        if count <= 0:
            empty = np.zeros(0)
            return empty, empty
        coeffs = _reference_coefficients(self.le_radius_index, self.max_thickness_tenths)
        grid, cdf = coeffs[9], coeffs[10]
        pick = rng.random(count)
        index = np.searchsorted(cdf, pick, side="right") - 1
        index = np.clip(index, 0, len(grid) - 2)
        span = grid[index + 1] - grid[index]
        x = grid[index] + rng.random(count) * span
        s = rng.random(count) * 2.0 - 1.0
        yt = self._scaled_half_array(x)
        yc = np.empty(count)
        slope = np.empty(count)
        for i, value in enumerate(x):
            yc[i], slope[i] = self.mean_line(float(value))
        theta = np.arctan(slope)
        return x - s * yt * np.sin(theta), yc + s * yt * np.cos(theta)

    def section_area_ratio(self) -> float:
        """翼型截面积 / 弦长²。"""
        x = np.linspace(0.0, 1.0, 401)
        yt = self._scaled_half_array(x)
        slopes = np.array([self.mean_line(float(value))[1] for value in x])
        band = 2.0 * yt * np.sqrt(1.0 + slopes * slopes)
        integrate = getattr(np, "trapezoid", None) or np.trapz
        return float(integrate(band, x))

    def max_offset_ratio(self) -> float:
        """表面偏离弦线的最大距离 / 弦长。"""
        x = np.linspace(0.0, 1.0, 401)
        peak = 0.0
        for value in x:
            _, yu, _, yl = self.surface_offsets(float(value))
            peak = max(peak, abs(yu), abs(yl))
        return peak

    def as_dict(self) -> dict:
        return {
            "camber_percent": self.camber_percent,
            "camber_location_tenths": self.camber_location_tenths,
            "thickness_percent": self.thickness_percent,
            "le_radius_index": self.le_radius_index,
            "max_thickness_tenths": self.max_thickness_tenths,
            "designation": self.designation_body(),
            "thickness_ratio": self.thickness_ratio,
            "max_thickness_fraction": self.max_thickness_fraction,
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


def resolve_naca_section(value=None) -> NacaSection:
    """None 用默认翼型；字典只覆盖写出的字段。"""
    if value is None or value is NACA_SECTION:
        return NACA_SECTION
    if isinstance(value, NacaSection):
        return value
    if isinstance(value, dict):
        fields = (
            "camber_percent",
            "camber_location_tenths",
            "thickness_percent",
            "le_radius_index",
            "max_thickness_tenths",
        )
        unknown = [key for key in value if key not in fields]
        if unknown:
            raise ValueError(
                "fin_section 含不能识别的翼型参数：" + ", ".join(unknown)
            )
        data = {key: getattr(NACA_SECTION, key) for key in fields}
        data.update(value)
        return NacaSection(**data)
    raise TypeError(f"fin_section 必须是 NacaSection 或字典，收到 {type(value).__name__}。")


# 两套舵面共用这一份。点云和 DATCOM 都导入它。
NACA_SECTION = NacaSection()
