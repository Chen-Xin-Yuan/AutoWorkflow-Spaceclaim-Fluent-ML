#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导弹外形点云生成 + 几何校验 + 三视图预览（PointNet / PointNet++ 演示数据）。

外形定义（回转轴 = +X，单位 m）:
    总弹长 total_length = 头锥 nose_length + 等直圆柱 midbody_length
    （total_length 本身就是完整弹长，不要再加 nose_length）
    x = 0                 : 头部顶点（锥顶倒角球冠的最前点）
    [0.000, x_t]          : 锥顶倒角（球冠，半径 R_fillet），半径 0 -> r_t
    [x_t, nose_length]    : 头锥母线，半径 r_t -> R_body（与倒角球相切，无折点）
    [nose_length, total_length] : 等直圆柱弹身，半径恒为 R_body
    x = total_length      : 尾部端面（尾盖）

    头锥头型由 scripts/missile_spec.py 顶部的 NOSE_SHAPE 决定，与批量 DATCOM
    共用同一份：ogive = 切线卵形（也是 DATCOM 不写 TNOSE 时的默认头型），
    cone = 直线圆锥。尾部半径与弹身半径相同，不做收缩（无 boat-tail）。
    锥顶倒角半径 R_fillet = NOSE_TIP_FILLET_RATIO × total_length（默认 0.4%，
    约 1.5 cm），也可用 nose_tip_fillet_radius 直接给绝对值（米）；置 0 即
    退化成尖头。x_t / r_t 由"倒角球与头锥母线相切"解析求出，两种头型同一套解法。

舵面（两组，每组 4 片，绕弹轴每 90 度等距分布）:
    位置参数: 每组一个"轴向长度" = 舵面前沿（根部前缘，整片舵面最靠前的点）
    到弹头顶点 x=0 的轴向距离，即 fwd_x_root_le / aft_x_root_le
    （默认 1.10 m / 2.82 m）；两组互相独立，后组不再要求后缘与弹尾对齐。
    约束: x_root_le > nose_length（长在圆柱段上），
          且 x_root_le + c_root <= total_length（后缘不伸出弹尾），均由校验把关。
    每片侧视均为直角梯形（后缘垂直于弹轴 = 直角腰在后缘）:
        根部弦长 c_root > 梢部弦长 c_tip；前缘后掠量 sweep_le ≡ c_root - c_tip，
        由前者自动派生（不单独填参数），保证后缘始终垂直于弹轴。
    建模方式: 在子午面 (x, r) 内画侧视梯形（根部贴在弹体圆柱表面上），
    厚度按对称截面（六边形或 NACA）加在周向。翼型参数与头型都在
    scripts/missile_spec.py 前部的配置区，与批量 DATCOM 使用同一份，
    根梢只是同一剖面按当地弦长缩放。

采样: 默认按 CFD 关键部位加密，总点数不变。舵面、前缘/后缘、根梢、锥顶倒角、
      头锥肩部、舵身交界和尾部底缘比大面积弹身更密。各组仍单独采样，4 片舵面
      平分该组点数，最后精确凑到目标点数并打乱。--seed 固定即可完全复现。
      文件前部 CFD_REFINEMENT_ENABLED = False 时，恢复按面积均匀采样。
      加密的是表面上的点，不是附面层体网格。

输出（默认 ../data_lib/datasets/point_clouds/，可用 --outdir 改）:
    不带几何标识时，文件名仍是 missile_<N>.*，点数和格式沿用本脚本现有演示。
    用 --geometry-id 或 --params 读入总表的 11 个主尺寸后，主文件名是
    <geometry_id>_pointcloud.ply / .xyz，并带标签、meta 和可选网格。

用法（在项目根或任意目录均可，路径自动推导）:
    python scripts/04_pointnet_demo.py                 # 按默认点数生成 + 校验
    python scripts/04_pointnet_demo.py --preview       # 顺便渲染三视图
    python scripts/04_pointnet_demo.py --nose-profile  # 顺便渲染锥顶倒角剖面图
    python scripts/04_pointnet_demo.py --n 4096 --seed 1
    python scripts/04_pointnet_demo.py --geometry-id <id>   # 从 cases.csv 读 11 个主尺寸
    python scripts/04_pointnet_demo.py --params geometry.json

调整头型 / 锥顶倒角: 头型在 scripts/missile_spec.py 顶部（NOSE_SHAPE = ogive / cone，
    与批量 DATCOM 共用）；倒角半径改 MissileGeometry.nose_tip_fillet_ratio
    （倒角半径 = 比例 × total_length）或用 nose_tip_fillet_radius 直接给绝对半径
    （米）；置 0 即退化成尖头。

部件标签: 0 头锥 / 1 圆柱弹身 / 2 尾端面 / 3 前组舵面 / 4 后组舵面
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from missile_spec import (
    BASELINE_GEOMETRY, FIN_AZIMUTH_DEG, FIN_SECTION, LE_MODE, NOSE_SEGMENTS,
    NOSE_SHAPE, NOSE_TIP_FILLET_RADIUS, NOSE_TIP_FILLET_RATIO, FinSection, NoseProfile,
    NoseShape, resolve_nose_shape,
)

# --------------------------------------------------------------------------- #
# 输入 / 输出 / 参数配置（命令行参数覆盖这里的运行默认值）
# --------------------------------------------------------------------------- #
# 输入：不带 --geometry-id / --params 时，使用 MissileGeometry 的演示默认值。
# 正式几何读取总表的 11 个主尺寸：L_N L D L_W C_RW C_TW B_W L_F C_RF C_TF B_F。
# D 是机身直径，脚本内部仍用半径 body_radius = D/2。
# 路径约定：项目根 = scripts/ 的上一级；工作区根 = 项目根的上一级。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent
CASES_CSV = WORKSPACE_ROOT / "data_lib" / "inputs" / "cases.csv"
# --outdir 覆盖输出目录；相对路径仍相对启动时的当前工作目录。
OUTPUT_DIR = WORKSPACE_ROOT / "data_lib" / "datasets" / "point_clouds"
MAIN_DIMENSIONS = (
    "L_N", "L", "D", "L_W", "C_RW", "C_TW", "B_W", "L_F", "C_RF", "C_TF", "B_F",
)

# 头型、舵面翼型、舵面布局、基准几何全部来自 scripts/missile_spec.py 前部的配置区，
# 与批量 DATCOM 共用同一份：改那一处，两边同时生效。这里不要再写第二份默认值。
# 截面保持上下对称（FIN_SECTION / FinSection 由 missile_spec 提供）。
DEFAULT_N_POINTS = 16384
DEFAULT_SEED = 0
DEFAULT_GAUSS = 0.0
DEFAULT_TAG_TEMPLATE = "missile_{n_points}"
# 调用采样 / 网格辅助函数时的原有离散参数默认值（无对应命令行开关）。
REVOLVED_SAMPLING_SEGMENTS = 256
MESH_AZIMUTH_SEGMENTS = 72

# CFD 表面点云局部加密。
# 总点数仍由 DEFAULT_N_POINTS / --n-points 决定，下面的倍数和比例只决定这些点落在哪里。
# 点始终在外形表面上。关闭后恢复按面积均匀采样：CFD_REFINEMENT_ENABLED = False。
CFD_REFINEMENT_ENABLED = True
# 按面积分点时的倍数。1 表示这一组不加权。舵面面积远小于弹身，倍数要大于 1 才会更密。
CFD_CYLINDER_AREA_WEIGHT = 1.0
CFD_NOSE_AREA_WEIGHT = 2.5
CFD_TAIL_AREA_WEIGHT = 3.0
CFD_FIN_AREA_WEIGHT = 4.5
# 舵面已分配的点数里，这一比例落在前缘、后缘、根部、梢部，其余在舵面上均匀分布。
CFD_FIN_BOUNDARY_FRACTION = 0.60
CFD_FIN_BOUNDARY_SPLIT = {
    "leading_edge": 0.42,
    "trailing_edge": 0.20,
    "root": 0.22,
    "tip": 0.16,
}
# 宽边界带，相对当地弦长 / 展长。带内的点按幂次贴向边界，指数越大越贴边。
CFD_FIN_CHORD_BAND = 0.12
CFD_FIN_SPAN_BAND = 0.15
CFD_EDGE_CLUSTER_POWER = 2.5
# 前缘另有一条紧带，用来盖住前缘圆角。宽度 = 倍数 × (前缘半径/弦长)，且不超过宽边界带。
# 紧带份额是前缘点数中的比例，其余前缘点落在宽边界带里。
CFD_FIN_LE_TIGHT_RADIUS_MULTIPLE = 12.0
CFD_FIN_LE_TIGHT_SHARE = 0.65
# 根部和梢部翼型端面里，贴向前缘和后缘的比例。
CFD_FIN_EDGE_PERIMETER_FRACTION = 0.70
# 头锥点数中划给锥顶倒角、头锥-圆柱肩部的比例，其余仍按头锥面积均匀分布。
CFD_NOSE_FILLET_FRACTION = 0.50
CFD_NOSE_SHOULDER_FRACTION = 0.22
# 倒角点数里贴向顶点的比例。核心用更大的指数，其余倒角点用较缓的指数盖住整个球冠。
CFD_NOSE_TIP_CORE_SHARE = 0.35
CFD_NOSE_TIP_CORE_POWER = 5.0
CFD_NOSE_FILLET_POWER = 1.8
# 圆柱点数里划给肩部、底缘和舵身交界的比例。交界再分成舵根前缘、后缘和整条根弦。
CFD_CYLINDER_FEATURE_FRACTION = 0.42
CFD_CYLINDER_FEATURE_SPLIT = {
    "shoulder": 0.16,
    "base": 0.18,
    "fin_junction": 0.66,
}
CFD_JUNCTION_SPLIT = {
    "leading_edge": 0.42,
    "trailing_edge": 0.24,
    "root_chord": 0.34,
}
# 舵根前缘/后缘的轴向窗口，除了直径倍数给出的外伸，还沿根弦向翼面内收这一段。
CFD_JUNCTION_LE_CHORD_FRACTION = 0.18
CFD_JUNCTION_TE_CHORD_FRACTION = 0.12
# 轴向带宽 = 倍数 × 弹身直径。肩部、底缘、舵根前后外伸共用。
CFD_BODY_AXIAL_BAND_DIAMETERS = 0.22
# 舵身干扰区的周向半宽（度）。贴着舵根线的那一部分更窄，宽度由翼型半厚对应的圆心角决定。
CFD_BODY_FIN_AZIMUTH_DEG = 12.0
CFD_FIN_ROOT_AZIMUTH_MULTIPLE = 1.2
CFD_FIN_ROOT_LINE_SHARE = 0.55
CFD_AXIAL_CLUSTER_POWER = 2.0
# 尾盖外缘环带占尾盖点数的比例，环带宽度相对尾盖半径。
CFD_TAIL_RIM_FRACTION = 0.72
CFD_TAIL_RIM_BAND = 0.20

# 输出文件名：{tag} 是 --tag 指定的标签或 DEFAULT_TAG_TEMPLATE 生成的标签。
# 预览图和头锥剖面图分别由 --preview / --nose-profile 开启。
OUTPUT_FILES = {
    "ply": "{tag}.ply",
    "xyz": "{tag}.xyz",
    "labels": "{tag}_labels.txt",
    "meta": "{tag}_meta.json",
    "mesh": "missile_surface.obj",
    "preview": "preview_missile.png",
    "nose_profile": "nose_tip_profile.png",
}

# 部件标签（PointNet / PointNet++ 分割演示用）
PART_LABELS = {
    0: "nose_cone",       # 头锥（头型见 missile_spec.py + 锥顶倒角球冠）
    1: "mid_cylinder",    # 圆柱弹身（尾部同半径，无尾锥）
    2: "tail_cap",        # 尾端面（尾盖）
    3: "fin_forward",     # 前组舵面（4 片）
    4: "fin_aft",         # 后组舵面（4 片）
}
LABEL_ID = {v: k for k, v in PART_LABELS.items()}

