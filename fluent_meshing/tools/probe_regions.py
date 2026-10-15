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


def show(label, value) -> None:
    print(f"--- {label} ---")
    print(str(value)[:4000])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--geometry', required=True)
    parser.add_argument('-n', '--processors', type=int, default=2)
    options = parser.parse_args()
    M.GEOMETRY_FILE = str(Path(options.geometry).resolve())
    M.ensure_writable_appdata(print, None)
    import ansys.fluent.core as pyfluent
    meshing = pyfluent.launch_fluent(product_version='24.1', mode='meshing', version='3d', precision='double',
                                     processor_count=options.processors, start_transcript=False, show_gui=False,
                                     start_watchdog=False, cleanup_on_exit=False, cwd=str(PROJECT))
    try:
        wf = meshing.workflow
        wf.InitializeWorkflow(WorkflowType='Watertight Geometry')
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
