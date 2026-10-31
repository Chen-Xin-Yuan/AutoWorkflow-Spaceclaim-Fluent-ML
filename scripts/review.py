# -*- coding: utf-8 -*-
"""确定性审核判据：把一次运行产物核成一份 `checks` 清单，结论由 `db.py record-review` 入库。

四个 `kind`：`model`（建模）、`mesh`（网格）、`solution`（求解）、`datcom`（低保真自检）。
本文件只读产物、只算判据，**不写库**；入库必须走 `python scripts/db.py record-review --file`，
或者加 `--record` 由本文件代为一调（写入路径仍然是 db.py 那一个）。

判据分两级：
    否决性（blocking=True）——任何一条不过就是 `rejected`
    告警性（blocking=False）——只记进 `checks`，不影响结论

-------------------------------------------------------------------------------
输入 / 输出 / 参数配置
-------------------------------------------------------------------------------
输入
    数据库          命令行 `--database` > 环境变量 `MISSILE_DB` > `workspace.json`（与 db.py 共用）。
    运行目录        `data_lib/runs/high/<stage>/<run_id>/`（model / mesh / solution）
                    低保真归档 `data_lib/runs/low/<geometry_id>/`（datcom）
    产物            `run.json`、`result.json`、`params.json`、`quality.json` 等，由各阶段脚本写出。
输出
    审核 JSON       `data_lib/runtime/reviews/<kind>_<run_id>.json`（`--out` 可覆盖）
                    字段：kind / geometry_id / condition_id / run_id / reviewer / verdict / checks / artifact
    `--record` 时还会经 `scripts/db.py` 写 `review` 表并推进任务状态。
参数配置
    EXTENTS_TOLERANCE_M        外形尺寸容差（米）
    POINT_CLOUD_BLOCKING       点云核对是否否决（P6 之前不否决，见文件内说明）
    ORTHOGONAL_QUALITY_MIN     最小正交质量下限（网格）
    CONVERGENCE_*              求解收敛门槛
覆盖优先级
    命令行 > 本节默认值。时间戳一律 UTC（由 db.py 统一生成）。
-------------------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

# 脚本根路径必须先定义：下面的 import 依赖它。
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import db  # noqa: E402  （同目录的 scripts/db.py，唯一的写库入口）

# =============================================================================
# 1. 参数配置区
# =============================================================================
RUNS_HIGH_REL = "data_lib/runs/high"
REVIEW_OUTPUT_DIR_REL = "data_lib/runtime/reviews"
STAGE_OF_KIND = {"model": "spaceclaim", "mesh": "fluent_meshing",
                 "solution": "fluent_solution", "datcom": "datcom"}

EXTENTS_TOLERANCE_M = 1e-3        # 外形尺寸（轴向/展向/垂向）允许误差

# 点云核对是**否决项**：`04_pointnet_demo.py --geometry-id` 现在从数据库读 16 个参数
# （`main_dimensions` 的 11 个主尺寸 + 头型），所以点云缺失或与库不一致就是真缺陷。
# 仍有一处已知缺口：翼型三参数还没写进 `*_meta.json`，因此只能核 12/16；等 DATCOM 侧
# 把翼型写进 meta（需要用户点头，见 DATCOM/AGENTS.md），这条判据自动覆盖 16 个。
POINT_CLOUD_BLOCKING = True

# 网格判据（P3 使用）
ORTHOGONAL_QUALITY_MIN = 0.05
MESH_BBOX_TOLERANCE_REL = 0.10
MESH_CELL_COUNT_TOLERANCE_REL = 0.50
REQUIRED_BOUNDARY_ZONES = ("inlet", "outlet", "wall")
# 网格会话的原始输出；`quality.json` 缺 `bboxM` 时从它解析包围盒（不改动已入账产物）
TRANSCRIPT_NAME = "validation.trn"

# 求解判据（P5 使用）
RESIDUAL_MAX = 1e-4
FLUCTUATION_WINDOW_STEPS = 200
FLUCTUATION_MAX_REL = 0.005
MASS_IMBALANCE_MAX_REL = 0.001

# 与 DATCOM 对比只告警不否决（§7.3）
DATCOM_WARN_TOLERANCE = {"CL": 0.30, "CD": 0.40, "CM": 0.30}


# =============================================================================
# 2. 通用工具
# =============================================================================
def _read_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(checks: list, name: str, ok: bool, detail: str = "", *, blocking: bool = True) -> bool:
    checks.append({"name": name, "ok": bool(ok), "detail": detail, "blocking": blocking})
    return bool(ok)


def _geometry_row(conn, geometry_id: str):
    row = conn.execute("SELECT * FROM geometry WHERE geometry_id=?", (geometry_id,)).fetchone()
    if row is None:
        raise SystemExit(f"库里没有几何 {geometry_id}")
    return row


def _run_directory(stage: str, run_id: str) -> Path:
    return db.WORKSPACE_ROOT / RUNS_HIGH_REL / stage / run_id


def _expected_extents(geometry) -> dict:
    """验收用的三个外形尺寸：轴向 = L；展向/垂向 = D + 2·max(B_W, B_F)（十字舵在最外）。"""
    return {
        "axial": float(geometry["L"]),
        "lateral": float(geometry["D"]) + 2 * max(float(geometry["B_W"]), float(geometry["B_F"])),
        "vertical": float(geometry["D"]) + 2 * max(float(geometry["B_W"]), float(geometry["B_F"])),
    }


# =============================================================================
# 3. model：建模审核
# =============================================================================
def review_model(conn, run_id: str, *, force: bool = False) -> dict:
    run = db.resolve_run(conn, run_id)
    if run is None:
        raise SystemExit(f"库里没有 run_id {run_id} 对应的任务（先在 dispatch 里领一次）")
    if run["stage"] != "spaceclaim":
        raise SystemExit(f"run_id {run_id} 属于 {run['stage']}，不是建模阶段")
    geometry_id = run["geometry_id"]
    geometry = _geometry_row(conn, geometry_id)
    run_dir = _run_directory("spaceclaim", run_id)
    checks: list = []

    if not _check(checks, "运行目录存在", run_dir.is_dir(), str(run_dir)):
        return _verdict(conn, "model", geometry_id, None, run_id, checks, None)

    try:
        manifest = _read_json(run_dir / "run.json")
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        _check(checks, "run.json 可读", False, str(exc))
        return _verdict(conn, "model", geometry_id, None, run_id, checks, None)

    # 判据 1：脚本自报通过且真机验证
    verified = bool((manifest.get("verification") or {}).get("runtimeVerified"))
    _check(checks, "run.json status=PASSED 且 runtimeVerified=true",
           manifest.get("status") == "PASSED" and verified,
           f"status={manifest.get('status')} runtimeVerified={verified}")

    # 判据 2：result.json 自报 PASS
    result = {}
    try:
        result = _read_json(run_dir / "result.json")
        _check(checks, "result.json status=PASS", result.get("status") == "PASS",
               f"status={result.get('status')}")
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        _check(checks, "result.json status=PASS", False, str(exc))

    # 判据 3：run.json 记录的每个产物与磁盘一致
    mismatched = []
    artifacts = manifest.get("artifacts") or []
    for item in artifacts:
        rel = item.get("path")
        path = db.WORKSPACE_ROOT / rel if rel else None
        if not path or not path.exists():
            mismatched.append(f"{rel} 缺失")
            continue
        actual = _sha256(path)
        if actual != item.get("sha256"):
            mismatched.append(f"{rel} sha256 不符")
    _check(checks, "run.json 里每个产物的 sha256 与磁盘一致",
           bool(artifacts) and not mismatched,
           (f"{len(artifacts)} 个产物" if not mismatched else "; ".join(mismatched[:5]))
           + ("（--force：已过审的产物被下游重新保存过，本项放宽为告警）" if force else ""),
           blocking=not force)

    # 判据 4/5：外形尺寸
    extents = ((result.get("measurements") or {}).get("extentsM") or {})
    expected = _expected_extents(geometry)
    for axis in ("axial", "lateral", "vertical"):
        measured = extents.get(axis)
        ok = isinstance(measured, (int, float)) and abs(float(measured) - expected[axis]) <= EXTENTS_TOLERANCE_M
        _check(checks, f"{axis} 尺寸与参数一致",
               ok,
               f"实测 {measured} 期望 {expected[axis]:.6f}（容差 {EXTENTS_TOLERANCE_M} m）")

    # 判据 7：参数快照与库内几何一致
    try:
        params = _read_json(run_dir / "params.json")
        snapshot = params.get("params") or {}
        tool_section = db.SECTION_TO_TOOL.get(geometry["fin_section_type"])
        wanted = {"fin_section": tool_section,
                  "thickness_percent": float(geometry["thickness_percent"]),
                  "nose_shape": geometry["nose_shape"]}
        if geometry["fin_section_type"] == "hex":
            wanted["flat_percent"] = float(geometry["flat_percent"])
        wrong = [f"{key}: 快照 {snapshot.get(key)} ≠ 库 {value}"
                 for key, value in wanted.items()
                 if not _close(snapshot.get(key), value)]
        _check(checks, "params.json 的翼型/头型与库内参数一致", not wrong,
               "一致" if not wrong else "; ".join(wrong))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        _check(checks, "params.json 的翼型/头型与库内参数一致", False, str(exc))

    # 判据 6：点云（P6 之前只告警）
    cloud_rel = _check_point_cloud(checks, geometry)
    if cloud_rel is not None:
        # 核对通过才落库：`cloud_path` 必须指向真正与库内参数一致的那份点云
        db.set_cloud_path(conn, geometry_id, cloud_rel)

    artifact = _find_model_artifact(run_dir, geometry_id)
    _check(checks, "模型文件存在且文件名以 geometry_id 开头", artifact is not None,
           str(artifact) if artifact else f"没找到 {geometry_id}_*.scdoc")

    return _verdict(conn, "model", geometry_id, None, run_id, checks,
                    _fingerprint(artifact) if artifact else None)


def _close(a, b, tolerance: float = 1e-6) -> bool:
    if isinstance(b, str):
        return a == b
    try:
        return abs(float(a) - float(b)) <= tolerance
    except (TypeError, ValueError):
        return False


def _find_model_artifact(run_dir: Path, geometry_id: str) -> Path | None:
    candidates = sorted(run_dir.glob(f"{geometry_id}*.scdoc"))
    if not candidates:
        # 手工运行默认叫 missile.scdoc，也接受（此时必须只有一个 .scdoc）
        all_scdoc = sorted(run_dir.glob("*.scdoc"))
        return all_scdoc[0] if len(all_scdoc) == 1 else None
    return candidates[0]


def _check_point_cloud(checks: list, geometry) -> str | None:
    """核对点云；通过时返回应写进 `geometry_state.cloud_path` 的相对路径。"""
    cloud_root = db.WORKSPACE_ROOT / "data_lib/datasets/point_clouds" / geometry["geometry_id"]
    meta_files = sorted(cloud_root.glob("*_meta.json")) if cloud_root.is_dir() else []
    if not meta_files:
        _check(checks, "点云存在且参数与库一致", False,
               "点云目录不存在或没有 *_meta.json（04_pointnet_demo.py 尚未改为读库，见 P6）",
               blocking=POINT_CLOUD_BLOCKING)
        return None
    try:
        meta = _read_json(meta_files[0])
    except json.JSONDecodeError as exc:
        _check(checks, "点云存在且参数与库一致", False, str(exc), blocking=POINT_CLOUD_BLOCKING)
        return None
    dimensions = meta.get("main_dimensions") or {}
    wrong = [
        f"{key}: 点云 {dimensions.get(key)} ≠ 库 {geometry[key]}"
        for key in db.GEOMETRY_LENGTH_KEYS
        if not _close(dimensions.get(key), float(geometry[key]))
    ]
    nose = ((meta.get("geometry") or {}).get("nose_shape") or {}).get("key")
    if nose != geometry["nose_shape"]:
        wrong.append(f"nose_shape: 点云 {nose} ≠ 库 {geometry['nose_shape']}")
    _check(checks, "点云存在且参数与库一致", not wrong,
           "主尺寸与头型一致（翼型参数尚未写进点云）" if not wrong else "; ".join(wrong),
           blocking=POINT_CLOUD_BLOCKING)
    return db.workspace_rel(meta_files[0]) if not wrong else None


def _fingerprint(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    return db.file_fingerprint(path)


def _verdict(conn, kind: str, geometry_id: str, condition_id, run_id: str,
             checks: list, artifact: dict | None, extra: dict | None = None) -> dict:
    blocking_failed = [c for c in checks if c["blocking"] and not c["ok"]]
    verdict = "rejected" if blocking_failed else "passed"
    payload = {
        "schemaVersion": 1,
        "kind": kind,
        "geometry_id": geometry_id,
        "condition_id": condition_id,
        "run_id": run_id,
        "reviewer": "script:review.py",
        "verdict": verdict,
        "checks": checks,
        "artifact": artifact,
    }
    if extra:
        payload.update(extra)
    return payload


# =============================================================================
# 4. 其余三个 kind（P3 / P5 / P4 接入）
# =============================================================================
def review_mesh(conn, run_id: str) -> dict:
    """网格审核（§7.2）。十条判据，其中第 10 条（单元数突变）只告警。

    产物都在 `runs/high/fluent_meshing/<run_id>/`：`quality.json`、`mesh-inspection.json`、
    `validation.trn`、`run.json`，以及 `.msh.h5`（`dispatch.py` 传 `--out` 时叫
    `<geometry_id>_mesh.msh.h5`，手工运行是 Fluent 默认的 `missile_mesh.msh.h5`）。

    2026-10-31 已在真机 Fluent 2024 R1 上跑通（449 万单元、OQ_min 0.0738），
    字段名按实际产物校正过两处：
      * 包围盒的基准是建模 `result.json` 的 `regions.enclosure.extentsM`（外壳），
        不是 `measurements.extentsM`（弹体 3.69 m）——拿弹体比会假失败 500% 以上。
      * `quality.json` 可能没有 `bboxM`：真机 `mesh_size` 输出不带单位括号，
        旧解析器要求 `min (mm) =`。现在按 `P_IMPORT["LengthUnit"]` 补单位，
        审核侧则在缺 `bboxM` 时回退解析本次运行的 `validation.trn`（不改已入账产物）。
    """
    run = db.resolve_run(conn, run_id)
    if run is None:
        raise SystemExit(f"库里没有 run_id {run_id} 对应的网格任务")
    if run["stage"] != "fluent_meshing":
        raise SystemExit(f"run_id {run_id} 属于 {run['stage']}，不是网格阶段")
    geometry_id = run["geometry_id"]
    run_dir = _run_directory("fluent_meshing", run_id)
    checks: list = []
    if not _check(checks, "运行目录存在", run_dir.is_dir(), str(run_dir)):
        return _verdict(conn, "mesh", geometry_id, None, run_id, checks, None)

    manifest = {}
    try:
        manifest = _read_json(run_dir / "run.json")
        verified = bool((manifest.get("verification") or {}).get("runtimeVerified"))
        _check(checks, "run.json status=PASSED 且 runtimeVerified=true",
               manifest.get("status") == "PASSED" and verified,
               f"status={manifest.get('status')} runtimeVerified={verified}")
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        _check(checks, "run.json status=PASSED 且 runtimeVerified=true", False, str(exc))

    quality = {}
    try:
        quality = _read_json(run_dir / "quality.json")
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        _check(checks, "quality.json 可读", False, str(exc))

    # 判据 2：最小正交质量
    oq = quality.get("minimumOrthogonalQuality")
    _check(checks, f"最小正交质量 ≥ {ORTHOGONAL_QUALITY_MIN:g}",
           isinstance(oq, (int, float)) and float(oq) >= ORTHOGONAL_QUALITY_MIN,
           f"OQ_min={oq}（阈值已从 0.01 提到 {ORTHOGONAL_QUALITY_MIN:g}）")

    # 判据 3：最小单元体积 > 0
    volume = quality.get("minimumCellVolume")
    _check(checks, "最小单元体积 > 0",
           isinstance(volume, (int, float)) and float(volume) > 0, f"minCellVolume={volume}")

    inspection = {}
    try:
        inspection = _read_json(run_dir / "mesh-inspection.json")
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        _check(checks, "mesh-inspection.json 可读", False, str(exc))

    # 判据 4/5：三个边界 zone + 恰好一个流体 cell zone
    boundaries = {item.get("name") for item in (inspection.get("boundaryZones")
                                                or quality.get("boundaryZones") or [])}
    missing = [name for name in REQUIRED_BOUNDARY_ZONES if name not in boundaries]
    _check(checks, "inlet / outlet / wall 三个面 zone 均非空", not missing,
           "齐全" if not missing else f"缺 {missing}（实际 {sorted(boundaries)}）")
    fluids = inspection.get("fluidRegions") or quality.get("fluidRegions") or []
    _check(checks, "恰好一个非空的流体 cell zone", len(fluids) == 1,
           f"{len(fluids)} 个：{[item.get('name') for item in fluids]}")

    # 判据 6：quality.json 的单元数与导出文件一致
    exported = inspection.get("cellCount") or inspection.get("cell_count")
    counted = quality.get("qualityCellCount")
    _check(checks, "单元数与导出文件一致",
           counted is not None and exported is not None and int(counted) == int(exported),
           f"quality={counted} 导出={exported}")

    # 判据 7：3D、单位 mm
    dimension = inspection.get("dimension") or inspection.get("dimensionality")
    units = inspection.get("units")
    _check(checks, "网格是 3D、单位 mm",
           str(dimension).upper().startswith("3") and str(units).lower() in ("mm", "millimeter", "millimetre"),
           f"dimension={dimension} units={units}")

    # 判据 8：网格域 bbox 与建模 extentsM 相差 ≤ 10%
    # `bboxM` 缺失时回退去解析本次运行自己的 `validation.trn`：那是 Fluent 会话输出的
    # 原始证据，用它推导 bbox 不改动任何已入账产物（`quality.json` 在 run.json 里，
    # 改它会破坏 sha256 证据链）。单位取独立核验产物的 `units`，不猜。
    bbox = inspection.get("bboxM") or quality.get("bboxM") or {}
    bbox_source = "artifact" if bbox else "transcript"
    if not bbox:
        bbox = _bbox_from_transcript(run_dir, inspection.get("units") or quality.get("units"))
    model_extents = (conn.execute(
        "SELECT model_path, model_run_id FROM geometry_state WHERE geometry_id=?",
        (geometry_id,)).fetchone())
    extent_error = None
    extent_note = "缺少可比对的 bbox"
    if model_extents is not None and model_extents["model_run_id"]:
        try:
            model_result = _read_json(_run_directory("spaceclaim", model_extents["model_run_id"]) / "result.json")
            # 基准是**外壳**（`regions.enclosure.extentsM`），不是导弹本体：
            # `measurements.extentsM` 是弹体 3.69 / 0.64 / 0.64，网格域是外壳
            # 23.83 / 5.4 / 5.4，拿弹体比会得到 500% 以上的假失败。
            enclosure = ((model_result.get("regions") or {}).get("enclosure") or {}).get("extentsM") or {}
            pairs = [(float(bbox[key]), float(enclosure[key]))
                     for key in ("axial", "lateral", "vertical")
                     if isinstance(bbox.get(key), (int, float)) and enclosure.get(key)]
            if pairs:
                extent_error = max(abs(measured - expected) / expected for measured, expected in pairs)
                extent_note = (f"相差 {extent_error:.2%}（bbox 来源：{bbox_source}；"
                               f"实测 {bbox.get('axial'):.3f}/{bbox.get('lateral'):.3f}/"
                               f"{bbox.get('vertical'):.3f} m，外壳 "
                               f"{enclosure.get('axial'):.3f}/{enclosure.get('lateral'):.3f}/"
                               f"{enclosure.get('vertical'):.3f} m）")
            else:
                extent_note = "缺少可比对的 bbox 或外壳尺寸（`regions.enclosure.extentsM`）"
        except (FileNotFoundError, json.JSONDecodeError):
            extent_error = None
            extent_note = "建模 result.json 不可读"
    _check(checks, f"网格域包围盒与建模外壳相差 ≤ {MESH_BBOX_TOLERANCE_REL:.0%}",
           extent_error is not None and extent_error <= MESH_BBOX_TOLERANCE_REL,
           extent_note)

    # 判据 9：同一几何单元数较上次通过值突变 > 50% → 只告警
    previous_cells = _previous_cell_count(conn, geometry_id, run_id)
    if counted is None or previous_cells is None:
        cells_ok, cells_detail = True, f"本次 {counted}，无可比对的历史通过值"
    else:
        delta = abs(int(counted) - previous_cells) / previous_cells if previous_cells else 0.0
        cells_ok = delta <= MESH_CELL_COUNT_TOLERANCE_REL
        cells_detail = (f"本次 {counted}，上次通过 {previous_cells}，相差 {delta:.1%}"
                        + ("" if cells_ok else f"（超过 {MESH_CELL_COUNT_TOLERANCE_REL:.0%}）"))
    _check(checks, "单元数较上次通过值未突变（仅告警）", cells_ok, cells_detail, blocking=False)

    # 网格产物：优先按 `<geometry_id>*.msh.h5`（`dispatch.py` 显式传 `--out` 时的名字），
    # 否则退回运行目录里唯一的 `.msh.h5`（Fluent 默认名 `missile_mesh.msh.h5`）。
    mesh_files = sorted(run_dir.glob(f"{geometry_id}*.msh.h5")) or sorted(run_dir.glob("*.msh.h5"))
    artifact = _fingerprint(mesh_files[0]) if mesh_files else None
    _check(checks, "网格文件存在", artifact is not None,
           str(mesh_files[0]) if mesh_files else f"运行目录里没有 .msh.h5：{run_dir}")
    return _verdict(conn, "mesh", geometry_id, None, run_id, checks, artifact)


CELL_COUNT_CHECK_NAME = "单元数与导出文件一致"


def _bbox_from_transcript(run_dir: Path, units: str | None) -> dict:
    """从本次运行的 `validation.trn` 里解析网格域包围盒（米）。

    复用 `fluent_meshing/mesh_validation.py` 的解析器，避免两份正则漂移。
    单位只用调用方给的 `units`（来自独立核验产物），传不进去就返回空字典。
    """
    transcript = run_dir / TRANSCRIPT_NAME
    if units is None or not transcript.exists():
        return {}
    sys.path.insert(0, str(db.WORKSPACE_ROOT / "fluent_meshing"))
    try:
        import mesh_validation
    except ImportError:
        return {}
    text = transcript.read_text(encoding="utf-8", errors="replace")
    extents = mesh_validation.parse_domain_extents(text, default_units=units)
    return (extents or {}).get("bboxM") or {}


def _previous_cell_count(conn, geometry_id: str, run_id: str) -> int | None:
    """上一份**通过审核**的网格的单元数。

    `review` 表没有单独的单元数列，值存在 `checks_json` 的那条判据里
    （detail 形如 `quality=4490131 导出=4490131`），所以从历史记录里读回来。
    """
    rows = conn.execute(
        "SELECT checks_json FROM review WHERE kind='mesh' AND geometry_id=?"
        " AND verdict='passed' AND run_id<>? ORDER BY created_at DESC LIMIT 5",
        (geometry_id, run_id)).fetchall()
    for row in rows:
        try:
            items = json.loads(row["checks_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        for item in items:
            if item.get("name") != CELL_COUNT_CHECK_NAME:
                continue
            match = re.search(r"quality=(\d+)", str(item.get("detail") or ""))
            if match:
                return int(match.group(1))
    return None


def review_solution(conn, run_id: str) -> dict:
    """求解审核（§7.3）。求解脚本本轮仍是空壳（P5 只建骨架），所以这条判据先定死、
    等脚本接入后直接生效；现在跑它只会得到"求解产物不存在"的否决，不会假装通过。

    求解器要写的产物（约定在这里定义，P5 之后照此实现）：
        data_lib/runs/high/fluent_solution/<run_id>/result.json
        {
          "status": "PASS",
          "converged": true,
          "residual": 3.1e-5,              # 连续性残差
          "massImbalance": 0.0004,          # |ṁ_in − ṁ_out| / ṁ_in
          "history": [{"step": 1, "CL": .., "CD": .., "CM": ..}, ...],   # 至少 200 步
          "CL": .., "CD": .., "CM": ..      # 最终值；缺省取 history 末条
        }
    """
    run = db.resolve_run(conn, run_id)
    if run is None:
        raise SystemExit(f"库里没有 run_id {run_id} 对应的求解任务")
    if run["stage"] != "fluent_solution":
        raise SystemExit(f"run_id {run_id} 属于 {run['stage']}，不是求解阶段")
    geometry_id, condition_id = run["geometry_id"], run["condition_id"]
    run_dir = _run_directory("fluent_solution", run_id)
    checks: list = []

    manifest_path = run_dir / "result.json"
    if not _check(checks, "求解产物 result.json 存在", manifest_path.is_file(), str(manifest_path)):
        return _verdict(conn, "solution", geometry_id, condition_id, run_id, checks, None)

    try:
        result = _read_json(manifest_path)
    except json.JSONDecodeError as exc:
        _check(checks, "求解产物可读", False, str(exc))
        return _verdict(conn, "solution", geometry_id, condition_id, run_id, checks, None)

    history = result.get("history") or []
    last = history[-1] if history else {}
    coefficients = {
        key: result.get(key, last.get(key)) for key in ("CL", "CD", "CM")
    }

    # 判据 1：连续性残差
    residual = result.get("residual")
    _check(checks, f"连续性残差 < {RESIDUAL_MAX:g}",
           isinstance(residual, (int, float)) and float(residual) < RESIDUAL_MAX,
           f"residual={residual}")

    # 判据 2：最后 200 步的三个系数相对波动 < 0.5%
    window = history[-FLUCTUATION_WINDOW_STEPS:]
    fluctuation = {}
    ok_window = len(window) >= FLUCTUATION_WINDOW_STEPS
    for key in ("CL", "CD", "CM"):
        values = [float(item[key]) for item in window if isinstance(item.get(key), (int, float))]
        if len(values) < 2:
            fluctuation[key] = None
            ok_window = False
            continue
        mean = sum(values) / len(values)
        amplitude = max(values) - min(values)
        fluctuation[key] = amplitude / abs(mean) if mean else float("inf")
    _check(checks, f"最后 {FLUCTUATION_WINDOW_STEPS} 步系数波动 < {FLUCTUATION_MAX_REL:.1%}",
           ok_window and all(
               value is not None and value < FLUCTUATION_MAX_REL
               for value in fluctuation.values()),
           ", ".join(f"{k}={'-' if v is None else f'{v:.4%}'}"
                     for k, v in fluctuation.items())
           + f"（窗口 {len(window)} 步）")

    # 判据 3：进出口质量流量不平衡
    imbalance = result.get("massImbalance")
    _check(checks, f"进/出口质量流量不平衡 < {MASS_IMBALANCE_MAX_REL:.1%}",
           isinstance(imbalance, (int, float)) and abs(float(imbalance)) < MASS_IMBALANCE_MAX_REL,
           f"massImbalance={imbalance}")

    # 判据 4：三个系数有限且 CD > 0
    finite = all(isinstance(value, (int, float)) and math.isfinite(float(value))
                 for value in coefficients.values())
    _check(checks, "三个系数均为有限值且 CD > 0",
           finite and isinstance(coefficients.get("CD"), (int, float))
           and float(coefficients["CD"]) > 0,
           ", ".join(f"{k}={v}" for k, v in coefficients.items()))

    _check(checks, "求解自报 status=PASS 且 converged",
           result.get("status") == "PASS" and result.get("converged") is True,
           f"status={result.get('status')} converged={result.get('converged')}")

    # 与 DATCOM 对比：只告警不否决（跨声速区差 20–40% 属正常）
    datcom = conn.execute(
        "SELECT CL, CD, CM FROM datcom_task WHERE geometry_id=? AND condition_id=? AND status='passed'",
        (geometry_id, condition_id),
    ).fetchone()
    if datcom is not None:
        deltas = {}
        for key in ("CL", "CD", "CM"):
            cfd, low = coefficients.get(key), datcom[key]
            if isinstance(cfd, (int, float)) and isinstance(low, (int, float)) and low:
                deltas[key] = abs(float(cfd) - float(low)) / abs(float(low))
        worst = ", ".join(f"{key} {value:.1%}" for key, value in deltas.items())
        _check(checks, "与 DATCOM 的差异在告警阈值内（不否决）",
               all(value <= DATCOM_WARN_TOLERANCE[key] for key, value in deltas.items()),
               worst or "没有可比的系数", blocking=False)
    else:
        _check(checks, "与 DATCOM 的差异在告警阈值内（不否决）", True,
               "该工况没有通过审核的低保真数据，跳过对比", blocking=False)

    artifact = _fingerprint(manifest_path)
    return _verdict(conn, "solution", geometry_id, condition_id, run_id, checks, artifact,
                    {"CL": coefficients.get("CL"), "CD": coefficients.get("CD"),
                     "CM": coefficients.get("CM"), "residual": residual,
                     "converged": result.get("converged") is True})


def review_datcom(conn, run_id: str) -> dict:
    """低保真脚本自检（§7.4）：没有 LLM 审核子智能体，只有这几条确定性判据。

    一次 DATCOM 调用覆盖"同一几何 + 同一高度"下的多个工况，所以这里按批次核，
    但入库存的是**每个工况一条** `datcom_task` 结论（`main()` 负责拆开）。
    """
    rows = conn.execute(
        "SELECT t.geometry_id, t.condition_id, t.status, c.mach, c.alpha_deg, c.altitude_m"
        " FROM datcom_task t JOIN condition c ON c.condition_id = t.condition_id"
        " WHERE t.run_id=? ORDER BY t.condition_id",
        (run_id,),
    ).fetchall()
    if not rows:
        raise SystemExit(f"库里没有 run_id {run_id} 对应的低保真任务（先用 dispatch 领一批）")
    geometry_id = rows[0]["geometry_id"]
    geometry = _geometry_row(conn, geometry_id)
    run_dir = db.WORKSPACE_ROOT / "data_lib/runs/low" / geometry_id
    manifest_path = run_dir / run_id / "run.json"
    checks: list = []

    if not _check(checks, "运行记录存在", manifest_path.is_file(), str(manifest_path)):
        return _verdict(conn, "datcom", geometry_id, None, run_id, checks, None, {"conditions": []})
    try:
        manifest = _read_json(manifest_path)
    except json.JSONDecodeError as exc:
        _check(checks, "运行记录可读", False, str(exc))
        return _verdict(conn, "datcom", geometry_id, None, run_id, checks, None, {"conditions": []})

    # 判据 4（前半）：进程退出码 0 且解析到的工况数等于预期
    datcom = manifest.get("datcom") or {}
    expected = int(datcom.get("expectedConditions") or 0)
    parsed = int(datcom.get("parsedConditions") or 0)
    _check(checks, "datcom 退出码 0 且工况数吻合",
           datcom.get("returncode") == 0 and expected == parsed and expected > 0,
           f"returncode={datcom.get('returncode')} parsed={parsed} expected={expected}")

    # 判据 1：几何核对报告没有未通过项
    geometry_checks = manifest.get("geometryChecks") or {}
    failed = int(geometry_checks.get("failed") or 0)
    detail = f"{geometry_checks.get('total')} 项，失败 {failed}"
    if failed:
        detail += "：" + "; ".join((geometry_checks.get("messages") or [])[:3])
    _check(checks, "几何核对没有未通过项", failed == 0, detail)

    # 判据 2：系数表行数 == 本批领取的工况数
    conditions = manifest.get("conditions") or []
    _check(checks, "系数表行数等于本批工况数", len(conditions) == len(rows),
           f"表里 {len(conditions)} 行，库里 {len(rows)} 个工况")

    # 判据 3：每行的马赫数 / 攻角 / 高度都能对上库里的 condition
    wanted = {row["condition_id"]: row for row in rows}
    mismatched = []
    for item in conditions:
        row = wanted.get(str(item.get("condition_id") or ""))
        if row is None:
            mismatched.append(f"{item.get('condition_id')} 不在库里")
            continue
        if (not _close(item.get("mach"), row["mach"])
                or not _close(item.get("alpha_deg"), row["alpha_deg"])
                or not _close(item.get("altitude_m"), row["altitude_m"])):
            mismatched.append(str(row["condition_id"]))
    _check(checks, "每行的来流与 condition 表一致", not mismatched,
           "一致" if not mismatched else "对不上：" + ", ".join(mismatched[:5]))

    # 判据 5：翼型与头型就是库里那一种（卡片按它写，系数才有意义）
    shape = manifest.get("shape") or {}
    section = shape.get("fin_section") or {}
    params = section.get("params") or {}
    nose = (shape.get("nose_shape") or {}).get("key")
    wrong = []
    if str(section.get("kind") or "").lower() != geometry["fin_section_type"]:
        wrong.append(f"翼型种类 {section.get('kind')} ≠ 库 {geometry['fin_section_type']}")
    if nose != geometry["nose_shape"]:
        wrong.append(f"头型 {nose} ≠ 库 {geometry['nose_shape']}")
    if not _close(params.get("thickness_percent"), geometry["thickness_percent"], 1e-2):
        wrong.append(f"厚度 {params.get('thickness_percent')} ≠ 库 {geometry['thickness_percent']}")
    if geometry["fin_section_type"] == "hex":
        if not _close(params.get("flat_percent"), geometry["flat_percent"], 1e-2):
            wrong.append(f"平顶 {params.get('flat_percent')} ≠ 库 {geometry['flat_percent']}")
    _check(checks, "卡片用的翼型/头型与库内参数一致", not wrong,
           "一致（DATCOM 卡片只保留 0.01% 格点）" if not wrong else "; ".join(wrong))

    csv_path = run_dir / f"{geometry_id}_aero_coefficients.csv"
    artifact = _fingerprint(csv_path) if csv_path.exists() else None
    _check(checks, "系数总表存在", artifact is not None, str(csv_path))

    per_condition = []
    for item in conditions:
        row = wanted.get(str(item.get("condition_id") or ""))
        if row is None:
            continue
        per_condition.append({
            "condition_id": row["condition_id"],
            "CL": item.get("CL"), "CD": item.get("CD"), "CM": item.get("CM"),
        })
    return _verdict(conn, "datcom", geometry_id, None, run_id, checks, artifact,
                    {"conditions": per_condition, "result_dir": db.workspace_rel(run_dir)})


REVIEWERS = {
    "model": review_model,
    "mesh": review_mesh,
    "solution": review_solution,
    "datcom": review_datcom,
}


# =============================================================================
# 5. 命令行
# =============================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review.py",
        description="确定性审核：算出 checks 清单，写审核 JSON；--record 时经 db.py 入库。",
    )
    parser.add_argument("--kind", required=True, choices=sorted(REVIEWERS))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--database", help="库文件路径（默认取 workspace.json）")
    parser.add_argument("--out", help="审核 JSON 输出路径（默认 data_lib/runtime/reviews/...）")
    parser.add_argument("--reviewer", default="script:review.py",
                        help="记入 review.reviewer：脚本或审核子智能体标识")
    parser.add_argument("--record", action="store_true", help="直接调用 db.py 入库")
    parser.add_argument("--json", action="store_true", help="只打印审核 JSON")
    parser.add_argument("--force", action="store_true",
                        help="放宽产物哈希核对：已过审的产物被下游（Fluent / SpaceClaim 界面）重新保存过时用，必须同时给 --force-reason")
    parser.add_argument("--force-reason", help="放宽理由，写进审核记录")
    return parser


def main(argv: list[str] | None = None) -> int:
    db._configure_output()
    args = build_parser().parse_args(argv)
    if args.force and not args.force_reason:
        print("--force 必须同时给 --force-reason，说明为什么放宽产物哈希核对。", file=sys.stderr)
        return 2
    conn = db.connect(db.resolve_database(args.database))
    try:
        # 已经 passed 的任务再跑一次审核会把状态打回 rejected 并清空产物路径。
        # 这种回归只应在显式 --force 时发生。
        run = db.resolve_run(conn, args.run_id)
        if run is not None and not args.force:
            row = conn.execute(
                f"SELECT {run['field']} AS status FROM {run['table']}"
                + (" WHERE geometry_id=?" if run["table"] == "geometry_state"
                   else " WHERE geometry_id=? AND condition_id=?"),
                (run["geometry_id"],) if run["table"] == "geometry_state"
                else (run["geometry_id"], run["condition_id"]),
            ).fetchone()
            if row is not None and row["status"] == "passed":
                print(f"{args.run_id} 已经 passed：重跑审核会把它打回 rejected。"
                      "确认要重审就加 --force --force-reason \"<理由>\"。", file=sys.stderr)
                return 2
        if args.kind == "model":
            payload = review_model(conn, args.run_id, force=args.force)
        else:
            payload = REVIEWERS[args.kind](conn, args.run_id)
    finally:
        conn.close()
    if args.reviewer:
        payload["reviewer"] = args.reviewer

    out = Path(args.out) if args.out else (
        db.WORKSPACE_ROOT / REVIEW_OUTPUT_DIR_REL / f"{args.kind}_{args.run_id}.json"
    )
    if not out.is_absolute():
        out = db.WORKSPACE_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        blocking = [c for c in payload["checks"] if c["blocking"]]
        warnings = [c for c in payload["checks"] if not c["blocking"]]
        print(f"{payload['kind']} 审核 {args.run_id} → {payload['verdict']}")
        for item in payload["checks"]:
            mark = "通过" if item["ok"] else ("否决" if item["blocking"] else "告警")
            print(f"  [{mark}] {item['name']}" + (f"  {item['detail']}" if item["detail"] else ""))
        print(f"  否决性判据 {len(blocking)} 条，告警性 {len(warnings)} 条")
        print(f"  审核记录：{db.workspace_rel(out)}")

    if args.record:
        conn = db.connect(db.resolve_database(args.database))
        try:
            if args.kind == "datcom":
                # 一批覆盖多个工况：每个工况各记一条 review（datcom_task 是一工况一行）
                per_condition = payload.get("conditions") or []
                results = []
                for item in per_condition:
                    single = dict(payload)
                    single.pop("conditions", None)
                    single["condition_id"] = item["condition_id"]
                    single["CL"] = item.get("CL")
                    single["CD"] = item.get("CD")
                    single["CM"] = item.get("CM")
                    results.append(db.record_review(conn, single))
                result = results if len(results) != 1 else results[0]
            else:
                result = db.record_review(conn, payload)
        finally:
            conn.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if payload["verdict"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