# 校验容差
TOL_R = 5e-5          # 径向/轴向容差（点云按 float32 保存，留出裕量）
TOL_A = 5e-4          # 子午面内反解坐标的容差（反解涉及浮点差值，误差被放大）
TOL_AZ_SHEET = 1e-3   # 方位角容差附加量（度）


# --------------------------------------------------------------------------- #
# 一、几何定义
# 字段默认值来自 scripts/missile_spec.py 的 BASELINE_GEOMETRY（唯一来源），
# 这里只做 11 个主尺寸 → 类字段的映射，不重复维护第二份数值。
# 正式几何由 --geometry-id / --params 覆盖这些默认值。
# --------------------------------------------------------------------------- #
_BASELINE = BASELINE_GEOMETRY


@dataclass
class MissileGeometry:
    """导弹几何参数（单位：米）。"""

    # --- 弹身 ---
    total_length: float = _BASELINE["L"]              # 总弹长：头部顶点 x=0 → 尾部端面（含头锥）
    body_radius: float = _BASELINE["D"] / 2.0         # 弹身圆柱半径（尾部半径同值，不区分尾锥）
    nose_length: float = _BASELINE["L_N"]             # 头锥轴向长度（总弹长的一部分）
    # 头型：ogive = 切线卵形 / cone = 直线圆锥，与批量 DATCOM 的 TNOSE 同一份。
    nose_shape: NoseShape = field(default_factory=lambda: resolve_nose_shape(NOSE_SHAPE))
    nose_tip_fillet_ratio: float = NOSE_TIP_FILLET_RATIO      # 锥顶倒角半径 = 比例 × total_length
    nose_tip_fillet_radius: float | None = NOSE_TIP_FILLET_RADIUS  # 直接给倒角半径(m)，非 None 时覆盖比例
    nose_segments: int = NOSE_SEGMENTS                # 头锥母线离散段数（网格用）

    # --- 舵面共同参数 ---
    # 截面默认取 fin_section 的当前翼型（HEX 六边形 / NACA 四位数）。
    # 绝对厚度 = 厚度比 × 当地弦长。
    section: FinSection = field(default_factory=lambda: FIN_SECTION)
    # 一组内 4 片舵，90° 等距；角度由 missile_spec 的 FIN_PHASE / CRUCIFORM_COUNT 派生
    fin_azimuth_deg: tuple = FIN_AZIMUTH_DEG

    # --- 舵面轴向位置 ---
    # "轴向长度" = 舵面前沿（根部前缘，即整片舵面最靠前的位置）到弹头顶点 x=0 的轴向距离。
    # 两组各自独立给定，后组不再要求后缘与弹尾对齐。
    # 约束：x_root_le > nose_length（长在圆柱段上），且 x_root_le + c_root <= total_length
    #       （后缘不得伸出弹尾；超了校验会 FAIL，而不是被悄悄裁掉）。

    # --- 前组舵面 ---
    fwd_x_root_le: float = _BASELINE["L_W"]   # 前组：前沿到弹头顶点 x=0 的轴向距离
    fwd_c_root: float = _BASELINE["C_RW"]
    fwd_c_tip: float = _BASELINE["C_TW"]
    fwd_span: float = _BASELINE["B_W"]
    # 前缘后掠量不单独给：sweep_le ≡ c_root - c_tip，由属性自动派生（后缘垂直于弹轴）

    # --- 后组舵面 ---
    aft_x_root_le: float = _BASELINE["L_F"]   # 后组：前沿到弹头顶点 x=0 的轴向距离
    aft_c_root: float = _BASELINE["C_RF"]
    aft_c_tip: float = _BASELINE["C_TF"]
    aft_span: float = _BASELINE["B_F"]

    # --- 端盖 ---
    include_nose_tip: bool = True
    include_tail_cap: bool = True

    # ------------------------------------------------------------------ #
    # 长度分段
    # ------------------------------------------------------------------ #
    @property
    def midbody_length(self) -> float:
        """等直圆柱段长度 = total_length - nose_length（必须 > 0）。"""
        return self.total_length - self.nose_length

    # ------------------------------------------------------------------ #
    # 头锥几何（解析式在 missile_spec.py：切线卵形 / 直线圆锥 + 锥顶倒角）
    # ------------------------------------------------------------------ #
    @property
    def tip_fillet_radius(self) -> float:
        """锥顶倒角球冠半径 R（米）：默认 = NOSE_TIP_FILLET_RATIO × total_length。"""
        if self.nose_tip_fillet_radius is not None:
            return float(self.nose_tip_fillet_radius)
        return float(self.nose_tip_fillet_ratio) * float(self.total_length)

    @property
    def nose_profile(self) -> NoseProfile:
        """头锥母线解析对象：头型 + 长度 + 弹身半径 + 倒角半径。

        头型由 missile_spec.NOSE_SHAPE 决定（ogive / cone），与批量 DATCOM 的
        TNOSE 卡片同一份；倒角只影响点云 / CFD，DATCOM 头锥是数学尖点。
        """
        return self.nose_shape.profile(
            self.nose_length, self.body_radius, self.tip_fillet_radius)

    @property
    def nose_tangent_x(self) -> float:
        """倒角球与头锥母线的切点轴向坐标 x_t（R = 0 时为 0）。"""
        return self.nose_profile.tangent_x

    @property
    def nose_tangent_r(self) -> float:
        """切点半径 r_t（R = 0 时为 0）。"""
        return self.nose_profile.tangent_r

    # ------------------------------------------------------------------ #
    # 舵面前缘后掠量：模型要求后缘垂直于弹轴（直角梯形），因此
    # sweep_le ≡ c_root - c_tip 是派生量，不单独给参数，避免与弦长对不上。
    # （若以后要允许后缘前掠/后掠，再把 sweep_le 改成独立字段并放开校验。）
    # ------------------------------------------------------------------ #
    @property
    def fwd_sweep_le(self) -> float:
        """前组前缘后掠量 = fwd_c_root - fwd_c_tip（自动派生）。"""
        return self.fwd_c_root - self.fwd_c_tip

    @property
    def aft_sweep_le(self) -> float:
        """后组前缘后掠量 = aft_c_root - aft_c_tip（自动派生）。"""
        return self.aft_c_root - self.aft_c_tip

    def radius(self, x):
        """回转体母线：x -> 半径（头锥（卵形/圆锥）+ 锥顶球冠倒角 + 等直圆柱）。"""
        x = np.asarray(x, dtype=float)
        nose = x < self.nose_length
        y = np.empty_like(x)

        if nose.any():
            y[nose] = self.nose_profile.radius(x[nose])                 # 头锥（含倒角）
        # 等直圆柱：尾部半径与弹身相同，无尾锥收缩
        y[~nose] = self.body_radius
        return y

    def radius_slope(self, x):
        """回转体母线斜率 dr/dx（解析式，避免分段处数值差分）。

        锥顶球冠段的真实斜率在 x→0 处发散，这里用 eps 截断（那里半径本身 → 0，
        对面积积分的贡献为 0；精确面积请用 nose_lateral_area()）。
        """
        x = np.asarray(x, dtype=float)
        nose = x < self.nose_length
        y = np.zeros_like(x)

        if nose.any():
            y[nose] = self.nose_profile.slope(x[nose])
        return y

    def nose_lateral_area(self) -> float:
        """头锥侧面积（解析式，含锥顶倒角球冠；圆锥与卵形各自的闭式解）。"""
        return float(self.nose_profile.lateral_area())

    @property
    def fin_half_thickness_envelope(self) -> float:
        """两套舵面里，翼型表面偏离弦线的最大距离（米）。校验用它做包络。"""
        chords = (self.fwd_c_root, self.fwd_c_tip, self.aft_c_root, self.aft_c_tip)
        return max(chords) * self.section.max_offset_ratio()


@dataclass
class FinPlanform:
    """一片舵面的侧视平面形状（直角梯形）。

    局部坐标: a = 展向 0..span，x = 轴向；弦长沿展向线性变化。
    """

    x_root_le: float
    c_root: float
    c_tip: float
    span: float
    sweep_le: float

    @property
    def x_root_te(self) -> float:
        return self.x_root_le + self.c_root

    @property
    def x_tip_le(self) -> float:
        return self.x_root_le + self.sweep_le

    @property
    def x_tip_te(self) -> float:
        return self.x_tip_le + self.c_tip

    def chord(self, a):
        a = np.asarray(a, dtype=float)
        return self.c_root + (self.c_tip - self.c_root) * (a / self.span)

    def leading_x(self, a):
        a = np.asarray(a, dtype=float)
        return self.x_root_le + self.sweep_le * (a / self.span)

    def area(self) -> float:
        return 0.5 * (self.c_root + self.c_tip) * self.span

    def is_right_trapezoid(self, tol: float = 1e-12) -> bool:
        """侧视：一条腰垂直于底 => 直角梯形（后缘 x 不随展向变化）。"""
        return abs(self.x_root_te - self.x_tip_te) < tol

    def corners(self) -> np.ndarray:
        """侧视四角点，逆时针: 根部前缘, 根部后缘, 梢部后缘, 梢部前缘。"""
        return np.array(
            [
                [self.x_root_le, 0.0],
                [self.x_root_te, 0.0],
                [self.x_tip_te, self.span],
                [self.x_tip_le, self.span],
            ]
        )


def fin_planforms(g: MissileGeometry) -> dict[str, FinPlanform]:
    return {
        "forward": FinPlanform(
            x_root_le=g.fwd_x_root_le,
            c_root=g.fwd_c_root,
            c_tip=g.fwd_c_tip,
            span=g.fwd_span,
            sweep_le=g.fwd_sweep_le,
        ),
        "aft": FinPlanform(
            x_root_le=g.aft_x_root_le,
            c_root=g.aft_c_root,
            c_tip=g.aft_c_tip,
            span=g.aft_span,
            sweep_le=g.aft_sweep_le,
        ),
    }


# --------------------------------------------------------------------------- #
# 二、采样
# --------------------------------------------------------------------------- #
def _dir_vectors(azimuth_deg) -> np.ndarray:
    """一组舵面的外法向单位向量（绕 +X 轴，每 90° 一个）。"""
    return np.asarray(
        [[0.0, math.cos(math.radians(d)), math.sin(math.radians(d))] for d in azimuth_deg]
    )


def _fin_normal_dir(azimuth_rad: float) -> np.ndarray:
    """舵面板厚方向 = 周向单位向量 ê_φ（子午面内梯形沿周向拉伸）。"""
    return np.array([0.0, -math.sin(azimuth_rad), math.cos(azimuth_rad)])


def surface_group_areas(g: MissileGeometry) -> dict[str, float]:
    """各面片分组的面积（m²）：回转体用解析式，避免数值积分在锥顶斜率发散。"""
    areas: dict[str, float] = {}
    plans = fin_planforms(g)

    # 头锥（头型见 missile_spec.py + 锥顶倒角球冠）侧面积
    areas["nose_cone"] = float(g.nose_lateral_area())

    # 等直圆柱弹身段（尾部半径与弹身相同，不再有尾锥/尾锥面积）
    # 圆柱段长度 = total_length - nose_length，必须为正，否则说明总长比头锥还短
    if g.midbody_length <= 0.0:
        raise ValueError(
            f"圆柱段长度为 {g.midbody_length:.6g} m：要求 total_length({g.total_length:g}) "
            f"> nose_length({g.nose_length:g})。注意 total_length 已包含头锥，不要重复相加"
        )
    areas["mid_cylinder"] = float(2 * math.pi * g.body_radius * g.midbody_length)

    # 舵面（4 片 × 上下表面 / 根梢两个翼型端面）
    cap_ratio = g.section.section_area_ratio()
    for name, p in plans.items():
        areas[f"fin_{name}_faces"] = 4.0 * 2.0 * p.area()
        areas[f"fin_{name}_edges"] = 4.0 * cap_ratio * (p.c_root ** 2 + p.c_tip ** 2)

    # 头部顶点处半径收成 0（倒角球冠端点），因此头端盖面积为 0；保留键位兼容原有开关
    if g.include_nose_tip:
        areas["nose_tip_cap"] = float(math.pi * g.radius(0.0) ** 2)
    if g.include_tail_cap:
        areas["tail_cap"] = float(math.pi * g.body_radius**2)
    return areas


