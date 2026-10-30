# -*- coding: utf-8 -*-
"""DATCOM 几何核对产物：for022 反查、核对报告与三视图。

`01_batch_datcom.py` 生成完 for005.dat 后调用这里的三件事：

1. `write_geometry_outputs`：写 `geometry_check.txt` 与 `geometry_check.png`；
2. `check_tecplot_geometry` + `append_tecplot_geometry_check`：拿 DATCOM 自己写出的
   for022.dat 反查建模（位置 / 弦长 / 展长 / 后掠），比只看输入卡片更硬；
3. `append_coefficient_check`：用 CL/CD 与 CN/CA 互相复核，防止解析错页。

本模块只读算例目录里已有的文件、只写核对产物；输入输出路径全部由调用方给出，
因此不设路径配置区。基准几何（README 那套）由调用方以 `baseline` 传入，
这样本模块不依赖 01 的演示配置。
"""

from __future__ import annotations

import math
import os
import re
import unicodedata
from datetime import datetime

from datcom_spec import (
    FOR022_NAME, GEOMETRY_CARD_HINT, GEOMETRY_CN_NAMES, GEOMETRY_PNG_NAME,
    GEOMETRY_TXT_NAME, M_TO_IN, REQUIRED_GEOMETRY_KEYS, archived_filename,
)
from missile_spec import resolve_nose_shape

# 报告头里写的脚本名：这份核对报告总是由 01_batch_datcom.py 触发生成的。
REPORT_SOURCE_SCRIPT = "01_batch_datcom.py"

# =============================================================================
# 4. for005 文本中的 namelist 区域定位（报告与卡片核对用）
# =============================================================================


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


def _card_token(lines, block, var):
    """读 for005 文本里某个 namelist 块内取名称/字符串值的变量（如 ``TNOSE = OGIVE``）。

    数值变量用 `_card_value`；这里专给 ``TNOSE`` 这类"值是名字"的卡片用，
    读不到返回 None。
    """
    start, end = _find_namelist_region(lines, block)
    if start is None:
        return None
    pattern = re.compile(r"^(\s*)" + re.escape(var) + r"\s*=\s*([^,]*)", re.IGNORECASE)
    for idx in range(start + 1, end):
        match = pattern.match(lines[idx])
        if match:
            token = match.group(2).strip().strip("'\" ").upper()
            return token or None
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

    # 1) 弹体：头锥（卵形 / 圆锥）+ 等直径圆柱段
    check("$AXIBOD LNOSE", g["L_N"] * inch, _card_value(lines, "AXIBOD", "LNOSE"))
    check("$AXIBOD DNOSE", g["D"] * inch, _card_value(lines, "AXIBOD", "DNOSE"))
    check("$AXIBOD LCENTR", (g["L"] - g["L_N"]) * inch,
          _card_value(lines, "AXIBOD", "LCENTR"))
    check("$AXIBOD DCENTR", g["D"] * inch, _card_value(lines, "AXIBOD", "DCENTR"))
    # 头型由 TNOSE 卡片决定（值是名字，不是数值），漏写会退回 DATCOM 默认的卵形
    nose = d.get("nose_shape") or {}
    if nose.get("datcom_token"):
        actual = _card_token(lines, "AXIBOD", "TNOSE")
        ok = actual == nose["datcom_token"].upper()
        checks.append((
            ok,
            f"$AXIBOD TNOSE：期望 {nose['datcom_token']}（{nose.get('cn_name', '头型')}），"
            f"卡片 {actual if actual else '没读到'}"
            + ("" if ok else "  ← 不一致"),
        ))
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


def _nose_shape_text(derived):
    """报告里描述鼻锥头型的一句话（老 meta 没有该字段时按 DATCOM 默认卵形说明）。"""
    nose = derived.get("nose_shape") or {}
    if nose.get("label"):
        return nose["label"]
    return "切线卵形（DATCOM 默认，未写 TNOSE 卡片）"


def _body_outline(meta, samples=64):
    """弹体上半侧母线 [(x, r)]：头锥按实际头型绘制，后接等直圆柱段。

    头型取自 meta（"ogive" / "cone"），由 missile_spec 的解析式给出母线；
    meta 里没有头型时按导弹默认的切线卵形画。
    """
    g = meta["geometry_m"]
    derived = meta["derived"]
    radius = derived["body_radius_m"]
    key = (derived.get("nose_shape") or {}).get("key")
    try:
        profile = resolve_nose_shape(key).profile(g["L_N"], radius)
        grid = [g["L_N"] * index / samples for index in range(samples + 1)]
        outline = [(x, float(r)) for x, r in zip(grid, profile.radius(grid))]
    except (ValueError, TypeError):
        outline = [(0.0, 0.0), (g["L_N"], radius)]          # 头型未知时退回直线锥
    outline.append((g["L"], radius))
    return outline


def _body_polygon(meta):
    """侧视 / 俯视用的弹体闭合多边形（上下对称）。"""
    outline = _body_outline(meta)
    return ([(x, r) for x, r in outline]
            + [(x, -r) for x, r in reversed(outline)])


def render_geometry_report(case_name, meta, for005_text, checks, baseline=None):
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
    add(f"脚本 {REPORT_SOURCE_SCRIPT}    "
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
    add("  · 弹体 = 头锥 + 等直径圆柱段；每道舵按 NPANEL = "
        f"{d['panels_per_fin_set']} 片建在弹体表面（根部回转半径 = D/2）。")
    add(f"  · 鼻锥头型 {_nose_shape_text(d)}")
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
    if baseline:
        add("五、与 README 基准几何（01_batch_datcom.py 顶部的 MISSILE_GEOMETRY）的差异")
        changed = [f"{key}：基准 {float(baseline[key]):g} m → 本算例 {g[key]:g} m"
                   for key in REQUIRED_GEOMETRY_KEYS
                   if key in baseline and abs(g[key] - float(baseline[key])) > 1e-9]
        if changed:
            for item in changed:
                add(f"  · {item}")
        else:
            add("  · 无（本算例就是 README 基准几何）")
    else:
        add("五、与 README 基准几何的差异")
        add("  · 未提供基准几何，跳过本项")
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
            _body_polygon(meta),
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
            _body_polygon(meta),
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
    print(f"    头型: {_nose_shape_text(d)}")
    print(f"    翼型: {d['fin_section_text']}")
    for fin in d["fins"]:
        print(f"    {fin['name']}: XLE = {fin['root_le_m']:.3f} m, "
              f"根弦 {fin['root_chord_m']:.3f} m, 梢弦 {fin['tip_chord_m']:.3f} m, "
              f"展长 {fin['span_m']:.3f} m, 后掠 {fin['sweep_deg']:.2f}°, "
              f"梢部半径 {fin['tip_radius_m']:.3f} m")


def write_geometry_outputs(out_dir, case_name, meta, for005_text, baseline=None):
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
        f.write(render_geometry_report(case_name, meta, for005_text, checks, baseline=baseline))
    png_path = plot_geometry(meta, os.path.join(out_dir, archived_filename(case_name, GEOMETRY_PNG_NAME)))
    return txt_path, png_path, checks
