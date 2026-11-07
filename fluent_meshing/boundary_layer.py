# -*- coding: utf-8 -*-
"""按工况（马赫数 / 高度）算边界层首层高度与层数，供 Fluent Meshing 使用。

**为什么网格不能按几何共用**：边界层厚度是雷诺数的函数，而雷诺数随来流速度（马赫数）
与密度（高度）变化。同一外形在 Ma 0.8 海平面和 Ma 2 海平面的 y+ = 1 首层高度差了近
一个数量级，拿去共用一个网格，必有一个工况落在 y+ 目标之外——壁面函数和壁面解析对
y+ 的要求不同，算出来的摩阻与分离位置都会错。

本模块只做设计计算：输入几何总长 L 与工况 (mach, altitude_m)，输出首层高度、层数、
增长率以及全部中间量（Re / Cf / tau_w / u_tau / y / delta）。**没有任何文件读写、
不连数据库**，所以随时可以在命令行上单独算一遍复核。

-------------------------------------------------------------------------------
输入 / 输出 / 参数配置
-------------------------------------------------------------------------------
输入
    几何总长 L      米，取 `geometry` 表的 `L`（弹体总长，含头锥），由调用方传入。
    工况            `mach`（无量纲）、`altitude_m`（米，ISA 几何高度）。
                    网格按 `(geometry_id, condition_id)` 一行，工况来自 `condition` 表。
输出
    `BoundaryLayerDesign`（dataclass）：首层高度、层数、增长率，加上 Re / Cf / tau_w /
    u_tau / y / delta / 来流速度 / 密度 / 黏度 / 声速 / 温度 / 压力。
    该对象可 `asdict()` 直接写进 `run.json` 与 `mesh_task` 行做可追溯证据。
    本模块不写文件、不建目录：落盘由 `mesh_runner.py`（run.json）与 `db.py`
    （`mesh_task` 的 `first_height_mm / n_layers / growth_rate / y_plus_target /
    re / cf / u_tau / delta_m`）负责。
参数配置（要改数值只改这一节；命令行与调用方参数优先）
    Y_PLUS_TARGET / GROWTH_RATE / MAX_LAYERS / MIN_LAYERS / FIRST_CELL_FACTOR /
    CF_CORRELATION / DELTA_CORRELATION / ISA_* / SUTHERLAND_* 。
    `describe()` 会把当前取值与公式出处一起打印出来，便于在报告里贴证据。
覆盖优先级
    函数参数（`design_boundary_layer(L, mach, altitude_m, ...)`）> 本节默认值。
    `mesh_runner.py` 的 `--y-plus / --growth-rate` 等命令行选项会转成函数参数；
    没有任何环境变量入口。时间戳与路径都不涉及。
-------------------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# =============================================================================
# 1. 参数配置区
# =============================================================================
# ---- 目标与几何增长 ----
# y+ = 1 对应"壁面解析"（resolved sublayer）：k-ω SST 在 y+ ≲ 1 时直接积分到壁面，
# 不需要壁面函数。若改用壁面函数（k-ε 标准壁面函数）应取 y+ ≈ 30–100，改这里即可。
Y_PLUS_TARGET = 1.0
# 相邻层厚度比。1.2 是全章最常用的值：既能覆盖到边界层外缘，又不至于在第一层之后
# 立刻放大到与主流尺度相当。Fluent 的 prism 全局增长率也是 1.2（见 P_VOLUME_MESH）。
GROWTH_RATE = 1.2
# 层数夹取范围：低于 8 层覆盖不到 delta；上限 60 是"y+ = 1 且 L≈3.7 m、Ma 2 海平面"
# 这类最苛刻组合所需的层数（h1 ≈ 0.0017 mm 时要 ~46 层才堆到 delta 30 mm），再高
# 说明首层高度与 delta 差得太离谱，应该先怀疑输入而不是继续加层。
MIN_LAYERS = 8
MAX_LAYERS = 60
# 首层高度相对"壁面到第一层质心距离"的倍数。Fluent 的 FirstHeight 是**第一层单元
# 的高度**，而 y+ 公式给出的是到**质心**的距离；一层单元内质心约在 0.5h 处，
# 所以 h ≈ 2·y。Ansys 的边界层教程也用同一换算。
FIRST_CELL_FACTOR = 2.0

# ---- 关联式（出处见各常量注释与 `describe()`）----
# 平板湍流摩阻系数：Cf = 0.058 · Re^-0.2
# 出处：Schlichting, "Boundary-Layer Theory", 7th ed., Ch. 21（平板湍流、Re < 1e9 的
#       常用工程关联式，等价于 Cf = 0.074 Re^-0.2 的一个略低变体）；Ansys Fluent
#       Theory Guide 的 "Turbulent Boundary Layer" 一节引用同一形式的 1/5 次幂律。
CF_CORRELATION = "Cf = 0.058 * Re**-0.2  (Schlichting, Boundary-Layer Theory, 7th ed., Ch.21)"
CF_COEFFICIENT = 0.058
CF_EXPONENT = -0.2
# 湍流边界层厚度：delta ≈ 0.37 · L · Re^-0.2（1/5 次幂律、零攻角平板）
# 出处：Schlichting, Ch. 21，"turbulent boundary layer over a flat plate" 厚度公式；
#       Ansys Knowledge 的 boundary-layer sizing 文章按 0.37·x·Re_x^-0.2 给出。
DELTA_CORRELATION = "delta = 0.37 * L * Re**-0.2  (flat-plate 1/5-power law, Schlichting Ch.21)"
DELTA_COEFFICIENT = 0.37
DELTA_EXPONENT = -0.2

# ---- ISA 标准大气（ISO 2533 / U.S. Standard Atmosphere 1976，对流层）----
# 只覆盖 0–11 km 的对流层。11 km 以上改用平流层公式；本项目工况在 0–10 km，
# 超出 11 km 直接报错，不做静默外推。
ISA_SEA_LEVEL_TEMPERATURE_K = 288.15
ISA_SEA_LEVEL_PRESSURE_PA = 101325.0
ISA_TEMPERATURE_LAPSE_K_PER_M = 0.0065      # 每米下降 6.5 K
ISA_TROPOPAUSE_M = 11000.0
ISA_GAS_CONSTANT_AIR = 287.05               # J/(kg·K)
ISA_GAMMA = 1.4
ISA_GRAVITY = 9.80665                       # m/s^2

# ---- Sutherland 黏度定律（空气，SI）----
# mu = mu_ref * (T/T_ref)^1.5 * (T_ref + S) / (T + S)
# 出处：Sutherland (1893)；Ansys Fluent Theory Guide 的 "Viscosity" 一节给出同一组
#       空气常数（T_ref = 273.15 K，mu_ref = 1.716e-5 Pa·s，S = 110.4 K）。
SUTHERLAND_T_REF_K = 273.15
SUTHERLAND_MU_REF = 1.716e-5
SUTHERLAND_S_K = 110.4
SUTHERLAND_EXPONENT = 1.5

# 量级下限：数值退化（mach = 0 或 L = 0）时不去猜，直接报错。
MIN_REYNOLDS = 1.0


class BoundaryLayerError(ValueError):
    """输入超出本模块的适用范围（负高度、超出对流层、非有限值等）。"""


# =============================================================================
# 2. 物性：ISA 标准大气 + Sutherland 黏度
# =============================================================================
def isa_atmosphere(altitude_m: float) -> dict:
    """ISA 对流层（0–11 km）：返回温度 / 压力 / 密度 / 声速。

    公式（ISO 2533、U.S. Standard Atmosphere 1976）：
        T = T0 - a·h
        p = p0 · (T/T0)^(g/(a·R))
        rho = p / (R·T)
        a_sound = sqrt(gamma·R·T)
    """
    h = float(altitude_m)
    if not math.isfinite(h) or h < 0.0:
        raise BoundaryLayerError(f"高度必须是非负有限值，收到 {altitude_m!r}")
    if h > ISA_TROPOPAUSE_M:
        raise BoundaryLayerError(
            f"高度 {h:g} m 超出本模块的对流层公式（上限 {ISA_TROPOPAUSE_M:g} m）。"
            "要用平流层工况，先在本文件补平流层分段公式，不要静默外推。")
    temperature = ISA_SEA_LEVEL_TEMPERATURE_K - ISA_TEMPERATURE_LAPSE_K_PER_M * h
    exponent = ISA_GRAVITY / (ISA_TEMPERATURE_LAPSE_K_PER_M * ISA_GAS_CONSTANT_AIR)
    pressure = ISA_SEA_LEVEL_PRESSURE_PA * (temperature / ISA_SEA_LEVEL_TEMPERATURE_K) ** exponent
    density = pressure / (ISA_GAS_CONSTANT_AIR * temperature)
    sound_speed = math.sqrt(ISA_GAMMA * ISA_GAS_CONSTANT_AIR * temperature)
    return {
        "temperature_k": temperature,
        "pressure_pa": pressure,
        "density_kg_m3": density,
        "sound_speed_m_s": sound_speed,
    }


def sutherland_viscosity(temperature_k: float) -> float:
    """空气动力黏度（Pa·s），Sutherland 定律。"""
    t = float(temperature_k)
    if not math.isfinite(t) or t <= 0.0:
        raise BoundaryLayerError(f"温度必须是正有限值，收到 {temperature_k!r}")
    return (SUTHERLAND_MU_REF * (t / SUTHERLAND_T_REF_K) ** SUTHERLAND_EXPONENT
            * (SUTHERLAND_T_REF_K + SUTHERLAND_S_K) / (t + SUTHERLAND_S_K))


# =============================================================================
# 3. 设计计算
# =============================================================================
@dataclass(frozen=True)
class BoundaryLayerDesign:
    """一次边界层设计的结果：设计值 + 全部中间量（都可写进 run.json / mesh_task）。"""

    # ---- 输入 ----
    geometry_length_m: float
    mach: float
    altitude_m: float
    y_plus_target: float
    growth_rate: float
    # ---- 物性 ----
    temperature_k: float
    pressure_pa: float
    density_kg_m3: float
    sound_speed_m_s: float
    velocity_m_s: float
    viscosity_pa_s: float
    # ---- 关联式中间量 ----
    re: float
    cf: float
    tau_w_pa: float
    u_tau_m_s: float
    y_m: float                 # 到 y+ 目标距离（质心）
    delta_m: float             # 湍流边界层厚度
    # ---- 设计输出 ----
    first_height_mm: float     # Fluent 的 FirstHeight（第一层单元高度）
    n_layers: int
    bl_thickness_mm: float     # 设计层堆叠到的总厚度 = h1·(r^n−1)/(r−1)
    capped: str                # "" / "min" / "max"：层数是否被夹取
    growth_rate_limited: bool  # 层数被上限夹住后，实际增长率被放大的情况

    def as_dict(self) -> dict:
        return asdict(self)


def layer_stack_height(first_height_mm: float, n_layers: int, growth_rate: float) -> float:
    """等比层堆叠的总厚度 h1·(r^n − 1)/(r − 1)；r = 1 时退化为 n·h1。"""
    if n_layers <= 0:
        return 0.0
    if abs(growth_rate - 1.0) < 1e-12:
        return first_height_mm * n_layers
    return first_height_mm * (growth_rate ** n_layers - 1.0) / (growth_rate - 1.0)


def design_boundary_layer(
    geometry_length_m: float,
    mach: float,
    altitude_m: float,
    *,
    y_plus_target: float = Y_PLUS_TARGET,
    growth_rate: float = GROWTH_RATE,
    min_layers: int = MIN_LAYERS,
    max_layers: int = MAX_LAYERS,
    first_cell_factor: float = FIRST_CELL_FACTOR,
) -> BoundaryLayerDesign:
    """按工况算边界层首层高度与层数。

    链路（全部为平板湍流工程关联式，出处见模块常量）：

        U    = mach · a(height)            ISA 标准大气算 T/p/rho/a
        mu   = Sutherland(T)
        Re   = rho·U·L / mu                L = 几何总长
        Cf   = 0.058 · Re^-0.2             Schlichting 平板湍流摩阻
        tau_w = 0.5·Cf·rho·U²              壁面剪应力
        u_tau = sqrt(tau_w/rho)            摩擦速度
        y     = y+ · mu / (rho·u_tau)      到第一层质心的距离
        h1    = first_cell_factor · y      FirstHeight（单元高度，不是质心距离）
        delta = 0.37 · L · Re^-0.2         边界层厚度
        n     = min{h1·(r^n−1)/(r−1) ≥ delta}，夹在 [min_layers, max_layers]

    适用性限制（写进 docstring 是为了让读报告的人知道边界在哪）：
      * 平板、零攻角、不可压近似。导弹在大攻角、跨声速激波后的实际边界层与此不同；
        这里给出的是**网格设计量级**，不是流场预测。
      * 1/5 次幂律在 Re ≈ 5e5–1e9 内可信；Re 过小时（层流区）Cf 关联式不适用，
        此时层数会被夹到下限，实际网格偏密，属可接受的一侧。
      * ISA 只覆盖对流层（0–11 km）；Sutherland 只对空气、200–2000 K 有效。
      * 只给**一维**设计（沿 L）。真实外形的转捩位置、舵面前缘分离都不在模型内。
    """
    length = float(geometry_length_m)
    m = float(mach)
    if not math.isfinite(length) or length <= 0.0:
        raise BoundaryLayerError(f"几何总长必须为正有限值，收到 {geometry_length_m!r}")
    if not math.isfinite(m) or m <= 0.0:
        raise BoundaryLayerError(f"马赫数必须为正有限值，收到 {mach!r}")
    if not math.isfinite(y_plus_target) or y_plus_target <= 0.0:
        raise BoundaryLayerError(f"y+ 目标必须为正有限值，收到 {y_plus_target!r}")
    if not (growth_rate > 1.0):
        raise BoundaryLayerError(f"增长率必须 > 1，收到 {growth_rate!r}")
    if not (0 < min_layers <= max_layers):
        raise BoundaryLayerError(f"层数范围不合法：min={min_layers} max={max_layers}")

    air = isa_atmosphere(altitude_m)
    temperature = air["temperature_k"]
    density = air["density_kg_m3"]
    sound_speed = air["sound_speed_m_s"]
    viscosity = sutherland_viscosity(temperature)
    velocity = m * sound_speed

    re = density * velocity * length / viscosity
    if not math.isfinite(re) or re < MIN_REYNOLDS:
        raise BoundaryLayerError(f"雷诺数退化（Re={re!r}），检查 L 与工况是否合理")

    cf = CF_COEFFICIENT * re ** CF_EXPONENT
    tau_w = 0.5 * cf * density * velocity ** 2
    u_tau = math.sqrt(tau_w / density)
    y = y_plus_target * viscosity / (density * u_tau)
    first_height_mm = first_cell_factor * y * 1000.0
    delta = DELTA_COEFFICIENT * length * re ** DELTA_EXPONENT

    # 满足 h1·(r^n−1)/(r−1) ≥ delta 的最小 n
    ratio = growth_rate
    n = 0
    while n < max_layers:
        n += 1
        if layer_stack_height(first_height_mm, n, ratio) >= delta * 1000.0:
            break
    capped = ""
    if n < min_layers:
        n, capped = min_layers, "min"
    elif n >= max_layers and layer_stack_height(first_height_mm, n, ratio) < delta * 1000.0:
        n, capped = max_layers, "max"

    stacked = layer_stack_height(first_height_mm, n, ratio)
    return BoundaryLayerDesign(
        geometry_length_m=length, mach=m, altitude_m=float(altitude_m),
        y_plus_target=float(y_plus_target), growth_rate=float(ratio),
        temperature_k=temperature, pressure_pa=air["pressure_pa"],
        density_kg_m3=density, sound_speed_m_s=sound_speed, velocity_m_s=velocity,
        viscosity_pa_s=viscosity,
        re=re, cf=cf, tau_w_pa=tau_w, u_tau_m_s=u_tau, y_m=y, delta_m=delta,
        first_height_mm=first_height_mm, n_layers=n, bl_thickness_mm=stacked,
        capped=capped, growth_rate_limited=capped == "max",
    )


def design_for_condition(conn, geometry_id: str, condition_id: str, **overrides) -> BoundaryLayerDesign:
    """从库里读几何总长与工况，算一次设计（`watch.py` / `dispatch.py` / `review.py` 共用）。

    **这是"设计值"的唯一入口**：三处都用它，所以审核比对的不是另一份手抄的数。
    `overrides` 透传给 `design_boundary_layer`（y+ / 增长率 / 层数范围）。
    """
    geometry = conn.execute("SELECT L FROM geometry WHERE geometry_id=?",
                            (geometry_id,)).fetchone()
    if geometry is None:
        raise BoundaryLayerError(f"库里没有几何 {geometry_id}")
    condition = conn.execute(
        "SELECT mach, altitude_m FROM condition WHERE condition_id=?",
        (condition_id,)).fetchone()
    if condition is None:
        raise BoundaryLayerError(f"库里没有工况 {condition_id}")
    return design_boundary_layer(float(geometry["L"]), float(condition["mach"]),
                                 float(condition["altitude_m"]), **overrides)


# =============================================================================
# 4. 自述与命令行（复核用）
# =============================================================================
def describe() -> str:
    """把当前参数与公式出处拼成一段可贴进报告的文字。"""
    return "\n".join([
        f"y+ 目标           {Y_PLUS_TARGET:g}（SST k-ω 壁面解析；改用水面函数时应取 30–100）",
        f"增长率            {GROWTH_RATE:g}",
        f"层数范围          [{MIN_LAYERS}, {MAX_LAYERS}]",
        f"首层高度倍数      {FIRST_CELL_FACTOR:g}（FirstHeight ≈ 2 × 质心距离 y）",
        f"Cp 关联式         {CF_CORRELATION}",
        f"边界层厚度关联式  {DELTA_CORRELATION}",
        "来流              ISA 标准大气（ISO 2533 对流层 0–11 km）："
        "T = T0 − a·h，p = p0·(T/T0)^(g/(aR))，rho = p/(R·T)，a = sqrt(gamma·R·T)",
        "黏度              Sutherland：mu = mu_ref·(T/T_ref)^1.5·(T_ref+S)/(T+S)，"
        f"T_ref={SUTHERLAND_T_REF_K:g} K，mu_ref={SUTHERLAND_MU_REF:g} Pa·s，S={SUTHERLAND_S_K:g} K",
        "适用性            平板、零攻角、不可压近似；Re ≈ 5e5–1e9；只给沿 L 的一维设计",
    ])


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="boundary_layer.py",
        description="按工况算边界层首层高度/层数；只做设计计算，不连库、不写文件。",
    )
    parser.add_argument("--length", type=float, required=True, help="几何总长 L（米）")
    parser.add_argument("--mach", type=float, required=True)
    parser.add_argument("--altitude", type=float, required=True, help="几何高度（米）")
    parser.add_argument("--y-plus", type=float, default=Y_PLUS_TARGET)
    parser.add_argument("--growth-rate", type=float, default=GROWTH_RATE)
    parser.add_argument("--describe", action="store_true", help="只打印参数与公式出处")
    args = parser.parse_args(argv)

    if args.describe:
        print(describe())
        return 0
    design = design_boundary_layer(args.length, args.mach, args.altitude,
                                   y_plus_target=args.y_plus,
                                   growth_rate=args.growth_rate)
    print(json.dumps(design.as_dict(), ensure_ascii=False, indent=2))
    print()
    print(f"首层高度 {design.first_height_mm:.4f} mm，{design.n_layers} 层，"
          f"增长率 {design.growth_rate:g}，堆叠厚度 {design.bl_thickness_mm:.2f} mm "
          f"(delta {design.delta_m * 1000:.2f} mm)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