def allocate_points(areas: dict[str, float], n_total: int, rng) -> dict[str, int]:
    """按面积比例分配点数，并补偿取整误差使总和精确等于 n_total。

    面积必须是非负有限值：一旦出现负面积 / NaN（通常意味着几何参数互相矛盾，
    例如头锥长度超过了圆柱段起点），这里直接抛可读的中文报错，而不是让 numpy
    抛出难懂的 "pvals < 0, pvals > 1 or pvals contains NaNs"。
    """
    names = list(areas)
    w = np.asarray([areas[k] for k in names], dtype=float)
    bad = ~np.isfinite(w) | (w < 0.0)
    if bad.any():
        detail = ", ".join(f"{k}={v:.6g}" for k, v, b in zip(names, w, bad) if b)
        raise ValueError(f"面积权重非法（负值或 NaN）: {detail}；请检查几何参数是否自洽")
    total = float(w.sum())
    if total <= 0.0:
        raise ValueError(f"面积权重全为 0，无法分配点数: {areas}")
    w = w / total
    counts = rng.multinomial(n_total, w)
    alloc = {k: int(c) for k, c in zip(names, counts)}
    diff = n_total - sum(alloc.values())
    while diff != 0:
        k = names[int(rng.integers(len(names)))]
        if diff > 0:
            alloc[k] += 1
            diff -= 1
        elif alloc[k] > 0:
            alloc[k] -= 1
            diff += 1
    return alloc


def _sample_revolved_section(g: MissileGeometry, x0, x1, n, rng, segs: int = REVOLVED_SAMPLING_SEGMENTS):
    """在回转面 x∈[x0,x1] 上按面积均匀采样 n 个点，返回 (points, normals)。"""
    if n <= 0 or x1 <= x0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    dx = (x1 - x0) / segs
    xs = np.linspace(x0, x1, segs + 1)
    rs = g.radius(xs)
    slope = (rs[1:] - rs[:-1]) / dx
    r_mid = 0.5 * (rs[1:] + rs[:-1])          # 中点法则估面积（比取左端点稳，头锥处不会低估到 0）
    dA = 2 * np.pi * r_mid * np.sqrt(1 + slope**2) * dx
    tot = float(dA.sum())
    if not np.isfinite(tot) or tot <= 0.0:     # 该段退化成零面积
        return np.zeros((0, 3)), np.zeros((0, 3))
    p = dA / tot
    idx = rng.choice(segs, size=n, p=p)
    x = xs[idx] + rng.random(n) * dx
    r = g.radius(x)
    phi = rng.random(n) * 2 * np.pi
    pts = np.stack([x, r * np.cos(phi), r * np.sin(phi)], axis=1)
    # 解析法向: dS/dx × dS/dφ ∝ (-dr/dx, cosφ, sinφ)
    drx = g.radius_slope(x)
    nrm = np.stack([-drx, np.cos(phi), np.sin(phi)], axis=1)
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
    return pts, nrm


def _point_to_trapezoid_dist(xq: np.ndarray, aq: np.ndarray, plan: FinPlanform) -> np.ndarray:
    """(x, a) 平面内点到侧视直角梯形的距离（在梯形内部为 0）。"""
    corners = plan.corners()
    inside = np.ones(xq.shape, dtype=bool)
    for (x0, a0), (x1, a1) in zip(corners, np.roll(corners, -1, axis=0)):
        cross = (x1 - x0) * (aq - a0) - (a1 - a0) * (xq - x0)
        inside &= cross >= -1e-15
    d = np.full(xq.shape, np.inf)
    for (x0, a0), (x1, a1) in zip(corners, np.roll(corners, -1, axis=0)):
        vx, va = x1 - x0, a1 - a0
        L2 = vx * vx + va * va
        t = np.clip(((xq - x0) * vx + (aq - a0) * va) / L2, 0.0, 1.0)
        d = np.minimum(d, np.hypot(xq - (x0 + t * vx), aq - (a0 + t * va)))
    return np.where(inside, 0.0, d)


def _radial_dir(azimuth_rad: float) -> np.ndarray:
    """展向单位向量，从弹轴指向舵梢。"""
    return np.array([0.0, math.cos(azimuth_rad), math.sin(azimuth_rad)])


def _place_fin_point(g: MissileGeometry, plan: FinPlanform, a: float, v: float, s: float, az: float):
    """把翼型截面上的点放回弹体坐标。v 是弦向参数，s 是厚度参数，+1 为上表面。"""
    chord = float(plan.chord(a))
    x_le = float(plan.leading_x(a))
    xc, yc = g.section.interior_xy(v, s)
    x = x_le + xc * chord
    r = float(g.radius(x)) + a
    return np.array([x, 0.0, 0.0]) + r * _radial_dir(az) + (yc * chord) * _fin_normal_dir(az)


def _face_normal(g, plan, a, v, s, az) -> np.ndarray:
    """上下表面的外法向。差分步长落在翼面内部。"""
    eps_v = 1e-4 if v < 1.0 - 1e-4 else -1e-4
    eps_a = 1e-4 * plan.span if a < plan.span - 1e-6 * plan.span else -1e-4 * plan.span
    if eps_a == 0.0:
        eps_a = 1e-6
    p0 = _place_fin_point(g, plan, a, v, s, az)
    pv = _place_fin_point(g, plan, a, min(max(v + eps_v, 0.0), 1.0), s, az) - p0
    pa = _place_fin_point(g, plan, min(max(a + eps_a, 0.0), plan.span), v, s, az) - p0
    if eps_v < 0.0:
        pv = -pv
    if eps_a < 0.0:
        pa = -pa
    normal = np.cross(pa, pv)
    length = float(np.linalg.norm(normal))
    if length < 1e-14:
        normal = s * _fin_normal_dir(az)
        length = float(np.linalg.norm(normal))
    normal = normal / length
    if float(np.dot(normal, _fin_normal_dir(az))) * s < 0.0:
        normal = -normal
    return normal


def _require_unit_interval(name: str, value: float) -> None:
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        raise ValueError(f"{name} 必须在 0 到 1 之间，收到 {value!r}")


def _require_split(name: str, weights: dict[str, float], keys: tuple[str, ...]) -> None:
    missing = [key for key in keys if key not in weights]
    if missing:
        raise ValueError(f"{name} 缺少 {', '.join(missing)}")
    total = float(sum(weights.values()))
    if not math.isfinite(total) or abs(total - 1.0) > 1e-6:
        raise ValueError(f"{name} 的比例之和必须为 1，收到 {total:.6g}")
    for key, value in weights.items():
        _require_unit_interval(f"{name}.{key}", value)


def _check_cfd_config() -> None:
    """加密参数不合法时直接说明，避免采样中途才出现难懂的错误。"""
    if not CFD_REFINEMENT_ENABLED:
        return
    for name, value in (
        ("CFD_CYLINDER_AREA_WEIGHT", CFD_CYLINDER_AREA_WEIGHT),
        ("CFD_NOSE_AREA_WEIGHT", CFD_NOSE_AREA_WEIGHT),
        ("CFD_TAIL_AREA_WEIGHT", CFD_TAIL_AREA_WEIGHT),
        ("CFD_FIN_AREA_WEIGHT", CFD_FIN_AREA_WEIGHT),
    ):
        number = float(value)
        if not math.isfinite(number) or number <= 0.0:
            raise ValueError(f"{name} 必须是正的有限数，收到 {value!r}")
    for name, value in (
        ("CFD_EDGE_CLUSTER_POWER", CFD_EDGE_CLUSTER_POWER),
        ("CFD_NOSE_TIP_CORE_POWER", CFD_NOSE_TIP_CORE_POWER),
        ("CFD_NOSE_FILLET_POWER", CFD_NOSE_FILLET_POWER),
        ("CFD_AXIAL_CLUSTER_POWER", CFD_AXIAL_CLUSTER_POWER),
    ):
        number = float(value)
        if not math.isfinite(number) or number < 1.0:
            raise ValueError(f"{name} 必须大于等于 1，收到 {value!r}")
    for name, value in (
        ("CFD_FIN_LE_TIGHT_RADIUS_MULTIPLE", CFD_FIN_LE_TIGHT_RADIUS_MULTIPLE),
        ("CFD_BODY_AXIAL_BAND_DIAMETERS", CFD_BODY_AXIAL_BAND_DIAMETERS),
        ("CFD_BODY_FIN_AZIMUTH_DEG", CFD_BODY_FIN_AZIMUTH_DEG),
        ("CFD_FIN_ROOT_AZIMUTH_MULTIPLE", CFD_FIN_ROOT_AZIMUTH_MULTIPLE),
        ("CFD_FIN_CHORD_BAND", CFD_FIN_CHORD_BAND),
        ("CFD_FIN_SPAN_BAND", CFD_FIN_SPAN_BAND),
        ("CFD_TAIL_RIM_BAND", CFD_TAIL_RIM_BAND),
        ("CFD_JUNCTION_LE_CHORD_FRACTION", CFD_JUNCTION_LE_CHORD_FRACTION),
        ("CFD_JUNCTION_TE_CHORD_FRACTION", CFD_JUNCTION_TE_CHORD_FRACTION),
    ):
        number = float(value)
        if not math.isfinite(number) or number < 0.0:
            raise ValueError(f"{name} 必须是非负有限数，收到 {value!r}")
    for name, value in (
        ("CFD_FIN_BOUNDARY_FRACTION", CFD_FIN_BOUNDARY_FRACTION),
        ("CFD_FIN_LE_TIGHT_SHARE", CFD_FIN_LE_TIGHT_SHARE),
        ("CFD_FIN_EDGE_PERIMETER_FRACTION", CFD_FIN_EDGE_PERIMETER_FRACTION),
        ("CFD_NOSE_FILLET_FRACTION", CFD_NOSE_FILLET_FRACTION),
        ("CFD_NOSE_SHOULDER_FRACTION", CFD_NOSE_SHOULDER_FRACTION),
        ("CFD_NOSE_TIP_CORE_SHARE", CFD_NOSE_TIP_CORE_SHARE),
        ("CFD_CYLINDER_FEATURE_FRACTION", CFD_CYLINDER_FEATURE_FRACTION),
        ("CFD_FIN_ROOT_LINE_SHARE", CFD_FIN_ROOT_LINE_SHARE),
        ("CFD_TAIL_RIM_FRACTION", CFD_TAIL_RIM_FRACTION),
    ):
        _require_unit_interval(name, value)
    if CFD_NOSE_FILLET_FRACTION + CFD_NOSE_SHOULDER_FRACTION > 1.0 + 1e-9:
        raise ValueError("头锥倒角比例与肩部比例之和不能超过 1")
    _require_split(
        "CFD_FIN_BOUNDARY_SPLIT",
        CFD_FIN_BOUNDARY_SPLIT,
        ("leading_edge", "trailing_edge", "root", "tip"),
    )
    _require_split(
        "CFD_CYLINDER_FEATURE_SPLIT",
        CFD_CYLINDER_FEATURE_SPLIT,
        ("shoulder", "base", "fin_junction"),
    )
    _require_split(
        "CFD_JUNCTION_SPLIT",
        CFD_JUNCTION_SPLIT,
        ("leading_edge", "trailing_edge", "root_chord"),
    )


def _cfd_weighted_areas(areas: dict[str, float]) -> dict[str, float]:
    """分配点数用的加权面积。返回值不替代真实面积。"""
    weighted = {}
    for key, area in areas.items():
        if key == "nose_cone":
            weight = CFD_NOSE_AREA_WEIGHT
        elif key == "mid_cylinder":
            weight = CFD_CYLINDER_AREA_WEIGHT
        elif key == "tail_cap":
            weight = CFD_TAIL_AREA_WEIGHT
        elif key.startswith("fin_"):
            weight = CFD_FIN_AREA_WEIGHT
        else:
            weight = 1.0
        weighted[key] = float(area) * float(weight)
    return weighted


