# -*- coding: utf-8 -*-
"""Diagnostic tool: show which names a model exposes to the meshing workflow.

Runs the recorded workflow up to the Update Regions step and prints the labels,
boundary zones and regions Fluent can see for the imported geometry. Use it when
a run fails with a name-mapping error.

    python tools/probe_regions.py --geometry ..\data_lib\runs\<run>\spaceclaim\missile.scdoc
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import mesh_missile as M  # noqa: E402


# ---------------------------------------------------------------------------
# 输入 / 输出 / 参数配置
# ---------------------------------------------------------------------------
# 输入：--geometry 必填，相对路径按调用时工作目录解析；-n/--processors
# 覆盖 DEFAULT_PROCESSORS。几何/工作流参数使用 mesh_missile.py 前部 P_*。
# 输出：打印到标准输出，不主动导出网格或报告；Fluent 会话工作目录仍为
# PROJECT。许可写入探测及必要的临时 APPDATA 路径由 M 的前部配置控制。
# PROJECT 仅用于脚本定位和既有会话工作目录，不作为默认输入文件。
DEFAULT_PROCESSORS = 2
DISPLAY_VALUE_LIMIT = 4000
# 版本、维度、精度和工作流名须与既有 Fluent 协议保持一致。
FLUENT_PRODUCT_VERSION = '24.1'
FLUENT_DIMENSION = '3d'
FLUENT_PRECISION = 'double'
FLUENT_WORKFLOW_TYPE = 'Watertight Geometry'


def show(label, value) -> None:
    print(f"--- {label} ---")
    print(str(value)[:DISPLAY_VALUE_LIMIT])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--geometry', required=True)
    parser.add_argument('-n', '--processors', type=int, default=DEFAULT_PROCESSORS)
    options = parser.parse_args()
    M.GEOMETRY_FILE = str(Path(options.geometry).resolve())
    M.ensure_writable_appdata(print, None)
    import ansys.fluent.core as pyfluent
    meshing = pyfluent.launch_fluent(product_version=FLUENT_PRODUCT_VERSION, mode='meshing', version=FLUENT_DIMENSION, precision=FLUENT_PRECISION,
                                     processor_count=options.processors, start_transcript=False, show_gui=False,
                                     start_watchdog=False, cleanup_on_exit=False, cwd=str(PROJECT))
    try:
        wf = meshing.workflow
        wf.InitializeWorkflow(WorkflowType=FLUENT_WORKFLOW_TYPE)
        M.step_import(print, wf, meshing)
        show('imported labels and zones', M.inspect_names(meshing, print))
        M.step_local_sizing(print, wf)
        M.step_surface_mesh(print, wf)
        M.step_describe_geometry(print, wf)
        M.step_share_topology(print, wf)
        M.step_create_regions(print, wf)
        regions = M.step_update_regions(print, wf)
        show('fluid regions after Update Regions', regions)
        for command in (('mesh', 'manage', 'list'), ('boundary', 'manage', 'list'), ('scoped_sizing', 'list')):
            try:
                value = meshing.tui
                for part in command:
                    value = getattr(value, part)
                show('tui ' + '/'.join(command), value())
            except Exception as exc:  # noqa: BLE001
                print('tui ' + '/'.join(command) + f' failed: {type(exc).__name__}: {exc}')
    finally:
        try:
            meshing.exit()
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
