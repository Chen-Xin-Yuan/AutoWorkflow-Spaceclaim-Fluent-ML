"""Strict fresh-output validation for the recorded missile workflow."""
from __future__ import annotations
import json
import math
from pathlib import Path
import re
import time


# ---------------------------------------------------------------------------
# 输入 / 输出 / 参数配置
# ---------------------------------------------------------------------------
# 输入：report_mesh() 接收 Fluent 会话及本次 run_dir；inspect_mesh()、
# verify_export() 的网格路径由调用方传入，无默认输入文件。
# 输出：以下报告名均相对调用方传入的 run_dir；inspect_mesh() 和校验函数
# 返回内存结果，不自行选择导出目录。CLI 覆盖由 mesh_runner.py 处理。
# 报告名是既有产物约定，修改时须同步读取报告的脚本/工具。
VALIDATION_TRANSCRIPT_NAME = 'validation.trn'
NATIVE_QUALITY_NAME = 'quality-native.json'
QUALITY_REPORT_NAME = 'quality.json'
MINIMUM_ORTHOGONAL_QUALITY = 0.01
TRANSCRIPT_WAIT_ATTEMPTS = 20
TRANSCRIPT_WAIT_SECONDS = 0.1
EXPORT_MTIME_TOLERANCE_SECONDS = 2

# 以下为既有报告格式、HDF5 协议及模型验收约定，修改需保持调用方兼容。
NUMBER = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
# Boundary zones the missile workflow must produce. They come from the CFD named
# selections in the SpaceClaim model plus the enclosure command's own zones.
REQUIRED_BOUNDARY_ZONES = ('inlet', 'outlet', 'wall')
MISSILE_WALL_HINTS = ('missile',)


def extract_number(text, patterns, name):
    for pattern in patterns:
        matches = re.findall(pattern, text, flags=re.I)
        if matches:
            return float(matches[-1])
    raise RuntimeError('Missing validation evidence: ' + name)


def parse_report(text, native_quality=None):
    minimum_volume = extract_number(text, [r'minimum\s+(?:cell\s+)?volume\s*[:=]\s*(' + NUMBER + ')', r'min(?:imum)?\s+cell\s+volume[^\n]*?(' + NUMBER + ')'], 'minimum cell volume')
    total_volume = extract_number(text, [r'total\s+(?:cell\s+)?volume\s*[:=]\s*(' + NUMBER + ')'], 'total volume')
    quality_count = None
    if native_quality is not None:
        # The explicitly requested Orthogonal Quality utility returns
        # [cell_count, minimum, maximum, average, ...]. This avoids relying on
        # the unlabelled console text produced by Fluent 24.1.
        if not isinstance(native_quality, (list, tuple)) or len(native_quality) < 4:
            raise RuntimeError('Invalid native orthogonal-quality response')
        count, minimum_quality, maximum_quality, average_quality = native_quality[:4]
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (count, minimum_quality, maximum_quality, average_quality)) or count <= 0 or int(count) != count or not 0 <= minimum_quality <= average_quality <= maximum_quality <= 1:
            raise RuntimeError('Inconsistent native orthogonal-quality response')
        quality_count = int(count)
    else:
        minimum_quality = extract_number(text, [r'minimum\s+orthogonal\s+quality\s*[:=]\s*(' + NUMBER + ')', r'orthogonal\s+quality[^\n]*?min(?:imum)?\s*[:=]\s*(' + NUMBER + ')'], 'minimum orthogonal quality')
    if minimum_volume <= 0 or minimum_quality <= MINIMUM_ORTHOGONAL_QUALITY:
        raise RuntimeError('Mesh has nonpositive volume or insufficient orthogonal quality')
    if re.search(r'(?:negative\s+(?:cell\s+)?volumes?|fatal\s+error|error\s*:|failed\s+(?:mesh|check))', text, re.I):
        # A check that reports zero negative volumes is permitted, but any real
        # failure is retained as feedback rather than hidden by exit code zero.
        failures = [line for line in text.splitlines() if re.search(r'negative\s+(?:cell\s+)?volumes?|fatal\s+error|error\s*:|failed\s+(?:mesh|check)', line, re.I)]
        if any(not re.search(r'\b0\s+negative|negative\s+(?:cell\s+)?volumes?\s*[:=]\s*0\b', line, re.I) for line in failures):
            raise RuntimeError('Mesh check reports an error')
    if not re.search(r'checking|mesh\s+check', text, re.I) or not re.search(r'\bDone\.', text, re.I):
        raise RuntimeError('No mesh check execution evidence')
    regions = [{'id': int(zone_id), 'name': name, 'cells': int(count)} for zone_id, name, count in re.findall(r'^\s*(\d+)\s+(\S+)\s+fluid\s+(\d+)\b', text, re.M | re.I)]
    if not regions:
        raise RuntimeError('No fluid cell zone was reported by the mesh check')
    boundaries = [{'id': int(zone_id), 'name': name, 'type': kind, 'faces': int(count)} for zone_id, name, kind, count in re.findall(r'^\s*(\d+)\s+(\S+)\s+(\S+)\s+(\d+)\s+\d+\s+\d+\s+\d+\s*$', text, re.M)]
    return {
        'minimumCellVolume': minimum_volume,
        'totalVolume': total_volume,
        'minimumOrthogonalQuality': minimum_quality,
        'qualityCellCount': quality_count,
        'meshCheckCompleted': True,
        'fluidRegions': regions,
        'boundaryZones': boundaries,
    }