def _cfd_refinement_report() -> dict:
    return {
        "enabled": bool(CFD_REFINEMENT_ENABLED),
        "cylinder_area_weight": float(CFD_CYLINDER_AREA_WEIGHT),
        "nose_area_weight": float(CFD_NOSE_AREA_WEIGHT),
        "tail_area_weight": float(CFD_TAIL_AREA_WEIGHT),
        "fin_area_weight": float(CFD_FIN_AREA_WEIGHT),
        "fin_boundary_fraction": float(CFD_FIN_BOUNDARY_FRACTION),
        "cylinder_feature_fraction": float(CFD_CYLINDER_FEATURE_FRACTION),
        "nose_fillet_fraction": float(CFD_NOSE_FILLET_FRACTION),
        "tail_rim_fraction": float(CFD_TAIL_RIM_FRACTION),
        "body_axial_band_diameters": float(CFD_BODY_AXIAL_BAND_DIAMETERS),
        "body_fin_azimuth_deg": float(CFD_BODY_FIN_AZIMUTH_DEG),
        "surface_only": True,
    }


def _split_counts(total: int, weights: dict[str, float]) -> dict[str, int]:
    """按比例分配整数，余数补给小数部分最大的项，总和精确等于 total。"""
    keys = list(weights)
    if int(total) <= 0:
        return {key: 0 for key in keys}
    raw_weights = np.asarray([float(weights[key]) for key in keys], dtype=float)
    if not np.isfinite(raw_weights).all() or np.any(raw_weights < 0.0):
        raise ValueError(f"点数分配比例非法: {weights}")
    weight_sum = float(raw_weights.sum())
    if weight_sum <= 0.0:
        raise ValueError(f"点数分配比例全为 0: {weights}")
    raw = raw_weights / weight_sum * int(total)
    counts = np.floor(raw).astype(int)
    leftover = int(total) - int(counts.sum())
    order = np.argsort(-(raw - counts))
    for index in range(leftover):
        counts[int(order[index % len(counts)])] += 1
    return {key: int(count) for key, count in zip(keys, counts)}


def _split_counts_even(total: int, parts: int) -> list[int]:
    if parts <= 0:
        raise ValueError("分配份数必须大于 0")
    base, remainder = divmod(int(total), parts)
    return [base + (1 if index < remainder else 0) for index in range(parts)]


def _unit_cluster(rng, n: int, power: float) -> np.ndarray:
    """[0, 1) 上的随机数。power > 1 时样本贴向 0。"""
    if n <= 0:
        return np.zeros(0)
    return rng.random(n) ** max(float(power), 1.0)


def _clamped_band(value: float) -> float:
    return float(min(max(float(value), 0.0), 0.49))


def _sample_x_toward(rng, n: int, x0: float, x1: float, target: float, power: float) -> np.ndarray:
    """在 [x0, x1] 内采样，并贴向 target。"""
    if n <= 0:
        return np.zeros(0)
    x0, x1 = float(x0), float(x1)
    if x1 < x0:
        x0, x1 = x1, x0
    if x1 - x0 < 1e-15:
        return np.full(n, x0)
    target = min(max(float(target), x0), x1)
    left = target - x0
    right = x1 - target
    span = left + right
    draw = _unit_cluster(rng, n, power)
    go_left = rng.random(n) < (left / span if span > 0.0 else 0.5)
    x = np.empty(n)
    x[go_left] = target - draw[go_left] * left
    x[~go_left] = target + draw[~go_left] * right
    return x


def _cat_points(parts_p: list[np.ndarray], parts_n: list[np.ndarray]):
    if len(parts_p) != len(parts_n):
        raise ValueError("点与法向分组数量不一致")
    kept_p = []
    kept_n = []
    for pts, nrm in zip(parts_p, parts_n):
        if len(pts) == 0:
            continue
        if len(pts) != len(nrm):
            raise ValueError("点与法向数量不一致")
        kept_p.append(pts)
        kept_n.append(nrm)
    if not kept_p:
        return np.zeros((0, 3)), np.zeros((0, 3))
    return np.concatenate(kept_p, axis=0), np.concatenate(kept_n, axis=0)


def _revolved_from_xphi(g: MissileGeometry, x, phi):
    """把轴向坐标和方位角放回回转面上，并给出外法向。"""
    x = np.asarray(x, dtype=float).reshape(-1)
    phi = np.asarray(phi, dtype=float).reshape(-1)
    if len(x) != len(phi):
        raise ValueError(f"回转采样的 x 与方位角数量不一致: {len(x)} != {len(phi)}")
    radius = np.asarray(g.radius(x), dtype=float).reshape(-1)
    pts = np.stack([x, radius * np.cos(phi), radius * np.sin(phi)], axis=1)
    slope = np.asarray(g.radius_slope(x), dtype=float).reshape(-1)
    nrm = np.stack([-slope, np.cos(phi), np.sin(phi)], axis=1)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-30)
    return pts, nrm


def _sample_revolved_targeted(g, n, rng, x0, x1, target, power):
    if n <= 0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    x0, x1 = float(x0), float(x1)
    if x1 - x0 < 1e-12:
        return _sample_revolved_section(g, g.nose_length, g.total_length, n, rng)
    x = _sample_x_toward(rng, n, x0, x1, target, power)
    phi = rng.random(n) * 2.0 * math.pi
    return _revolved_from_xphi(g, x, phi)


def _body_axial_band(g: MissileGeometry) -> float:
    return float(CFD_BODY_AXIAL_BAND_DIAMETERS) * 2.0 * float(g.body_radius)


def _sample_nose_fillet(g: MissileGeometry, n: int, rng):
    xt = float(g.nose_tangent_x)
    if n <= 0 or xt <= 0.0:
        return _sample_revolved_section(g, 0.0, g.nose_length, n, rng)
    n_core = int(round(n * float(CFD_NOSE_TIP_CORE_SHARE)))
    n_core = min(max(n_core, 0), n)
    n_cap = n - n_core
    xs = []
    if n_core:
        xs.append(_sample_x_toward(rng, n_core, 0.0, xt, 0.0, CFD_NOSE_TIP_CORE_POWER))
    if n_cap:
        xs.append(_sample_x_toward(rng, n_cap, 0.0, xt, 0.0, CFD_NOSE_FILLET_POWER))
    phi = rng.random(n) * 2.0 * math.pi
    return _revolved_from_xphi(g, np.concatenate(xs), phi)


def _sample_nose_surface(g: MissileGeometry, n: int, rng, refine: bool):
    if n <= 0 or not refine:
        return _sample_revolved_section(g, 0.0, g.nose_length, n, rng)
    fillet_w = float(CFD_NOSE_FILLET_FRACTION) if g.tip_fillet_radius > 0.0 else 0.0
    shoulder_w = float(CFD_NOSE_SHOULDER_FRACTION)
    rest_w = max(0.0, 1.0 - fillet_w - shoulder_w)
    counts = _split_counts(n, {"fillet": fillet_w, "shoulder": shoulder_w, "rest": rest_w})
    parts_p, parts_n = [], []
    if counts["fillet"]:
        pts, nrm = _sample_nose_fillet(g, counts["fillet"], rng)
        parts_p.append(pts)
        parts_n.append(nrm)
    if counts["shoulder"]:
        x0 = max(0.0, g.nose_length - _body_axial_band(g))
        pts, nrm = _sample_revolved_targeted(
            g, counts["shoulder"], rng, x0, g.nose_length, g.nose_length, CFD_AXIAL_CLUSTER_POWER
        )
        parts_p.append(pts)
        parts_n.append(nrm)
    if counts["rest"]:
        pts, nrm = _sample_revolved_section(g, 0.0, g.nose_length, counts["rest"], rng)
        parts_p.append(pts)
        parts_n.append(nrm)
    return _cat_points(parts_p, parts_n)


def _junction_phi(rng, n: int, azimuth: float, tight: float, wide: float) -> np.ndarray:
    """舵根线用窄角，干扰区用到给定的周向半宽。"""
    if n <= 0:
        return np.zeros(0)
    n_line = int(round(n * float(CFD_FIN_ROOT_LINE_SHARE)))
    n_line = min(max(n_line, 0), n)
    n_wide = n - n_line
    parts = []
    if n_line:
        magnitude = _unit_cluster(rng, n_line, CFD_EDGE_CLUSTER_POWER) * tight
        sign = np.where(rng.random(n_line) < 0.5, -1.0, 1.0)
        parts.append(azimuth + sign * magnitude)
    if n_wide:
        magnitude = tight + rng.random(n_wide) * max(wide - tight, 0.0)
        sign = np.where(rng.random(n_wide) < 0.5, -1.0, 1.0)
        parts.append(azimuth + sign * magnitude)
    return np.concatenate(parts)


def _sample_fin_junctions(g: MissileGeometry, n: int, rng, plans: dict[str, FinPlanform]):
    """弹身上沿每片舵根的交界带：前缘、后缘和整条根弦。"""
    if n <= 0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    stations = [
        (plan, math.radians(float(az0)))
        for plan in plans.values()
        for az0 in g.fin_azimuth_deg
    ]
    if not stations:
        return _sample_revolved_section(g, g.nose_length, g.total_length, n, rng)
    counts = _split_counts_even(n, len(stations))
    xs, phis = [], []
    band = _body_axial_band(g)
    for (plan, azimuth), count in zip(stations, counts):
        if count <= 0:
            continue
        half_thickness = 0.5 * g.section.thickness_ratio * float(plan.c_root)
        tight = math.atan2(half_thickness, g.body_radius) * float(CFD_FIN_ROOT_AZIMUTH_MULTIPLE)
        wide = max(math.radians(float(CFD_BODY_FIN_AZIMUTH_DEG)), tight)
        part = _split_counts(count, CFD_JUNCTION_SPLIT)
        windows = {
            "leading_edge": (
                max(g.nose_length, plan.x_root_le - band),
                min(g.total_length, plan.x_root_le + float(CFD_JUNCTION_LE_CHORD_FRACTION) * plan.c_root),
                plan.x_root_le,
            ),
            "trailing_edge": (
                max(g.nose_length, plan.x_root_te - float(CFD_JUNCTION_TE_CHORD_FRACTION) * plan.c_root),
                min(g.total_length, plan.x_root_te + band),
                plan.x_root_te,
            ),
            "root_chord": (
                max(g.nose_length, plan.x_root_le),
                min(g.total_length, plan.x_root_te),
                None,
            ),
        }
        for key, (x_lo, x_hi, target) in windows.items():
            m = part[key]
            if m <= 0:
                continue
            if float(x_hi) - float(x_lo) < 1e-12:
                x = _sample_x_toward(
                    rng, m, g.nose_length, g.total_length,
                    0.5 * (g.nose_length + g.total_length), 1.0,
                )
                phi = rng.random(m) * 2.0 * math.pi
            elif target is None:
                x = float(x_lo) + rng.random(m) * (float(x_hi) - float(x_lo))
                phi = _junction_phi(rng, m, azimuth, tight, wide)
            else:
                x = _sample_x_toward(rng, m, x_lo, x_hi, target, CFD_AXIAL_CLUSTER_POWER)
                phi = _junction_phi(rng, m, azimuth, tight, wide)
            xs.append(np.asarray(x, dtype=float))
            phis.append(np.asarray(phi, dtype=float))
    if not xs:
        return _sample_revolved_section(g, g.nose_length, g.total_length, n, rng)
    return _revolved_from_xphi(g, np.concatenate(xs), np.concatenate(phis))


