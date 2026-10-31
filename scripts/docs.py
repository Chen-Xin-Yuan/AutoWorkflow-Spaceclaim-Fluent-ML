# -*- coding: utf-8 -*-
"""把参数值同步进 README：值只写在代码里，README 里的参数表由本脚本生成。

为什么要它：参数默认值是会被直接改的，而 README 里的表格是手工抄的，两边一定会漂。
所以参数表改成"生成块"——`<!-- AUTO-PARAMS:begin name=xxx -->` 与 `:end` 之间的内容
只由本脚本写，人只改块外的散文。

用法：

    python scripts/docs.py --check     # 只检查有没有漂，漂了就列出来并退出码 1
    python scripts/docs.py --write     # 按代码重写所有生成块

CI/日常建议：改完参数跑一次 `--write`，提交前跑一次 `--check`。

-------------------------------------------------------------------------------
输入 / 输出 / 参数配置
-------------------------------------------------------------------------------
输入（唯一真相源，本脚本只读不写）
    `DATCOM/scripts/missile_spec.py`   头型、两套翼型参数、舵面布局、基准几何（import 取值）
    `spaceclaim/lib/missile-schema.mjs` 11 个主尺寸与约定参数的默认值和优化区间（node 导出 JSON）
    `scripts/db.py` / `scripts/review.py` / `scripts/watch.py` /
    `fluent_meshing/mesh_validation.py` 的重试预算、并发、判据阈值（import 取值）
输出
    下列 README 里被标记包住的参数表（`--write` 时重写）：
        `README.md`、`DATCOM/README.md`、`spaceclaim/README.md`
    没有标记的文件不动。
参数配置
    TARGETS  要处理的文件与它含哪些生成块；加新表就在这里加一行，并在那个文件里放一对标记。
覆盖优先级
    只有命令行开关；没有环境变量。生成内容不含机器绝对路径。
-------------------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = SCRIPT_DIR.parent
DATCOM_SCRIPTS = WORKSPACE_ROOT / "DATCOM" / "scripts"

# =============================================================================
# 1. 参数配置区
# =============================================================================
BEGIN = "<!-- AUTO-PARAMS:begin name={name} -->"
END = "<!-- AUTO-PARAMS:end -->"
# 哪些文件里有生成块、分别含哪几块（顺序即输出顺序）
TARGETS = {
    "README.md": ("geometry", "fin-section"),
    # TODO(下一步)：给下面两个文件加上标记对，然后在这里登记
    #   "DATCOM/README.md": ("fin-section", "conventions"),
    #   "spaceclaim/README.md": ("geometry", "conventions"),
}
NODE_SCHEMA_DUMP = (
    "import('./lib/missile-schema.mjs').then((m) => {"
    "const fields = ['field','label','unit','default','min','max','range','options','implemented','kind'];"
    "const pick = (list) => list.map((p) => Object.fromEntries(fields.filter((f) => p[f] !== undefined)"
    ".map((f) => [f, p[f]])));"
    "process.stdout.write(JSON.stringify({geometry: pick(m.GEOMETRY_PARAMS),"
    "convention: pick(m.CONVENTION_PARAMS), regions: pick(m.REGION_PARAMS)}));"
    "});"
)


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _cell(text) -> str:
    return str(text).replace("|", "\\|")


# =============================================================================
# 2. 取值：代码是唯一真相源
# =============================================================================
def load_missile_spec():
    if str(DATCOM_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(DATCOM_SCRIPTS))
    import missile_spec  # noqa: PLC0415

    return missile_spec


def load_spaceclaim_schema() -> dict:
    completed = subprocess.run(
        ["node", "-e", NODE_SCHEMA_DUMP], cwd=str(WORKSPACE_ROOT / "spaceclaim"),
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(f"读取 spaceclaim schema 失败：{completed.stderr.strip()[:300]}")
    return json.loads(completed.stdout)


def load_thresholds() -> dict:
    import db  # noqa: PLC0415
    import review  # noqa: PLC0415

    sys.path.insert(0, str(WORKSPACE_ROOT / "fluent_meshing"))
    import mesh_validation  # noqa: PLC0415

    return {
        "自修重试预算": db.SELF_REPAIR_BUDGET,
        "每阶段并发": 1,
        "watch 扫描间隔（秒）": 30,
        "最小正交质量下限": mesh_validation.MINIMUM_ORTHOGONAL_QUALITY,
        "外形尺寸容差（m）": review.EXTENTS_TOLERANCE_M,
        "求解残差上限": review.RESIDUAL_MAX,
        "系数波动上限": review.FLUCTUATION_MAX_REL,
        "质量流量不平衡上限": review.MASS_IMBALANCE_MAX_REL,
    }


# =============================================================================
# 3. 渲染：每个块一种表格
# =============================================================================
def render_geometry(schema: dict, spec) -> str:
    rows = ["| 设计变量 | 定义 | 下限 / m | 上限 / m | 基准 / m |", "|---|---|---|---|---|"]
    for param in schema["geometry"]:
        field = param["field"]
        if field not in spec.BASELINE_GEOMETRY:
            continue
        low, high = (param.get("range") or ["—", "—"])[:2]
        rows.append("| `{}` | {} | {} | {} | {} |".format(
            field, _cell(param["label"]), _fmt(low), _fmt(high),
            _fmt(spec.BASELINE_GEOMETRY[field])))
    rows.append("")
    rows.append("表里 `—` 表示没有给设计范围；基准值来自 `spaceclaim/lib/missile-schema.mjs`，"
                "与 `DATCOM/scripts/missile_spec.py` 的 `BASELINE_GEOMETRY` 必须一致。")
    return "\n".join(rows)


def render_fin_section(schema: dict, spec) -> str:
    hex_section = spec.make_fin_section("HEX")
    naca_section = spec.make_fin_section("NACA")
    rows = ["| 截面 | 设计变量 | 定义 | 允许值 | 基准 |", "|---|---|---|---|---|"]
    definitions = {
        "thickness_percent": "最大厚度，占弦长的百分数",
        "flat_percent": "平顶段长度，占弦长的百分数",
        "le_radius": "DATCOM 的 LER 卡片值（前缘圆角参数），只进 DATCOM",
        "le_radius_index": "前缘半径指数（0 尖前缘，6 标准圆前缘）",
        "max_thickness_tenths": "最大厚度位置（1/10 弦长）",
    }
    allowed = {
        "thickness_percent": "0 < 值 < 40",
        "flat_percent": "0 ≤ 值 < 100",
        "le_radius": "≥ 0",
        "le_radius_index": "整数 0–9",
        "max_thickness_tenths": "2 / 3 / 4 / 5 / 6",
    }
    for label, section, fields in (
        ("六边形（默认）", hex_section, ("thickness_percent", "flat_percent", "le_radius")),
        ("NACA（可切回）", naca_section, ("thickness_percent", "le_radius_index",
                                         "max_thickness_tenths")),
    ):
        for index, field in enumerate(fields):
            rows.append("| {} | `{}` | {} | {} | {} |".format(
                label if index == 0 else "", field, definitions[field], allowed[field],
                _fmt(getattr(section, field))))
    rows.append("")
    rows.append("基准值来自 `DATCOM/scripts/missile_spec.py` 前部 1.2 配置区；"
                "两个截面的 `thickness_percent` 是同一个量（前后舵共用）。")
    return "\n".join(rows)


def render_conventions(schema: dict, spec) -> str:
    rows = ["| 约定参数 | 含义 | 默认值 | 可选值 |", "|---|---|---|---|"]
    for param in schema["convention"]:
        if param["field"] == "scdm_api_version":
            continue
        options = param.get("options") or []
        implemented = param.get("implemented")
        note = ""
        if implemented is not None and sorted(implemented) != sorted(options):
            note = "（已实现：{}）".format(" / ".join(implemented))
        rows.append("| `{}` | {} | `{}` | {}{} |".format(
            param["field"], _cell(param["label"]), _fmt(param.get("default")),
            " / ".join(f"`{item}`" for item in options) if options else "—", note))
    rows.append("")
    rows.append("默认值与可选值来自 `spaceclaim/lib/missile-schema.mjs`；"
                "落库的真实值以数据库为准，这一表只是建模侧的默认口径。")
    return "\n".join(rows)


def render_thresholds(values: dict) -> str:
    rows = ["| 阈值 | 值 |", "|---|---|"]
    for key, value in values.items():
        rows.append(f"| {key} | {_fmt(value)} |")
    rows.append("")
    rows.append("生成自 `scripts/db.py` / `scripts/review.py` / `fluent_meshing/mesh_validation.py`。")
    return "\n".join(rows)


RENDERERS = {
    "geometry": lambda: render_geometry(load_spaceclaim_schema(), load_missile_spec()),
    "fin-section": lambda: render_fin_section(load_spaceclaim_schema(), load_missile_spec()),
    "conventions": lambda: render_conventions(load_spaceclaim_schema(), load_missile_spec()),
    "thresholds": lambda: render_thresholds(load_thresholds()),
}


# =============================================================================
# 4. 替换生成块
# =============================================================================
def sync_file(path: Path, blocks: tuple[str, ...], write: bool) -> list[str]:
    """返回问题列表；空列表表示这个文件是同步的。"""
    if not path.is_file():
        return [f"{path.name}: 文件不存在"]
    text = path.read_text(encoding="utf-8")
    problems = []
    for name in blocks:
        begin, end = BEGIN.format(name=name), END
        pattern = re.compile(re.escape(begin) + r"(.*?)" + re.escape(end), re.S)
        rendered = RENDERERS[name]()
        new_block = f"{begin}\n{rendered}\n{end}"
        match = pattern.search(text)
        if match is None:
            problems.append(f"{path}: 缺少生成块 name={name}（需要手动加一对标记）")
            continue
        if match.group(1).strip("\n") == rendered:
            continue
        problems.append(f"{path}: 生成块 name={name} 与代码不一致")
        if write:
            text = pattern.sub(lambda _m: new_block, text, count=1)
    if write and problems:
        path.write_text(text, encoding="utf-8")
    return problems


def main(argv: list[str] | None = None) -> int:
    import db  # noqa: PLC0415  （只为了 _configure_output：管道输出用 UTF-8）
    db._configure_output()
    parser = argparse.ArgumentParser(prog="docs.py",
                                     description="把代码里的参数值同步进 README 的生成块。")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="只检查漂移")
    mode.add_argument("--write", action="store_true", help="按代码重写生成块")
    args = parser.parse_args(argv)

    problems = []
    for relative, blocks in TARGETS.items():
        problems += sync_file(WORKSPACE_ROOT / relative, blocks, args.write)

    if args.write:
        if problems:
            print(f"已重写 {len(problems)} 个生成块。")
        else:
            print("已经是同步的，没有改动。")
        return 0
    if problems:
        print("README 参数与代码不一致：")
        for item in problems:
            print(f"  - {item}")
        print("\n运行 python scripts/docs.py --write 重新生成。")
        return 1
    print("README 参数与代码一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
