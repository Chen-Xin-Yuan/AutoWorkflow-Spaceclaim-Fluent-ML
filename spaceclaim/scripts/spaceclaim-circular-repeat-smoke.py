# -*- coding: utf-8 -*-
"""Runtime acceptance test: six solids repeated every 60 degrees around Z."""

import math
from System import Environment
from System.IO import File, Path


# === 输入 / 输出 / 参数来源 ===
# 输入：SpaceClaim 宿主 API；几何和验收数值为固定 fixture，不是生产模型参数。
# 输出：下面的环境变量由 verify-geometry.mjs 注入本次 run 的实际路径。
# 模型和结果路径为必填；诊断缺省写入 TEMP，未设置 TEMP 时回退当前目录。
# 修改输出位置应在驱动入口配置，
# 保留环境变量在运行时解析，不要将某次 run 的绝对路径写回源码。
MODEL_PATH_ENV = "SCDM_RUNTIME_MODEL"
RESULT_PATH_ENV = "SCDM_RUNTIME_RESULT"
DIAGNOSTIC_PATH_ENV = "SCDM_RUNTIME_DIAGNOSTIC"
DIAGNOSTIC_FALLBACK_FILENAME = "ansys-scdmdoc-circular-repeat-diagnostic.txt"

diagnostic_path = Environment.GetEnvironmentVariable(DIAGNOSTIC_PATH_ENV) or Path.Combine(Environment.GetEnvironmentVariable("TEMP") or ".", DIAGNOSTIC_FALLBACK_FILENAME)


def required_output(name):
    value = Environment.GetEnvironmentVariable(name)
    if not value:
        raise Exception("Set {0} before running the SpaceClaim acceptance script.".format(name))
    return value


try:
    model_path = required_output(MODEL_PATH_ENV)
    result_path = required_output(RESULT_PATH_ENV)

    block_origin = Point.Create(MM(0), MM(-2), MM(0))
    corner = Point.Create(MM(20), MM(2), MM(5))
    BlockBody.Create(block_origin, corner, ExtrudeType.Add)
    base_body = GetRootPart().Bodies[-1]

    coordinate_system = GetRootPart().CoordinateSystems[0]
    axis = list(coordinate_system.Axes)[2]
    circular_pattern = CircularPatternData()
    circular_pattern.CircularAxis = Selection.Create(axis)
    circular_pattern.CircularCount = 6
    circular_pattern.CircularAngle = DEG(360)
    pattern_result = Pattern.CreateCircular(BodySelection.Create(base_body), circular_pattern)
    if not pattern_result.Success:
        raise Exception("Pattern.CreateCircular returned Success=False.")

    created_objects = list(pattern_result.CreatedObjects)
    body_objects = [obj for obj in created_objects if obj.GetType().Name in ("Body", "DesignBody", "DesignBodyGeneral")]
    bodies = body_objects
    if len(bodies) != 6:
        type_names = {}
        for obj in created_objects:
            name = obj.GetType().FullName
            type_names[name] = type_names.get(name, 0) + 1
        raise Exception("Expected 6 repeated bodies, got {0}; created types: {1}".format(len(bodies), type_names))

    angles = []
    for body in bodies:
        center = MeasureHelper.GetCenterOfMass(Selection.Create(body))
        angle = math.degrees(math.atan2(center.Y, center.X))
        if angle < 0:
            angle += 360.0
        angles.append(angle)

    angles.sort()
    expected = [0.0, 60.0, 120.0, 180.0, 240.0, 300.0]
    tolerance = 1.0e-5
    for actual, target in zip(angles, expected):
        if abs(actual - target) > tolerance:
            raise Exception("Unexpected repeated-body angle: expected {0}, got {1}.".format(target, actual))

    merge_result = Combine.Merge(Selection.Create(bodies))
    if not merge_result.Success:
        raise Exception("Combine.Merge returned Success=False.")
    merged_bodies = list(GetRootPart().Bodies)
    if len(merged_bodies) != 1:
        raise Exception("Expected 1 merged body after Combine.Merge, got {0}.".format(len(merged_bodies)))

    merged_faces = list(merged_bodies[0].Faces)
    if not merged_faces:
        raise Exception("Combine.Merge produced a body without faces.")

    DocumentSave.Execute(model_path)
    File.WriteAllText(
        result_path,
        "status=PASS\r\npattern_body_count={0}\r\nangles_deg={1}\r\nstep_deg=60\r\ncoverage_deg=360\r\nmerged_body_count={2}\r\nmerged_face_count={3}\r\n".format(
            len(bodies), ",".join(["{0:.6f}".format(angle) for angle in angles]), len(merged_bodies), len(merged_faces)
        ),
    )
except Exception as error:
    File.WriteAllText(diagnostic_path, "status=FAIL\r\nerror={0}\r\n".format(error))
    raise