def _sample_cylinder_surface(g, n, rng, plans, refine: bool):
    x0, x1 = float(g.nose_length), float(g.total_length)
    if n <= 0 or not refine:
        return _sample_revolved_section(g, x0, x1, n, rng)
    n_feat = int(round(n * float(CFD_CYLINDER_FEATURE_FRACTION)))
    n_feat = min(max(n_feat, 0), n)
    parts_p, parts_n = [], []
    if n - n_feat:
        pts, nrm = _sample_revolved_section(g, x0, x1, n - n_feat, rng)
        parts_p.append(pts)
        parts_n.append(nrm)
    splits = _split_counts(n_feat, CFD_CYLINDER_FEATURE_SPLIT)
    band = _body_axial_band(g)
    if splits["shoulder"]:
        pts, nrm = _sample_revolved_targeted(
            g, splits["shoulder"], rng, x0, min(x1, x0 + band), x0, CFD_AXIAL_CLUSTER_POWER
        )
        parts_p.append(pts)
        parts_n.append(nrm)
    if splits["base"]:
        pts, nrm = _sample_revolved_targeted(
            g, splits["base"], rng, max(x0, x1 - band), x1, x1, CFD_AXIAL_CLUSTER_POWER
        )
        parts_p.append(pts)
        parts_n.append(nrm)
    if splits["fin_junction"]:
        pts, nrm = _sample_fin_junctions(g, splits["fin_junction"], rng, plans)
        parts_p.append(pts)
        parts_n.append(nrm)
    return _cat_points(parts_p, parts_n)


def _sample_tail_cap(g: MissileGeometry, n: int, rng, refine: bool):
    if n <= 0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    radius = float(g.body_radius)
    if not refine:
        rr = np.sqrt(rng.random(n)) * radius
    else:
        n_rim = int(round(n * float(CFD_TAIL_RIM_FRACTION)))
        n_rim = min(max(n_rim, 0), n)
        parts = []
        if n - n_rim:
            parts.append(np.sqrt(rng.random(n - n_rim)) * radius)
        if n_rim:
            band = min(max(float(CFD_TAIL_RIM_BAND), 0.0), 1.0)
            inner = radius * (1.0 - band)
            parts.append(radius - _unit_cluster(rng, n_rim, CFD_EDGE_CLUSTER_POWER) * (radius - inner))
        rr = np.concatenate(parts)
    phi = rng.random(n) * 2.0 * math.pi
    pts = np.stack(
        [np.full(n, g.total_length), rr * np.cos(phi), rr * np.sin(phi)], axis=1
    )
    return pts, np.tile([1.0, 0.0, 0.0], (n, 1))


def _leading_edge_chord_fraction(g: MissileGeometry) -> float:
    chord_band = _clamped_band(CFD_FIN_CHORD_BAND)
    tight = g.section.leading_edge_radius_ratio() * float(CFD_FIN_LE_TIGHT_RADIUS_MULTIPLE)
    if tight <= 0.0:
        tight = 0.008
    ceiling = chord_band if chord_band > 0.0 else 0.008
    return float(min(max(tight, 0.008), ceiling))


def _draw_fin_av(g: MissileGeometry, plan: FinPlanform, n: int, rng, refine: bool):
    """舵面参数 (展向 a, 弦向 v)。v=0 是前缘，v=1 是后缘。"""
    if n <= 0:
        return np.zeros(0), np.zeros(0)
    if not refine:
        return rng.random(n) * plan.span, rng.random(n)
    n_bound = int(round(n * float(CFD_FIN_BOUNDARY_FRACTION)))
    n_bound = min(max(n_bound, 0), n)
    splits = _split_counts(n_bound, CFD_FIN_BOUNDARY_SPLIT)
    parts_a, parts_v = [], []
    if n - n_bound:
        parts_a.append(rng.random(n - n_bound) * plan.span)
        parts_v.append(rng.random(n - n_bound))
    chord_band = _clamped_band(CFD_FIN_CHORD_BAND)
    span_band = _clamped_band(CFD_FIN_SPAN_BAND)
    n_le = splits["leading_edge"]
    if n_le:
        n_tight = int(round(n_le * float(CFD_FIN_LE_TIGHT_SHARE)))
        n_tight = min(max(n_tight, 0), n_le)
        tight_width = _leading_edge_chord_fraction(g)
        if n_tight:
            parts_a.append(rng.random(n_tight) * plan.span)
            parts_v.append(_unit_cluster(rng, n_tight, CFD_EDGE_CLUSTER_POWER) * tight_width)
        if n_le - n_tight:
            parts_a.append(rng.random(n_le - n_tight) * plan.span)
            parts_v.append(_unit_cluster(rng, n_le - n_tight, CFD_EDGE_CLUSTER_POWER) * chord_band)
    if splits["trailing_edge"]:
        count = splits["trailing_edge"]
        parts_a.append(rng.random(count) * plan.span)
        parts_v.append(1.0 - _unit_cluster(rng, count, CFD_EDGE_CLUSTER_POWER) * chord_band)
    if splits["root"]:
        count = splits["root"]
        parts_a.append(plan.span * _unit_cluster(rng, count, CFD_EDGE_CLUSTER_POWER) * span_band)
        parts_v.append(rng.random(count))
    if splits["tip"]:
        count = splits["tip"]
        parts_a.append(plan.span * (1.0 - _unit_cluster(rng, count, CFD_EDGE_CLUSTER_POWER) * span_band))
        parts_v.append(rng.random(count))
    return np.concatenate(parts_a), np.concatenate(parts_v)


def _airfoil_section_xy(g: MissileGeometry, n: int, rng, refine: bool):
    """翼型端面内的点。加密时把大部分点贴到前缘和后缘。"""
    if n <= 0:
        return np.zeros(0), np.zeros(0)
    if not refine:
        return g.section.sample_interior(n, rng)
    n_edge = int(round(n * float(CFD_FIN_EDGE_PERIMETER_FRACTION)))
    n_edge = min(max(n_edge, 0), n)
    xs, ys = [], []
    if n - n_edge:
        xi, yi = g.section.sample_interior(n - n_edge, rng)
        xs.append(xi)
        ys.append(yi)
    if n_edge:
        n_le = (n_edge + 1) // 2
        n_te = n_edge - n_le
        tight = _leading_edge_chord_fraction(g)
        chord_band = _clamped_band(CFD_FIN_CHORD_BAND)
        for count, leading, width in ((n_le, True, tight), (n_te, False, chord_band)):
            if count <= 0:
                continue
            x = _unit_cluster(rng, count, CFD_EDGE_CLUSTER_POWER) * width
            if not leading:
                x = 1.0 - x
            s = rng.random(count) * 2.0 - 1.0
            xy = np.array(
                [g.section.interior_xy(float(x[i]), float(s[i])) for i in range(count)],
                dtype=float,
            )
            xs.append(xy[:, 0])
            ys.append(xy[:, 1])
    return np.concatenate(xs), np.concatenate(ys)


def _sample_fin_faces(
    g: MissileGeometry, plan: FinPlanform, n, rng, az0, x_max=None, refine: bool = False,
):
    """舵面上下表面。refine 时前缘、后缘、根部、梢部更密，厚度仍取当前截面。"""
    if n <= 0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    if x_max is None:
        x_max = g.total_length
    az = math.radians(az0)
    pts = np.zeros((n, 3))
    nrm = np.zeros((n, 3))
    todo = np.arange(n)
    cand = np.zeros((0, 3))
    normals = np.zeros((0, 3))
    keep = np.zeros(0, dtype=bool)
    for _ in range(200):
        if not len(todo):
            break
        m = len(todo)
        a, v = _draw_fin_av(g, plan, m, rng, refine)
        side = np.where(rng.random(m) < 0.5, -1.0, 1.0)
        cand = np.zeros((m, 3))
        normals = np.zeros((m, 3))
        for i in range(m):
            aa = float(a[i])
            vv = float(min(max(v[i], 0.0), 1.0))
            ss = float(side[i])
            cand[i] = _place_fin_point(g, plan, aa, vv, ss, az)
            normals[i] = _face_normal(g, plan, aa, vv, ss, az)
        keep = cand[:, 0] <= x_max + 1e-12
        idx = todo[keep]
        pts[idx] = cand[keep]
        nrm[idx] = normals[keep]
        todo = todo[~keep]
    if len(todo):
        pts[todo] = cand[~keep]
        nrm[todo] = normals[~keep]
    return pts, nrm


def _sample_fin_edges(
    g: MissileGeometry, plan: FinPlanform, n, rng, az0, x_max=None, refine: bool = False,
):
    """根部和梢部的翼型端面。加密时端面内的点贴向前缘和后缘。"""
    if n <= 0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    if x_max is None:
        x_max = g.total_length
    weights = np.array([plan.c_root ** 2, plan.c_tip ** 2], dtype=float)
    if float(weights.sum()) <= 0.0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    probability = weights / weights.sum()
    az = math.radians(az0)
    out = np.zeros((n, 3))
    normals = np.zeros((n, 3))
    filled = np.zeros(n, dtype=bool)
    for _ in range(200):
        if filled.all():
            break
        pending = np.where(~filled)[0]
        m = len(pending)
        which = rng.choice(2, size=m, p=probability)
        xc, yc = _airfoil_section_xy(g, m, rng, refine)
        cand = np.zeros((m, 3))
        cand_n = np.zeros((m, 3))
        for i in range(m):
            a = 0.0 if which[i] == 0 else plan.span
            chord = float(plan.chord(a))
            x = float(plan.leading_x(a)) + float(xc[i]) * chord
            r = float(g.radius(x)) + a
            cand[i] = (
                np.array([x, 0.0, 0.0])
                + r * _radial_dir(az)
                + (float(yc[i]) * chord) * _fin_normal_dir(az)
            )
            cand_n[i] = -_radial_dir(az) if which[i] == 0 else _radial_dir(az)
        ok = cand[:, 0] <= x_max + 1e-12
        out[pending[ok]] = cand[ok]
        normals[pending[ok]] = cand_n[ok]
        filled[pending[ok]] = True
    if not filled.all():
        missing = np.where(~filled)[0]
        out[missing] = cand[~ok]
        normals[missing] = cand_n[~ok]
    return out, normals