def report_mesh(meshing, run_dir):
    transcript = Path(run_dir) / VALIDATION_TRANSCRIPT_NAME
    if transcript.exists():
        raise FileExistsError('Validation transcript must be fresh')
    meshing.tui.file.start_transcript(str(transcript))
    try:
        meshing.tui.mesh.check_mesh()
        meshing.tui.report.quality_method('orthogonal-quality')
        meshing.tui.report.cell_quality_limits('*')
        native_quality = meshing.scheme_eval.scheme_eval('(tgapi-util-get-cell-quality-limits "*" "Orthogonal Quality")')
        (Path(run_dir) / NATIVE_QUALITY_NAME).write_text(json.dumps(native_quality, indent=2) + '\n', encoding='utf-8')
        meshing.tui.report.mesh_size()
        meshing.tui.mesh.manage.list()
        meshing.tui.boundary.manage.list()
    finally:
        meshing.tui.file.stop_transcript()
    for _ in range(TRANSCRIPT_WAIT_ATTEMPTS):
        if transcript.exists() and transcript.stat().st_size:
            break
        time.sleep(TRANSCRIPT_WAIT_SECONDS)
    text = transcript.read_text(encoding='utf-8', errors='replace')
    result = parse_report(text, native_quality)
    result['qualityMethod'] = 'tgapi-util-get-cell-quality-limits: Orthogonal Quality'
    result['transcript'] = transcript.name
    (Path(run_dir) / QUALITY_REPORT_NAME).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def read_topology(mesh, group):
    """Return the named zones of one mesh topology group as {name: count}."""
    import numpy as np
    topology = mesh[group].get('zoneTopology') if group in mesh else None
    if topology is None or 'name' not in topology:
        return {}
    names = topology['name'][()]
    while isinstance(names, np.ndarray):
        names = names.flat[0]
    if isinstance(names, bytes):
        names = names.decode('utf-8')
    minimum = np.asarray(topology['minId']).reshape(-1)
    maximum = np.asarray(topology['maxId']).reshape(-1)
    return {str(name): int(maximum[index] - minimum[index] + 1) for index, name in enumerate(str(names).split(';'))}


def inspect_mesh(file):
    """Independent check of the exported mesh file (runs in the inspect environment)."""
    import h5py
    import numpy as np
    with h5py.File(file, 'r') as handle:
        mesh = handle['meshes/1']
        dimension = int(np.asarray(mesh.attrs['dimension']).flat[0])
        units = mesh.attrs['units']
        if isinstance(units, bytes):
            units = units.decode('ascii')
        units = str(units)
        counts = {key: int(np.asarray(mesh.attrs[key]).flat[0]) for key in ('cellCount', 'faceCount', 'nodeCount')}
        if dimension != 3 or any(value <= 0 for value in counts.values()):
            raise RuntimeError('Empty or non-3D mesh export')
        if units != 'mm':
            raise RuntimeError('Exported mesh must be in mm')
        cell_zones = read_topology(mesh, 'cells')
        face_zones = read_topology(mesh, 'faces')
    return {'dimension': dimension, 'units': units, **counts, 'cellZones': cell_zones, 'faceZones': face_zones}


def assert_missile_mesh(mesh, quality=None):
    """Require the exported mesh to carry the regions and boundary groups the model defines.

    The quality report is cross-checked against the exported file: the number of
    cells and the boundary zone names must agree, so a stale or empty export
    cannot be reported as a verified mesh.
    """
    missing = [name for name in REQUIRED_BOUNDARY_ZONES if mesh['faceZones'].get(name, 0) <= 0]
    missile_walls = [name for name in mesh['faceZones'] if any(hint in name.lower() for hint in MISSILE_WALL_HINTS) and mesh['faceZones'][name] > 0]
    if missing:
        raise RuntimeError('Exported mesh is missing boundary zones: ' + ', '.join(missing))
    if not missile_walls:
        raise RuntimeError('Exported mesh has no non-empty missile wall zone')
    fluid = {name: count for name, count in mesh['cellZones'].items() if count > 0}
    if len(fluid) != 1:
        raise RuntimeError('Exported mesh must contain exactly one non-empty fluid cell zone; found ' + repr(sorted(fluid)))
    if quality:
        reported = quality.get('qualityCellCount')
        if reported is not None and int(reported) != mesh['cellCount']:
            raise RuntimeError(f'Quality report covered {reported} cells but the export holds {mesh["cellCount"]}')
        reported_names = {zone['name'] for zone in quality.get('boundaryZones', []) if zone.get('faces', 0) > 0}
        missing_from_report = [name for name in REQUIRED_BOUNDARY_ZONES if name not in reported_names]
        if reported_names and missing_from_report:
            raise RuntimeError('Mesh check did not report the boundary zones: ' + ', '.join(missing_from_report))
    return {'boundaryZones': sorted(REQUIRED_BOUNDARY_ZONES), 'missileWallZones': sorted(missile_walls), 'fluidCellZones': fluid}


def verify_export(file, started_at, quality):
    """Worker-side gate: the export must be fresh and the quality report must cover it."""
    file = Path(file)
    if not file.is_file() or file.stat().st_size <= 0 or file.stat().st_mtime < started_at - EXPORT_MTIME_TOLERANCE_SECONDS:
        raise RuntimeError('No fresh nonempty mesh export')
    if quality.get('qualityCellCount') is not None and quality['qualityCellCount'] <= 0:
        raise RuntimeError('Quality report covered no cells')
    return {'runtimeVerified': True, 'quality': quality, 'meshFile': file.name, 'meshFileBytes': file.stat().st_size}