def build_pointcloud(
    g: MissileGeometry,
    n_total: int = DEFAULT_N_POINTS,
    seed: int = DEFAULT_SEED,
    noise: float = DEFAULT_GAUSS,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """→ (points[N,3], normals[N,3], labels[N], 统计信息)。"""
    _check_cfd_config()
    refine = bool(CFD_REFINEMENT_ENABLED)
    rng = np.random.default_rng(seed)
    plans = fin_planforms(g)
    dirs = _dir_vectors(g.fin_azimuth_deg)
    areas = surface_group_areas(g)
    allocation_areas = _cfd_weighted_areas(areas) if refine else areas
    alloc = allocate_points(allocation_areas, n_total, rng)

    chunks: list[np.ndarray] = []
    normals: list[np.ndarray] = []
    labels: list[np.ndarray] = []

    def add(pts: np.ndarray, nrm: np.ndarray, label: str) -> None:
        chunks.append(pts)
        normals.append(nrm)
        labels.append(np.full(len(pts), LABEL_ID[label], dtype=np.int8))

    # --- 回转体两段：头锥（卵形 / 圆锥）+ 等直圆柱弹身（尾部同半径，无尾锥）---
    pts, nrm = _sample_nose_surface(g, alloc.get("nose_cone", 0), rng, refine)
    add(pts, nrm, "nose_cone")
    pts, nrm = _sample_cylinder_surface(g, alloc.get("mid_cylinder", 0), rng, plans, refine)
    add(pts, nrm, "mid_cylinder")

    # --- 端盖 ---
    if g.include_nose_tip and alloc.get("nose_tip_cap", 0) > 0:
        n = alloc["nose_tip_cap"]
        rr = np.sqrt(rng.random(n)) * g.radius(0.0)
        phi = rng.random(n) * 2 * np.pi
        add(
            np.stack([np.zeros(n), rr * np.cos(phi), rr * np.sin(phi)], axis=1),
            np.tile([-1.0, 0.0, 0.0], (n, 1)),
            "nose_cone",
        )
    if g.include_tail_cap and alloc.get("tail_cap", 0) > 0:
        pts, nrm = _sample_tail_cap(g, alloc["tail_cap"], rng, refine)
        add(pts, nrm, "tail_cap")

    # --- 舵面（4 片平分点数，余数按片补齐）---
    fin_report = {}
    for name, plan in plans.items():
        n_face = alloc.get(f"fin_{name}_faces", 0)
        n_edge = alloc.get(f"fin_{name}_edges", 0)
        label = f"fin_{name}"
        face_counts = _split_counts_even(n_face, len(dirs))
        edge_counts = _split_counts_even(n_edge, len(dirs))
        for index, d in enumerate(dirs):
            az0 = math.degrees(math.atan2(d[2], d[1])) % 360.0
            pts, nrm = _sample_fin_faces(
                g, plan, face_counts[index], rng, az0, x_max=g.total_length, refine=refine,
            )
            add(pts, nrm, label)
            pts, nrm = _sample_fin_edges(
                g, plan, edge_counts[index], rng, az0, x_max=g.total_length, refine=refine,
            )
            add(pts, nrm, label)
        fin_report[name] = {
            "n_faces": n_face,
            "n_edges": n_edge,
            "x_root_le": plan.x_root_le,
            "x_root_te": plan.x_root_te,
            "x_tip_le": plan.x_tip_le,
            "x_tip_te": plan.x_tip_te,
            "c_root": plan.c_root,
            "c_tip": plan.c_tip,
            "span": plan.span,
            "sweep_le": plan.sweep_le,
            "area_one_fin_m2": plan.area(),
            "is_right_trapezoid": plan.is_right_trapezoid(),
        }

    points = np.concatenate(chunks, axis=0)
    normal_arr = np.concatenate(normals, axis=0)
    label_arr = np.concatenate(labels, axis=0)
    raw_count = int(len(points))

    # 精确凑数（分配阶段已保证，这里做一次保险裁剪/回填）
    if len(points) > n_total:
        sel = rng.permutation(len(points))[:n_total]
        points, normal_arr, label_arr = points[sel], normal_arr[sel], label_arr[sel]
    elif len(points) < n_total:
        extra = rng.integers(0, len(points), n_total - len(points))
        points = np.concatenate([points, points[extra]], axis=0)
        normal_arr = np.concatenate([normal_arr, normal_arr[extra]], axis=0)
        label_arr = np.concatenate([label_arr, label_arr[extra]], axis=0)

    if noise > 0:
        points = points + rng.normal(0.0, noise, points.shape)

    # 打乱顺序，避免同类点连续排布（标签同步重排）
    perm = rng.permutation(len(points))
    points, normal_arr, label_arr = points[perm], normal_arr[perm], label_arr[perm]

    info = {
        "n_points": int(len(points)),
        "seed": seed,
        "noise_sigma": noise,
        "sampling_mode": "cfd-local-refinement" if refine else "area-weighted uniform",
        "cfd_refinement": _cfd_refinement_report(),
        "count_adjustment": int(raw_count - n_total),
        "areas_m2": areas,
        "allocation_areas_m2": allocation_areas,
        "points_per_group": alloc,
        "points_per_part": {
            PART_LABELS[i]: int((label_arr == i).sum()) for i in PART_LABELS
        },
        "fins": fin_report,
    }
    return points, normal_arr, label_arr, info


# --------------------------------------------------------------------------- #
# 三、参考三角网格（同一几何，封闭流形）
# --------------------------------------------------------------------------- #
def add_fin_plate_mesh(
    g: MissileGeometry,
    plan: FinPlanform,
    az0: float,
    verts: list,
    faces: list,
    nu: int = 8,
    nv: int = 24,
    x_max: float | None = None,
) -> None:
    """把一片舵面按当前截面加成网格。弦向用余弦间距，让前缘圆角有足够的点。"""
    if x_max is None:
        x_max = g.total_length
    az = math.radians(az0)
    # 六边形截面的前后缘在几何上都是尖点（该处半厚为 0），上下表面必须共用顶点，
    # 网格才既有封闭流形、又没有零面积三角形；NACA 后缘还有 0.09% 弦长的小厚度，
    # 这时仍按上下两排顶点建面，保持原有拓扑不变。
    closed_te = float(g.section.half_thickness_array(np.array([1.0]))[0]) <= 1e-12

    grid: dict[tuple, int] = {}
    for iu in range(nu + 1):
        a = plan.span * iu / nu
        for iv in range(nv + 1):
            v = 0.5 * (1.0 - math.cos(math.pi * iv / nv))
            if iv == 0 or (iv == nv and closed_te):
                # 前缘（以及六边形的尖后缘）上下表面交于同一点，必须共用顶点。
                p = _place_fin_point(g, plan, a, v, 1.0, az)
                if p[0] > x_max:
                    p = np.array([x_max, p[1], p[2]])
                grid[(iu, iv)] = len(verts)
                verts.append(list(p))
                continue
            for side_i, side in enumerate((-1.0, 1.0)):
                p = _place_fin_point(g, plan, a, v, side, az)
                if p[0] > x_max:
                    p = np.array([x_max, p[1], p[2]])
                grid[(iu, iv, side_i)] = len(verts)
                verts.append(list(p))

    def vid(iu, iv, side):
        if iv == 0 or (iv == nv and closed_te):
            return grid[(iu, iv)]
        return grid[(iu, iv, 0 if side < 0.0 else 1)]

    for iu in range(nu):
        for iv in range(nv):
            for side in (-1.0, 1.0):
                v00 = vid(iu, iv, side)
                v10 = vid(iu + 1, iv, side)
                v11 = vid(iu + 1, iv + 1, side)
                v01 = vid(iu, iv + 1, side)
                if len({v00, v10, v11, v01}) < 3:
                    continue
                if side > 0.0:
                    faces.append((v00, v10, v11))
                    faces.append((v00, v11, v01))
                else:
                    faces.append((v00, v11, v10))
                    faces.append((v00, v01, v11))

    ring = (
        [(0, iv) for iv in range(nv)]
        + [(iu, nv) for iu in range(nu)]
        + [(nu, iv) for iv in range(nv, 0, -1)]
        + [(iu, 0) for iu in range(nu, 0, -1)]
    )
    for k in range(len(ring)):
        i0, j0 = ring[k]
        i1, j1 = ring[(k + 1) % len(ring)]
        a_lo, a_hi = vid(i0, j0, -1.0), vid(i0, j0, 1.0)
        b_lo, b_hi = vid(i1, j1, -1.0), vid(i1, j1, 1.0)
        if a_lo == a_hi and b_lo == b_hi:
            continue
        if a_lo == a_hi:
            faces.append((a_lo, b_lo, b_hi))
            continue
        if b_lo == b_hi:
            faces.append((a_lo, b_lo, a_hi))
            continue
        faces.append((a_lo, b_lo, b_hi))
        faces.append((a_lo, b_hi, a_hi))


def build_mesh(g: MissileGeometry, n_az: int = MESH_AZIMUTH_SEGMENTS) -> tuple[np.ndarray, np.ndarray]:
    """整弹三角网格（封闭流形：头部收成一点、尾部加端盖、舵面为当前截面）。"""
    plans = fin_planforms(g)
    verts: list[list[float]] = []
    faces: list[tuple[int, int, int]] = []

    # 头部：头锥母线（卵形要沿弧细分，圆锥两站就够）+ 锥顶倒角球冠多切几刀
    profile_x = g.nose_profile.stations(g.nose_segments)
    # 等直圆柱弹身段（尾部与弹身同半径，无尾锥）
    profile_x += list(np.linspace(g.nose_length, g.total_length, 4)[1:])
    xs = np.array(sorted(set(profile_x)))
    rs = g.radius(xs)
    phi = np.linspace(0, 2 * np.pi, n_az, endpoint=False)

    apex = len(verts)
    verts.append([0.0, 0.0, 0.0])                      # 头部顶点
    ring_start = []
    for x, r in zip(xs, rs):
        ring_start.append(len(verts))
        for p in phi:
            verts.append([float(x), float(r * math.cos(p)), float(r * math.sin(p))])
    a = ring_start[0]
    for j in range(n_az):                              # 头锥顶点扇形
        faces.append((apex, a + j, a + (j + 1) % n_az))
    for k in range(len(xs) - 1):
        a, b = ring_start[k], ring_start[k + 1]
        for j in range(n_az):
            j2 = (j + 1) % n_az
            faces.append((a + j, a + j2, b + j2))
            faces.append((a + j, b + j2, b + j))
    tail = ring_start[-1]
    cap_center = len(verts)
    verts.append([float(g.total_length), 0.0, 0.0])     # 尾端面圆心
    for j in range(n_az):
        j2 = (j + 1) % n_az
        faces.append((cap_center, tail + j2, tail + j))

    for plan in plans.values():
        for d in _dir_vectors(g.fin_azimuth_deg):
            add_fin_plate_mesh(
                g, plan, math.degrees(math.atan2(d[2], d[1])) % 360.0, verts, faces,
                x_max=g.total_length,
            )
    return np.asarray(verts), np.asarray(faces)


# --------------------------------------------------------------------------- #
# 四、几何校验
# --------------------------------------------------------------------------- #
def validate(
    g: MissileGeometry, points: np.ndarray, n_total: int | None = None
) -> list[tuple[str, bool, str]]:
    """对点云做几何校验，返回 [(项目, 是否通过, 说明)]。

    n_total 给定时顺带核对点数（不再把 8192 写死，改 --n 也不会误报失败）。
    """
    plans = fin_planforms(g)
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    n = len(points)
    x = points[:, 0]
    r = np.linalg.norm(points[:, 1:], axis=1)
    az = (np.degrees(np.arctan2(points[:, 2], points[:, 1])) + 360.0) % 360.0

    # 1) 点数与范围
    expected = n_total if n_total is not None else n
    check(f"point_count == {expected}", n == expected, f"n = {n}")
    check(
        "all x within [0, total_length]",
        bool(x.min() >= -1e-9 and x.max() <= g.total_length + 1e-9),
        f"x in [{x.min():.4f}, {x.max():.4f}]",
    )

    # 2) 每个点都落在弹身表面 / 尾端面 / 舵面实体上
    on_body = np.abs(r - g.radius(np.clip(x, 0.0, g.total_length))) < TOL_R
    on_tail_cap = (np.abs(x - g.total_length) < TOL_R) & (r <= g.body_radius + TOL_R)

    fin_hit = np.zeros(n, dtype=bool)
    fin_group = np.full(n, -1, dtype=int)
    for gi, (name, plan) in enumerate(plans.items()):
        for az0 in g.fin_azimuth_deg:
            dev = np.abs(((az - az0 + 180.0) % 360.0) - 180.0)
            e_n = _fin_normal_dir(math.radians(az0))        # 周向 = 板厚方向
            h = points @ e_n                                # 偏离板中面的距离
            a_gen = r - g.radius(np.clip(x, 0.0, g.total_length))
            dist = _point_to_trapezoid_dist(x, a_gen, plan)
            # 方位角容差：板厚 t/2 在半径很小处对应的圆心角可以更大
            dev_tol = np.degrees(
                np.arctan2(g.fin_half_thickness_envelope + TOL_A, np.maximum(r, 1e-6))
            ) + TOL_AZ_SHEET
            hit = (
                (dev < np.minimum(dev_tol, 15.0))
                & (np.abs(h) <= g.fin_half_thickness_envelope + TOL_R)
                & (dist <= g.fin_half_thickness_envelope + TOL_A)
            )
            newly = hit & ~fin_hit
            fin_group[newly] = gi
            fin_hit |= hit

    stray = ~(on_body | on_tail_cap | fin_hit)
    check(
        "all points on body / tail cap / fin surface",
        int(stray.sum()) == 0,
        f"{int(stray.sum())} stray points",
    )
    for gi, name in enumerate(plans):
        check(
            f"{name} fin group has points",
            int((fin_group == gi).sum()) > 0,
            f"{int((fin_group == gi).sum())} points",
        )

    # 3) 每组 4 片、90° 等距
    for gi, name in enumerate(plans):
        m = fin_group == gi
        base = az[m] % 90.0
        base = np.where(base > 45.0, base - 90.0, base)
        dev = np.abs(base)
        dev_tol = np.degrees(
            np.arctan2(g.fin_half_thickness_envelope + TOL_A, np.maximum(r[m], 1e-6))
        ) + TOL_AZ_SHEET
        near_edge = dev <= np.degrees(
            np.arctan2(2.0 * g.fin_half_thickness_envelope + TOL_A, np.maximum(r[m], 1e-6))
        ) + TOL_AZ_SHEET
        quadrants = sorted(set(((az[m] // 90.0) % 4).astype(int).tolist()))
        check(
            f"{name} fins: 4 fins at 90 deg spacing",
            len(quadrants) == 4 and bool(near_edge.all()) and float((dev <= dev_tol).mean()) > 0.5,
            f"quadrants={quadrants} max_az_dev={dev.max():.2f} deg",
        )

    # 4) 侧视直角梯形（点云只能建后缘垂直于弹轴的梯形，DATCOM 的对齐方式必须一致）
    check(
        "planform: LE_MODE == trailing_aligned (matches right-trapezoid model)",
        LE_MODE == "trailing_aligned",
        f"missile_spec.LE_MODE = {LE_MODE!r}",
    )
    for name, plan in plans.items():
        check(
            f"{name} planform is right trapezoid (TE vertical)",
            plan.is_right_trapezoid(),
            f"x_root_te={plan.x_root_te:.4f} x_tip_te={plan.x_tip_te:.4f}",
        )
        check(
            f"{name} chord c_root > c_tip",
            plan.c_root > plan.c_tip,
            f"c_root={plan.c_root:.3f} > c_tip={plan.c_tip:.3f}",
        )

    # 5) 舵面轴向位置：两组各自独立（"前沿到弹头顶点的轴向距离"），
    #    不再要求后组后缘与弹尾对齐，只要求后缘不伸出弹尾
    for name, plan in plans.items():
        check(
            f"{name} fin: trailing edge inside body (x_root_te <= total_length)",
            plan.x_root_te <= g.total_length + 1e-9,
            f"x_root_te={plan.x_root_te:.4f} <= total_length={g.total_length:.4f} "
            f"(le={plan.x_root_le:.3f} + c_root={plan.c_root:.3f})",
        )
    fwd_plan, aft_plan = plans["forward"], plans["aft"]
    check(
        "fin groups do not overlap axially",
        fwd_plan.x_root_te <= aft_plan.x_root_le + 1e-9
        or aft_plan.x_root_te <= fwd_plan.x_root_le + 1e-9,
        f"forward [{fwd_plan.x_root_le:.3f}, {fwd_plan.x_root_te:.3f}], "
        f"aft [{aft_plan.x_root_le:.3f}, {aft_plan.x_root_te:.3f}]",
    )

    # 6) 弹身轮廓
    check("body: nose radius -> 0 at x=0", abs(float(g.radius(0.0))) < 1e-12, "r(0) = 0")

    # 长度分段：total_length 本身就是完整弹长，圆柱段 = total_length - nose_length
    check(
        "length: total_length > nose_length (cylinder section > 0)",
        g.midbody_length > 0.0,
        f"total_length={g.total_length:g} = nose_length({g.nose_length:g}) + "
        f"midbody_length({g.midbody_length:g})",
    )

    # 圆柱段取两处抽样（不写死 0.3：头锥变长后 0.3 会落在头锥上）
    x_cyl = [0.5 * (g.nose_length + g.total_length), g.total_length]
    r_cyl = [float(g.radius(xi)) for xi in x_cyl]
    check(
        "body: constant-radius cylinder over [nose_length, total_length]",
        all(abs(ri - g.body_radius) < 1e-12 for ri in r_cyl),
        f"r({x_cyl[0]:.2f})={r_cyl[0]:.4f}, r({x_cyl[1]:.2f})={r_cyl[1]:.4f}, "
        f"r_body={g.body_radius:.4f}",
    )
    check(
        "body: tail radius == body radius (no boat-tail)",
        abs(float(g.radius(g.total_length)) - g.body_radius) < 1e-12,
        f"r_tail={float(g.radius(g.total_length)):.3f} == r_body={g.body_radius:.3f}",
    )

    # 7) 锥顶倒角（球冠与头锥母线相切、无折点；两种头型同一套检查）
    profile = g.nose_profile
    R = g.tip_fillet_radius
    xt = profile.tangent_x
    nose_cn = g.nose_shape.cn_name
    if R > 0.0:
        # 倒角球球心 (R, 0) 到头锥母线的最近距离应等于 R（相切）
        xs_prof = np.linspace(xt, g.nose_length, 4001)
        rs_prof = profile.radius(xs_prof)
        dist = np.hypot(xs_prof - R, rs_prof)
        gap = float(dist.min()) - R
        check(
            f"nose: tip fillet is tangent to the {nose_cn} profile (min distance == R)",
            abs(gap) < 1e-6 and 0.0 < xt < g.nose_length,
            f"R={R:.6f} min_dist={float(dist.min()):.6f} x_t={xt:.6f} "
            f"头型={nose_cn}",
        )
        # 切点处斜率连续（球冠解析斜率应等于头锥母线斜率，即无折点）
        s_cap = (R - xt) / math.sqrt(max(2.0 * R * xt - xt * xt, 1e-300))
        s_body = float(profile.slope(xt))
        check(
            "nose: fillet/nose slopes match at the tangent point",
            abs(s_cap - s_body) < 1e-9,
            f"slope_cap={s_cap:.9f} slope_{nose_cn}={s_body:.9f}",
        )
        # 球冠段母线应与解析球面一致
        xs_cap = np.linspace(0.0, xt, 16)
        err = float(np.max(np.abs(g.radius(xs_cap) - np.sqrt(np.clip(2 * R * xs_cap - xs_cap**2, 0, None)))))
        check("nose: tip cap follows the sphere profile", err < 1e-9, f"max err = {err:.2e}")
    else:
        # 无倒角：母线应从尖点 x=0 起、到肩部 x=nose_length 收到 body_radius
        r0 = float(g.radius(0.0))
        rl = float(g.radius(g.nose_length))
        check(
            f"nose: sharp {nose_cn} profile (fillet disabled)",
            abs(r0) < 1e-12 and abs(rl - g.body_radius) < 1e-12,
            f"R = 0，r(0)={r0:.2e}，r(L_N)={rl:.6f} == r_body={g.body_radius:.6f}",
        )

    # 8) 舵面根部应落在圆柱段上（避免舵面长在头锥上）
    check(
        "fin roots lie on the cylindrical body section",
        all(p.x_root_le >= g.nose_length for p in plans.values()),
        ", ".join(f"{k}: x_root_le={p.x_root_le:.3f} vs nose_length={g.nose_length:.3f}"
                  for k, p in plans.items()),
    )

    # 参考网格自检（封闭流形 + 无退化面）
    verts, faces = build_mesh(g)
    tri = verts[faces]
    area = 0.5 * np.linalg.norm(
        np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1
    )
    edge_use: dict[tuple[int, int], int] = {}
    for f in faces:
        for a_, b_ in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])):
            key = (min(a_, b_), max(a_, b_))
            edge_use[key] = edge_use.get(key, 0) + 1
    check(
        "reference mesh is a closed manifold",
        all(v == 2 for v in edge_use.values()) and float(area.min()) > 1e-12,
        f"{len(verts)} verts / {len(faces)} faces / min area {area.min():.2e}",
    )
    return results


# --------------------------------------------------------------------------- #
# 五、导出
# --------------------------------------------------------------------------- #
def write_ply_binary(path: Path, points: np.ndarray) -> None:
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        "comment missile point cloud generated by scripts/04_pointnet_demo.py\n"
        f"element vertex {len(points)}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "end_header\n"
    )
    path.write_bytes(header.encode("ascii") + np.ascontiguousarray(points, dtype="<f4").tobytes())


def write_text(path: Path, lines) -> None:
    """按 LF 写文本，保证跨平台可复现（仓库按 LF 存储）。"""
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def write_obj(path: Path, verts: np.ndarray, faces: np.ndarray) -> None:
    lines = ["# missile surface (same geometry as the point cloud)"]
    lines += [f"v {v[0]:.8f} {v[1]:.8f} {v[2]:.8f}" for v in verts]
    lines += [f"f {f[0] + 1} {f[1] + 1} {f[2] + 1}" for f in faces]
    write_text(path, lines)


def load_points(path: Path) -> np.ndarray:
    """读取本脚本生成的二进制 PLY 或 XYZ。"""
    if path.suffix.lower() == ".ply":
        raw = path.read_bytes()
        end = raw.index(b"end_header")
        header = raw[:end].decode("ascii", errors="ignore")
        n = 0
        for line in header.splitlines():
            if line.startswith("element vertex"):
                n = int(line.split()[-1])
        body = raw[raw.index(b"\n", end) + 1:]
        return np.frombuffer(body, dtype="<f4", count=n * 3).reshape(n, 3).astype(float)
    return np.loadtxt(path, dtype=float).reshape(-1, 3)


def render_preview(points: np.ndarray, path: Path) -> bool:
    """渲染侧视 / 尾向 / 3D 三视图 PNG；缺 matplotlib 时跳过。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                                  # pragma: no cover
        print(f"[warn] skip preview (matplotlib unavailable: {exc})")
        return False

    x, y, z = points[:, 0], points[:, 1], points[:, 2]
    # 中文字体不可靠，图内文字统一用英文
    fig = plt.figure(figsize=(16, 5.2), dpi=130)
    ax1 = fig.add_subplot(1, 3, 1)
    ax1.scatter(x, z, s=1.6, c=np.hypot(y, z), cmap="viridis", linewidths=0)
    ax1.set_title("side view (X-Z): right-trapezoid fins")
    ax1.set_xlabel("x / m")
    ax1.set_ylabel("z / m")
    ax1.set_aspect("equal")
    ax1.grid(alpha=0.25)

    ax2 = fig.add_subplot(1, 3, 2)
    ax2.scatter(y, z, s=1.6, c=x, cmap="plasma", linewidths=0)
    ax2.set_title("axial view (Y-Z): 4 fins @ 90 deg")
    ax2.set_xlabel("y / m")
    ax2.set_ylabel("z / m")
    ax2.set_aspect("equal")
    ax2.grid(alpha=0.25)

    ax3 = fig.add_subplot(1, 3, 3, projection="3d")
    ax3.scatter(x, y, z, s=1.0, c=x, cmap="plasma", linewidths=0)
    ax3.set_title("3D")
    ax3.set_xlabel("x")
    ax3.set_ylabel("y")
    ax3.set_zlabel("z")
    try:
        ax3.set_box_aspect((np.ptp(x), np.ptp(y), np.ptp(z)))
    except Exception:
        pass

    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return True


def render_nose_profile(g: MissileGeometry, path: Path) -> bool:
    """渲染头锥母线放大图（核对锥顶倒角）；缺 matplotlib 时跳过。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                                  # pragma: no cover
        print(f"[warn] skip nose profile (matplotlib unavailable: {exc})")
        return False

    R = g.tip_fillet_radius
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), dpi=140)

    # 左：锥顶放大（球冠母线 + 相切头锥母线 + 无倒角的理想母线作参考）
    ax = axes[0]
    x_tip = min(g.nose_length, max(4.0 * R, 0.05))
    x = np.linspace(0.0, x_tip, 600)
    ax.plot(x, g.radius(x), "k-", lw=2, label="r(x)")
    ideal = g.nose_shape.profile(g.nose_length, g.body_radius).radius(x)
    ax.plot(x, ideal, "r--", lw=1, label=f"ideal sharp {g.nose_shape.key}")
    if R > 0.0:
        t = np.linspace(0.0, np.pi / 2.0, 120)
        ax.plot(R - R * np.cos(t), R * np.sin(t), "g:", lw=1, label=f"fillet sphere R={R:.4f}")
        ax.plot([g.nose_tangent_x], [g.nose_tangent_r], "bo", ms=6,
                label=f"tangent x_t={g.nose_tangent_x:.4f}")
    ax.set_title(f"nose tip zoom ({g.nose_shape.key} + tip fillet)")
    ax.set_xlabel("x / m")
    ax.set_ylabel("r / m")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    ax.set_aspect("equal")

    # 右：整弹母线（上下对称），标出头锥/圆柱交界
    ax = axes[1]
    xa = np.linspace(0.0, g.total_length, 900)
    ra = g.radius(xa)
    ax.plot(xa, ra, "k-", lw=2)
    ax.plot(xa, -ra, "k-", lw=2)
    ax.axvline(g.nose_length, color="r", ls=":", lw=1,
               label=f"nose/cylinder joint x={g.nose_length:g}")
    ax.axhline(0.0, color="gray", lw=0.6)
    ax.set_title(f"full body: {g.nose_shape.key} nose "
                 f"(TNOSE = {g.nose_shape.datcom_token}) + constant-radius cylinder")
    ax.set_xlabel("x / m")
    ax.set_ylabel("r / m")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    ax.set_aspect("equal")

    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return True


# --------------------------------------------------------------------------- #
# 六、入口
# --------------------------------------------------------------------------- #
def missile_from_main_dimensions(values: dict) -> MissileGeometry:
    """用总表的 11 个主尺寸构造外形。D 是直径，内部半径取 D/2。"""
    missing = [key for key in MAIN_DIMENSIONS if key not in values]
    if missing:
        raise ValueError("缺少主尺寸：" + ", ".join(missing))
    diameter = float(values["D"])
    if not math.isfinite(diameter) or diameter <= 0:
        raise ValueError("D 必须是大于 0 的有限数。")
    return MissileGeometry(
        total_length=float(values["L"]),
        body_radius=diameter / 2.0,
        nose_length=float(values["L_N"]),
        fwd_x_root_le=float(values["L_W"]),
        fwd_c_root=float(values["C_RW"]),
        fwd_c_tip=float(values["C_TW"]),
        fwd_span=float(values["B_W"]),
        aft_x_root_le=float(values["L_F"]),
        aft_c_root=float(values["C_RF"]),
        aft_c_tip=float(values["C_TF"]),
        aft_span=float(values["B_F"]),
    )


def main_dimensions_from_cases(geometry_id: str) -> dict:
    """从输入总表取出该 geometry_id 的 11 个主尺寸。各行必须一致。"""
    if not CASES_CSV.is_file():
        raise FileNotFoundError(f"找不到输入总表：{CASES_CSV}")
    with CASES_CSV.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        missing = [key for key in ("geometry_id", *MAIN_DIMENSIONS) if key not in header]
        if missing:
            raise ValueError("输入总表缺少列：" + ", ".join(missing))
        rows = [row for row in reader if (row.get("geometry_id") or "").strip() == geometry_id]
    if not rows:
        raise ValueError(f"输入总表里没有 geometry_id={geometry_id!r}。")
    parsed = []
    for row in rows:
        values = {}
        for key in MAIN_DIMENSIONS:
            raw = (row.get(key) or "").strip()
            if raw == "":
                raise ValueError(f"geometry_id {geometry_id} 的 {key} 是空的。")
            values[key] = float(raw)
        parsed.append(values)
    first = parsed[0]
    for other in parsed[1:]:
        if any(abs(first[key] - other[key]) > 1e-9 for key in MAIN_DIMENSIONS):
            raise ValueError(f"geometry_id {geometry_id} 的各行主尺寸不一致。")
    return first


def main_dimensions_from_params(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    missing = [key for key in MAIN_DIMENSIONS if key not in raw]
    if missing:
        raise ValueError(f"{path.name} 缺少主尺寸：" + ", ".join(missing))
    return {key: float(raw[key]) for key in MAIN_DIMENSIONS}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="导弹外形点云生成 + 校验 + 预览（PointNet 演示）")
    ap.add_argument("-n", "--n-points", type=int, default=DEFAULT_N_POINTS, help=f"点数（默认 {DEFAULT_N_POINTS}）")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help="随机种子（固定即完全复现）")
    ap.add_argument("--gauss", type=float, default=DEFAULT_GAUSS, help="高斯噪声标准差（米），默认无噪声")
    ap.add_argument("--outdir", type=Path, default=None, help="输出目录，默认 data_lib/datasets/point_clouds")
    ap.add_argument("--tag", type=str, default=None, help="文件名标签。给出几何标识时默认 <geometry_id>_pointcloud")
    ap.add_argument("--geometry-id", default=None, help="从 data_lib/inputs/cases.csv 读取该几何的 11 个主尺寸")
    ap.add_argument("--params", type=Path, default=None, help="含 11 个主尺寸的 JSON，字段名与总表相同")
    ap.add_argument("--no-mesh", action="store_true", help="不导出参考网格 obj")
    ap.add_argument("--preview", action="store_true", help="渲染三视图 PNG")
    ap.add_argument("--nose-profile", action="store_true", help="渲染头锥母线放大图（核对锥顶倒角）")
    ap.add_argument("--skip-validate", action="store_true", help="跳过几何校验")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    geometry_id = (args.geometry_id or "").strip()
    main_dimensions = None
    try:
        if args.params and geometry_id:
            raise ValueError("--params 和 --geometry-id 只能选一个。")
        if args.params:
            main_dimensions = main_dimensions_from_params(args.params)
            geometry_id = geometry_id or args.params.stem
        elif geometry_id:
            main_dimensions = main_dimensions_from_cases(geometry_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}")
        return 1
    g = missile_from_main_dimensions(main_dimensions) if main_dimensions else MissileGeometry()
    plans = fin_planforms(g)
    if args.tag:
        tag = args.tag
    elif geometry_id:
        tag = f"{geometry_id}_pointcloud"
    else:
        tag = DEFAULT_TAG_TEMPLATE.format(n_points=args.n_points)
    outdir = Path(args.outdir) if args.outdir else OUTPUT_DIR
    outdir.mkdir(parents=True, exist_ok=True)

    points, normals, labels, info = build_pointcloud(
        g, n_total=args.n_points, seed=args.seed, noise=args.gauss
    )

    ply_path = outdir / OUTPUT_FILES["ply"].format(tag=tag)
    xyz_path = outdir / OUTPUT_FILES["xyz"].format(tag=tag)
    labels_path = outdir / OUTPUT_FILES["labels"].format(tag=tag)
    write_ply_binary(ply_path, points)
    write_text(xyz_path, (f"{p[0]:.8f} {p[1]:.8f} {p[2]:.8f}" for p in points))
    write_text(labels_path, (str(int(v)) for v in labels))

    mesh_path = outdir / (f"{tag}.obj" if geometry_id else OUTPUT_FILES["mesh"])
    if not args.no_mesh:
        verts, faces = build_mesh(g)
        write_obj(mesh_path, verts, faces)

    n = len(points)
    radial = np.linalg.norm(points[:, 1:], axis=1)
    checks = {}
    if not args.skip_validate:
        results = validate(g, points, n_total=args.n_points)
        checks = {name: ok for name, ok, _ in results}

    meta = {
        "description": "missile-shaped point cloud: nose (tangent ogive or cone, see nose_shape) "
                       "with a spherical tip fillet + constant-radius cylinder body "
                       "(tail radius == body radius, no boat-tail), "
                       "two fin groups (4 right-trapezoid fins each, 90 deg apart, symmetric fin section). "
                       "Each fin group is positioned by its leading-edge axial distance from the "
                       "nose tip (fwd_x_root_le / aft_x_root_le); the aft group is NOT required "
                       "to be flush with the tail.",
        "nose_shape": {
            "key": g.nose_shape.key,
            "cn_name": g.nose_shape.cn_name,
            "datcom_token": g.nose_shape.datcom_token,
            "tip_fillet_radius_m": float(g.tip_fillet_radius),
            "tangent_x_m": float(g.nose_tangent_x),
            "tangent_r_m": float(g.nose_tangent_r),
        },
        "fin_section": g.section.as_dict(),
        "generator": "scripts/04_pointnet_demo.py",
        "project_root": str(PROJECT_ROOT),
        "geometry": {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(g).items()},
        "main_dimensions": main_dimensions,
        "fin_planforms": {
            k: {
                "x_root_le": v.x_root_le, "x_root_te": v.x_root_te,
                "x_tip_le": v.x_tip_le, "x_tip_te": v.x_tip_te,
                "c_root": v.c_root, "c_tip": v.c_tip, "span": v.span,
                "sweep_le": v.sweep_le, "area_one_fin_m2": v.area(),
                "is_right_trapezoid": v.is_right_trapezoid(),
            }
            for k, v in plans.items()
        },
        "sampling": {
            "mode": info["sampling_mode"],
            "cfd_refinement": info["cfd_refinement"],
            "count_adjustment": info["count_adjustment"],
            "points_per_group": info["points_per_group"],
            "points_per_part": info["points_per_part"],
            "areas_m2": info["areas_m2"],
            "allocation_areas_m2": info["allocation_areas_m2"],
            "seed": args.seed,
            "noise_sigma": args.gauss,
        },
        "part_labels": {str(k): v for k, v in PART_LABELS.items()},
        "bbox_min": points.min(axis=0).tolist(),
        "bbox_max": points.max(axis=0).tolist(),
        "r_max": float(radial.max()),
        "checks": checks,
        "files": {
            "ply": ply_path.name,
            "xyz": xyz_path.name,
            "labels": labels_path.name,
            "mesh": None if args.no_mesh else mesh_path.name,
        },
    }
    meta_path = outdir / OUTPUT_FILES["meta"].format(tag=tag)
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
    )

    print(f"[OK] points = {n}  (requested {args.n_points})")
    print(f"     nose = {g.nose_shape.label()}")
    print(f"     fin section = {g.section.designation()}")
    print(f"     bbox min = {np.round(points.min(axis=0), 4).tolist()}")
    print(f"     bbox max = {np.round(points.max(axis=0), 4).tolist()}")
    print(f"     r_max    = {radial.max():.4f}  (aft-fin tip radius)")
    print(f"     per-part: " + ", ".join(f"{k}={v}" for k, v in info["points_per_part"].items()))
    if info["cfd_refinement"]["enabled"]:
        print("     sampling = CFD 关键部位加密（舵面、前缘/后缘、根梢、锥顶、肩部、舵身交界、底缘）")
    else:
        print("     sampling = 按面积均匀")
    for name, p in plans.items():
        print(
            f"     fin[{name}]: root_x=[{p.x_root_le:.3f},{p.x_root_te:.3f}] "
            f"tip_x=[{p.x_tip_le:.3f},{p.x_tip_te:.3f}] span={p.span:.3f} "
            f"c_root={p.c_root:.3f} c_tip={p.c_tip:.3f} right_trapezoid={p.is_right_trapezoid()}"
        )
    for path in (ply_path, xyz_path, labels_path, meta_path):
        print(f"     -> {path}")
    if not args.no_mesh:
        print(f"     -> {mesh_path}")

    if not args.skip_validate:
        width = max(len(name) for name, _, _ in results)
        print("--- geometry validation ---")
        for name, ok, detail in results:
            print(f"[{'PASS' if ok else 'FAIL'}] {name.ljust(width)}  {detail}")
        failed = [name for name, ok, _ in results if not ok]
        if failed:
            print(f"RESULT: FAILED ({len(failed)}): {failed}")
            return 1
        print(f"RESULT: ALL {len(results)} CHECKS PASSED")

    if args.preview:
        png_path = outdir / (f"{tag}_preview.png" if geometry_id else OUTPUT_FILES["preview"])
        if render_preview(points, png_path):
            print(f"     -> {png_path}")
    if args.nose_profile:
        prof_path = outdir / (f"{tag}_nose_profile.png" if geometry_id else OUTPUT_FILES["nose_profile"])
        if render_nose_profile(g, prof_path):
            print(f"     -> {prof_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
