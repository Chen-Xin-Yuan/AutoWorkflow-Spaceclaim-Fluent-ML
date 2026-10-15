#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Automated Fluent Meshing (Watertight Geometry) for ``missile.scdoc``.

The script drives ANSYS Fluent Meshing 2024 R1 through PyFluent and reproduces
the workflow that is recorded in ``fluent_meshing.md`` (which was extracted from
the previously generated ``missile.msh.h5``):

    1. Import Geometry          -  missile.scdoc, mm, feature angle 40 deg
    2. Add Local Sizing         -  4 curvature controls + 1 body-of-influence
                                   control (missile_body / missile_nose /
                                   missile_nose_tip / missile_fin / boi_1)
    3. Generate the Surface Mesh-  min 15 mm, max 300 mm, growth 1.2,
                                   cells per gap 2, proximity to faces
    4. Describe Geometry        -  no capping, share topology = yes
       4.1 Apply Share Topology -  gap distance 3 mm, manual interface select
       4.2 Create Regions       -  1 flow volume
    5. Update Regions           -  region ``zone----enclosure----``: solid -> fluid
    6. Add Boundary Layers      -  ``last-ratio_1``: first height 0.07 mm,
                                   12 layers, last-ratio, stair step,
                                   scoped to the fluid region / missile surfaces
    7. Generate the Volume Mesh -  polyhedra, region based sizing
                                   (missile 25 mm, enclosure 500 mm, growth 1.2)

The resulting mesh is written into the output directory of this project.

Usage
-----
    python mesh_missile.py                 # full run, 16 processes
    python mesh_missile.py -n 8            # pick the process count
    python mesh_missile.py --keep-fluent   # do not shut Fluent down at the end

Environment notes (this machine / this workspace)
-------------------------------------------------
* Fluent Meshing is required: ``AWP_ROOT241`` must point at the 2024 R1 install.
* PyFluent is installed locally in ``./_deps`` (python 3.10 interpreter); the
  wrapper ``run_meshing.cmd`` sets ``PYTHONPATH`` accordingly.
* ``start_watchdog=False`` is used because the PyFluent watchdog process is
  started with ``CREATE_BREAKAWAY_FROM_JOB``, which fails when the launcher runs
  inside a Windows job object.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sys
import time
import traceback
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
GEOMETRY_FILE = os.path.join(PROJECT_DIR, "geometry", "missile.scdoc")
OUTPUT_DIR = PROJECT_DIR          # the mesh is exported into the project folder
RUNS_DIR = os.path.join(PROJECT_DIR, "runs")
LOG_DIR = os.path.join(PROJECT_DIR, "logs")
MESH_NAME = "missile_mesh.msh.h5"

# --------------------------------------------------------------------------
# Workflow parameters -- taken verbatim from fluent_meshing.md
# --------------------------------------------------------------------------
# 1. Import Geometry (TaskObject1 / ImportGeometry)
P_IMPORT = {
    "LengthUnit": "mm",
    "ImportCadPreferences": {"MaxFacetLength": 0},
}

# 2. Add Local Sizing (TaskObject2 / AddLocalSizingWTM) - children 2.1 - 2.5
#    NOTE: the numeric values must be handed over as numbers; Fluent rejects
#    strings here ("tg-draw-size-boxes: wta[2](float)").
P_LOCAL_SIZING = [
    {   # 2.1 missile_body
        "BOIControlName": "missile_body",
        "BOIExecution": "Curvature",
        "BOIFaceLabelList": ["missile_body"],
        "BOIMinSize": 3.0,
        "BOIMaxSize": 30.0,
        "BOIGrowthRate": 1.18,
        "BOICurvatureNormalAngle": 18.0,
        "BOICellsPerGap": 1,
        "BOISize": 18.0,
    },
    {   # 2.2 missile_nose
        "BOIControlName": "missile_nose",
        "BOIExecution": "Curvature",
        "BOIFaceLabelList": ["missile_nose"],
        "BOIMinSize": 2.0,
        "BOIMaxSize": 18.0,
        "BOIGrowthRate": 1.18,
        "BOICurvatureNormalAngle": 16.0,
        "BOICellsPerGap": 1,
        "BOISize": 9.0,
    },
    {   # 2.3 missile_nose_tip
        "BOIControlName": "missile_nose_tip",
        "BOIExecution": "Curvature",
        "BOIFaceLabelList": ["missile_nose_tip"],
        "BOIMinSize": 1.6,
        "BOIMaxSize": 10.0,
        "BOIGrowthRate": 1.15,
        "BOICurvatureNormalAngle": 18.0,
        "BOICellsPerGap": 1,
        "BOISize": 6.0,
    },
    {   # 2.4 missile_fin
        "BOIControlName": "missile_fin",
        "BOIExecution": "Curvature",
        "BOIFaceLabelList": ["missile_fin"],
        "BOIMinSize": 3.0,
        "BOIMaxSize": 40.0,
        "BOIGrowthRate": 1.2,
        "BOICurvatureNormalAngle": 18.0,
        "BOICellsPerGap": 1,
    },
    {   # 2.5 boi_1 (body of influence)
        "BOIControlName": "boi_1",
        "BOIExecution": "Body Of Influence",
        "BOIFaceLabelList": ["missile-boi"],
        "BOISize": 30.0,
        "BOIGrowthRate": 1.2,
        "BOIMinSize": 15.0,
        "BOIMaxSize": 100.0,
        "BOICurvatureNormalAngle": 16.0,
        "BOICellsPerGap": 1,
    },
]

# 3. Generate the Surface Mesh (TaskObject3 / GenerateTheSurfaceMeshWTM)
P_SURFACE_MESH = {
    "CFDSurfaceMeshControls": {
        "MinSize": 15.0,
        "MaxSize": 300.0,
        "GrowthRate": 1.2,
        "CellsPerGap": 2,
        "ScopeProximityTo": "faces",
    },
    "ExecuteShareTopology": "No",
}

# 4. Describe Geometry (TaskObject4 / GeometrySetup)
P_DESCRIBE_GEOMETRY = {
    "CappingRequired": "No",
    "InvokeShareTopology": "Yes",
    "NonConformal": "No",
}

# 4.1 Apply Share Topology (TaskObject5 / ShareTopology)
P_SHARE_TOPOLOGY = {
    "GapDistance": 3.0,
    "InterfaceSelect": "Manual",
}

# 4.2 Create Regions (TaskObject8 / CreateRegions)
P_CREATE_REGIONS = {"NumberOfFlowVolumes": 1}

# 5. Update Regions (TaskObject9 / UpdateRegions)
P_REGION_TYPE = "fluid"
# Region name the original model exposes for the far-field enclosure. The new
# .scdoc is produced by the SpaceClaim workflow with the same schema, so the name
# is *verified* against the imported geometry before it is used; this constant is
# only a last-resort fallback and never silently replaces a missing region.
FALLBACK_REGION_NAME = "zone----enclosure----"

# Names the imported geometry must actually expose. They come from the named
# selections the SpaceClaim model creates; a mismatch means the sizing or the
# boundary-layer scoping would silently do nothing, so it is a hard error.
REQUIRED_FACE_LABELS = [
    "missile_body",
    "missile_nose",
    "missile_nose_tip",
    "missile_fin",
    "missile-boi",
]
REQUIRED_BOUNDARY_ZONES = ["inlet", "wall", "outlet"]
MISSILE_ZONE_HINT = ["missile-zone", "missile"]

# 6. Add Boundary Layers (TaskObject10 -> TaskObject17 / last-ratio_1)
P_BOUNDARY_LAYER = {
    "BLControlName": "last-ratio_1",
    "OffsetMethodType": "last-ratio",
    "FirstHeight": 0.07,
    "NumberOfLayers": 12,
    "LocalPrismPreferences": {"Continuous": "Stair Step"},
    "FaceScope": {"RegionsType": "fluid-regions"},
    "BLRegionList": [FALLBACK_REGION_NAME],
    "RegionScope": ["missile"],
    "BLZoneList": [
        "missile-zone----enclosure----_43",
        "inlet",
        "wall",
        "outlet",
    ],
}

# 7. Generate the Volume Mesh (TaskObject11 / GenerateTheVolumeMeshWTM)
P_VOLUME_MESH = {
    "VolumeFill": "polyhedra",
    "SizingMethod": "Region-based",
    "MeshSolidRegions": False,
    "ShowSolidFluidMeshed": True,
    "VolumeFillControls": {
        "GrowthRate": 1.2,
        "TetPolyMaxCellLength": 80.0,
    },
    # region based sizing, index aligned lists
    "RegionTetNameList": ["missile", FALLBACK_REGION_NAME],
    "RegionTetMaxCellLengthList": [25.0, 500.0],
    "RegionTetGrowthRateList": [1.2, 1.2],
}

TASK_IMPORT = "Import Geometry"
TASK_LOCAL_SIZING = "Add Local Sizing"
TASK_SURFACE_MESH = "Generate the Surface Mesh"
TASK_DESCRIBE = "Describe Geometry"
TASK_SHARE_TOPO = "Apply Share Topology"
TASK_CREATE_REGIONS = "Create Regions"
TASK_UPDATE_REGIONS = "Update Regions"
TASK_BOUNDARY_LAYERS = "Add Boundary Layers"
TASK_VOLUME_MESH = "Generate the Volume Mesh"

# set with --stop-after to stop the run after one step (debugging aid)
STOP_AFTER = None
# "DrawSizeControl" of the local sizing controls: the recorded workflow uses
# "true"; drawing is a display feature and is switched off with
# --draw-size-control false.
DRAW_SIZE_CONTROL = "true"


class StopHere(Exception):
    """Raised to end a debug run cleanly (``--stop-after``)."""


class MappingError(RuntimeError):
    """Raised when a required label, zone or region cannot be resolved uniquely."""


def check_stop(tag: str) -> None:
    if STOP_AFTER and tag == STOP_AFTER:
        raise StopHere()


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
class Logger:
    """Log to stdout and to a run log file at the same time."""

    def __init__(self, path: str) -> None:
        self._fh = open(path, "w", encoding="utf-8")
        self.path = path

    def __call__(self, *args) -> None:
        msg = " ".join(str(a) for a in args)
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {msg}"
        print(line, flush=True)
        self._fh.write(line + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:  # noqa: BLE001
            pass


def state_of(task) -> dict:
    """Return a task's argument state as a plain dict (never raises)."""
    try:
        return task.Arguments.get_state()
    except Exception as exc:  # noqa: BLE001
        return {"<unavailable>": f"{type(exc).__name__}: {exc}"}


def show(log: Logger, title: str, task) -> dict:
    state = state_of(task)
    log(f"--- {title} arguments ---")
    log(json.dumps(state, indent=2, default=str))
    return state


def matches_for(candidates, available):
    """All entries of *available* matching any candidate, exact matches first."""
    if not available:
        return []
    available = [str(item) for item in available]
    exact = [item for candidate in candidates for item in available if item.lower() == str(candidate).lower()]
    if exact:
        return list(dict.fromkeys(exact))
    fuzzy = [item for candidate in candidates for item in available if str(candidate).lower() in item.lower()]
    return list(dict.fromkeys(fuzzy))


def resolve_name(name, available, what: str, log: Logger):
    """Map one configured control name onto the name the model really exposes.

    Exact match wins. A single substring match is accepted and logged. More than
    one match is an explicit error: guessing between two zones would silently
    scope the control to the wrong one.
    """
    matches = matches_for([name], available)
    if not matches:
        raise MappingError(f"Could not find {what} '{name}' among {list(available)}")
    if len(matches) > 1:
        raise MappingError(f"{what} '{name}' matches more than one entry: {matches}")
    if matches[0] != name:
        log(f"    mapped {what} '{name}' -> '{matches[0]}'")
    return matches[0]


def find_child(log: Logger, parent, name: str):
    """Return the parent's child task with the given display name."""
    try:
        names = [t.name() for t in parent.ordered_children()]
    except Exception as exc:  # noqa: BLE001
        log(f"    could not list children of {parent.name()}: {exc}")
        return None
    log(f"    children of '{parent.name()}': {names}")
    for task in parent.ordered_children():
        if task.name() == name:
            return task
    return None


# --------------------------------------------------------------------------
# Imported-name inspection
# --------------------------------------------------------------------------
def inspect_names(meshing, log: Logger) -> dict:
    """Read the labels and zones of the imported geometry from the live session.

    Returns ``{'labels': [...], 'zones': [...]}`` where a missing key means the
    running Fluent build does not expose that query. A query that is available
    but reports missing names is a hard error later on, so a mismatch can never
    be mistaken for "no evidence".
    """
    found = {}
    if meshing is None:
        return found
    for key, commands in (('labels', (('geometry', 'labels'),)), ('zones', (('boundary', 'list'),))):
        for command in commands:
            try:
                value = getattr(getattr(meshing.tui, command[0]), command[1])()
                text = str(value)
            except Exception as exc:  # noqa: BLE001
                log(f"    {command[0]}/{command[1]} not available: {type(exc).__name__}: {exc}")
                continue
            if not text.strip():
                log(f"    {command[0]}/{command[1]} returned nothing; treating names as unavailable")
                break
            names = [str(name) for name in _names_from_listing(text)]
            # A non-empty answer is treated as the authoritative list, so an empty
            # parse means "the model has none" and later checks will report it.
            found[key] = names
            log(f"    imported {key}: {names}")
            break
    return found


def _names_from_listing(text: str) -> list:
    normalized = text.replace(';', '\n').replace(',', '\n')
    from_name = re.findall(r'^\s*(?:label|zone)?\s*name\s*[:=]\s*(\S+)\s*$', normalized, re.I | re.M)
    if from_name:
        return from_name
    names = []
    for line in normalized.splitlines():
        stripped = line.strip().strip('"')
        if not stripped or stripped.startswith('#') or ':' in stripped or '=' in stripped:
            continue
        if re.match(r'^(id|name)\b', stripped, re.I):
            continue
        parts = stripped.split()
        if len(parts) == 1 and parts[0] not in ('Done.',):
            names.append(parts[0])
        elif len(parts) >= 2 and re.fullmatch(r'\d+', parts[0]):
            names.append(parts[1])
    return names


def step_check_import(log: Logger, meshing) -> dict:
    """Require the labels the local sizing controls depend on to exist."""
    log("-" * 78)
    log("CHECK - imported labels and zones")
    inspected = inspect_names(meshing, log)
    labels = inspected.get('labels')
    if labels is None:
        log("    WARNING: this Fluent build exposes no label query; the export check will verify the mesh instead")
        return inspected
    missing = [name for name in REQUIRED_FACE_LABELS if not matches_for([name], labels)]
    if missing:
        raise MappingError("Imported geometry is missing face labels: " + ', '.join(missing) + " (available: " + repr(labels) + ")")
    log(f"    all {len(REQUIRED_FACE_LABELS)} required face labels are present")
    return inspected


# --------------------------------------------------------------------------
# Workflow steps
# --------------------------------------------------------------------------
def step_import(log: Logger, wf, meshing=None) -> None:
    log("=" * 78)
    log("STEP 1 - Import Geometry")
    task = wf.TaskObject[TASK_IMPORT]
    args = dict(P_IMPORT)
    args["FileName"] = GEOMETRY_FILE
    task.Arguments.set_state(args)
    task.Execute()
    state = show(log, TASK_IMPORT, task)
    log(f"    task state: {task.State()}")
    if task.Errors():
        raise RuntimeError(f"Import Geometry errors: {task.Errors()}")
    log(f"    imported parts: {state.get('NumParts')}")
    step_check_import(log, meshing)


def step_local_sizing(log: Logger, wf) -> None:
    log("=" * 78)
    log("STEP 2 - Add Local Sizing (4 curvature + 1 body of influence)")
    parent = wf.TaskObject[TASK_LOCAL_SIZING]
    show(log, TASK_LOCAL_SIZING + " (before adding children)", parent)
    log(f"    DrawSizeControl = {DRAW_SIZE_CONTROL}")
    created = []
    for spec in P_LOCAL_SIZING:
        args = dict(spec)
        args["AddChild"] = "yes"
        args["BOIZoneorLabel"] = "label"
        args["DrawSizeControl"] = DRAW_SIZE_CONTROL
        # AddChildAndUpdate() creates the child *and* updates the workflow, so the
        # child's mandatory arguments (above all the face zone labels) have to be
        # handed over in the same call.
        child = parent.add_child_and_update(args)
        if child is None:
            raise RuntimeError("Add Local Sizing did not create a child task")
        state = state_of(child)
        log(f"    + {spec['BOIControlName']:<16} {spec['BOIExecution']:<18} "
            f"labels={state.get('BOIFaceLabelList')} min={state.get('BOIMinSize')} "
            f"max={state.get('BOIMaxSize')} growth={state.get('BOIGrowthRate')} "
            f"angle={state.get('BOICurvatureNormalAngle')} state={child.State()}")
        if child.Errors():
            raise RuntimeError(f"{spec['BOIControlName']} errors: {child.Errors()}")
        if child.Warnings():
            log(f"      warning: {child.Warnings()}")
        created.append(child)
    parent.Execute()
    log(f"    local sizing controls: {len(created)}")


def step_surface_mesh(log: Logger, wf) -> None:
    log("=" * 78)
    log("STEP 3 - Generate the Surface Mesh")
    task = wf.TaskObject[TASK_SURFACE_MESH]
    task.Arguments.set_state(P_SURFACE_MESH)
    task.Execute()
    show(log, TASK_SURFACE_MESH, task)
    log(f"    task state: {task.State()}")
    if task.Errors():
        raise RuntimeError(f"Surface mesh errors: {task.Errors()}")
    if task.Warnings():
        log(f"    warning: {task.Warnings()}")


def step_describe_geometry(log: Logger, wf) -> None:
    log("=" * 78)
    log("STEP 4 - Describe Geometry (share topology = yes, no capping)")
    task = wf.TaskObject[TASK_DESCRIBE]
    task.Arguments.set_state(P_DESCRIBE_GEOMETRY)
    task.Execute()
    show(log, TASK_DESCRIBE, task)
    log(f"    task state: {task.State()}")
    if task.Errors():
        raise RuntimeError(f"Describe geometry errors: {task.Errors()}")
    if task.Warnings():
        log(f"    warning: {task.Warnings()}")


def step_share_topology(log: Logger, wf) -> None:
    log("=" * 78)
    log("STEP 4.1 - Apply Share Topology")
    task = wf.TaskObject[TASK_SHARE_TOPO]
    task.Arguments.set_state(P_SHARE_TOPOLOGY)
    task.Execute()
    show(log, TASK_SHARE_TOPO, task)
    log(f"    task state: {task.State()}")
    if task.Errors():
        raise RuntimeError(f"Share topology errors: {task.Errors()}")
    if task.Warnings():
        log(f"    warning: {task.Warnings()}")


def step_create_regions(log: Logger, wf) -> None:
    log("=" * 78)
    log("STEP 4.2 - Create Regions")
    task = wf.TaskObject[TASK_CREATE_REGIONS]
    task.Arguments.set_state(P_CREATE_REGIONS)
    task.Execute()
    show(log, TASK_CREATE_REGIONS, task)
    log(f"    task state: {task.State()}")
    if task.Errors():
        raise RuntimeError(f"Create regions errors: {task.Errors()}")
    if task.Warnings():
        log(f"    warning: {task.Warnings()}")


def step_update_regions(log: Logger, wf) -> list:
    """Set the detected region(s) to fluid.  Returns the region names."""
    log("=" * 78)
    log("STEP 5 - Update Regions (solid -> fluid)")
    task = wf.TaskObject[TASK_UPDATE_REGIONS]
    state = show(log, TASK_UPDATE_REGIONS, task)

    names = state.get("RegionNameList") or state.get("OldRegionNameList") or []
    names = [str(n) for n in names if n]
    if not names:
        # Fluent 24.1 reports an empty argument state when nothing has to change.
        # That is not evidence that the documented region exists, so the name is
        # only used after resolving it against the imported geometry, and the
        # exported mesh is checked for the fluid zone afterwards.
        log("    no region list reported by the task")
        names = [FALLBACK_REGION_NAME]

    old_types = [str(t) for t in (state.get("OldRegionTypeList") or [])]
    if not old_types or len(old_types) != len(names):
        old_types = ["solid"] * len(names)

    try:
        task.Arguments.set_state(
            {
                "OldRegionNameList": names,
                "OldRegionTypeList": old_types,
                "RegionNameList": names,
                "RegionTypeList": [P_REGION_TYPE] * len(names),
            }
        )
        task.Execute()
    except Exception as exc:  # noqa: BLE001
        raise MappingError(
            f"Update Regions could not set {names} to fluid ({type(exc).__name__}: {exc}); "
            "the imported geometry does not expose the expected region"
        ) from exc
    show(log, TASK_UPDATE_REGIONS, task)
    log(f"    task state: {task.State()}")
    if task.Errors():
        raise MappingError(f"Update regions errors: {task.Errors()}")
    if task.Warnings():
        log(f"    warning: {task.Warnings()}")
    log(f"    fluid regions: {names}")
    return names


def step_boundary_layers(log: Logger, meshing, wf, region_names: list) -> None:
    log("=" * 78)
    log("STEP 6 - Add Boundary Layers (last-ratio_1)")
    parent = wf.TaskObject[TASK_BOUNDARY_LAYERS]

    args = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
            for k, v in P_BOUNDARY_LAYER.items()}
    args["BLRegionList"] = list(region_names)
    args["AddChild"] = "yes"

    child = parent.add_child_and_update(args)
    child = find_child(log, parent, P_BOUNDARY_LAYER["BLControlName"]) or child
    if child is None:
        raise RuntimeError("Add Boundary Layers did not create a child task")

    state = state_of(child)
    log("    boundary layer control arguments:")
    log(json.dumps(state, indent=2, default=str))
    log(f"    task state: {safe_state(child)}")

    # the complete* fields are filled in by the GUI; when Fluent reports them,
    # every configured zone must resolve to exactly one real zone. A missing or
    # ambiguous zone is an error, never a silent fallback to the body surfaces.
    zone_scope = state.get("CompleteBLZoneList") or []
    if zone_scope:
        log(f"    available BL zones: {list(zone_scope)}")
        zones = []
        for wanted in P_BOUNDARY_LAYER["BLZoneList"]:
            match = resolve_name(wanted, zone_scope, "boundary layer zone", log)
            if match not in zones:
                zones.append(match)
        missing = [name for name in REQUIRED_BOUNDARY_ZONES if not matches_for([name], zone_scope)]
        if missing:
            raise MappingError("The imported geometry is missing boundary zones for the boundary layer: " + ', '.join(missing) + " (available: " + repr(list(zone_scope)) + ")")
        if zones != args["BLZoneList"]:
            log(f"    remapping BL zone list -> {zones}")
            child.Arguments.set_state({"BLZoneList": zones})
            child.Execute()
    else:
        log("    Fluent reported no complete zone list; the exported mesh is checked for the boundary groups instead")

    errors = child.Errors()
    if errors:
        log("    boundary layer task reported errors, current controls:")
        show_scoped_prisms(log, meshing)
        raise RuntimeError(f"Boundary layer errors: {errors}")
    if child.Warnings():
        log(f"    warning: {child.Warnings()}")
    show_scoped_prisms(log, meshing)
    parent.Execute()
    log("    boundary layer control applied")


def show_scoped_prisms(log: Logger, meshing) -> None:
    """Log the prism controls Fluent actually holds (verification aid)."""
    try:
        log("    /mesh/scoped_prisms/list:")
        out = meshing.tui.mesh.scoped_prisms.list()
        for line in str(out).splitlines():
            log(f"      {line}")
    except Exception as exc:  # noqa: BLE001
        log(f"    could not list prism controls: {type(exc).__name__}: {exc}")


def safe_state(task) -> str:
    try:
        return str(task.State())
    except Exception as exc:  # noqa: BLE001
        return f"<unavailable: {type(exc).__name__}: {exc}>"


def cell_zone_names(meshing) -> list:
    """Names of the cell zones of the current mesh, read from the live session."""
    try:
        listing = meshing.tui.mesh.manage.list()
    except Exception as exc:  # noqa: BLE001
        raise MappingError(f'Could not list the cell zones of the generated mesh: {type(exc).__name__}: {exc}') from exc
    names = []
    for line in str(listing).splitlines():
        parts = line.split()
        if len(parts) >= 3 and re.fullmatch(r'\d+', parts[0]) and re.fullmatch(r'\d+', parts[-1]):
            names.append(parts[1])
    return names


def assert_region_scope(quality, enclosure_region: str, log: Logger) -> str:
    """Check that the volume mesh really holds exactly the region the sizing used.

    The names come from the mesh check that ran on the generated mesh, so a
    configured name that did not exist cannot pass unnoticed: the cell zone list
    would be empty or would hold more than one fluid zone.
    """
    zones = [zone['name'] for zone in (quality or {}).get('fluidRegions', []) if zone.get('cells', 0) > 0]
    log(f"    fluid cell zones in the generated mesh: {zones}")
    if not zones:
        raise MappingError('The volume mesh reports no fluid cell zone; the region name used for sizing did not exist')
    if len(zones) > 1:
        raise MappingError('The volume mesh holds more than one fluid cell zone: ' + repr(zones))
    if enclosure_region and zones[0] != enclosure_region:
        raise MappingError(f"The volume mesh holds '{zones[0]}' but the workflow made '{enclosure_region}' fluid")
    log(f"    mesh region: enclosure='{zones[0]}'")
    return zones[0]


def resolve_from_quality(quality, primary, log: Logger) -> str:
    """Confirm the fluid cell zone name against the name the workflow reported."""
    return assert_region_scope(quality, primary, log)


def step_volume_mesh(log: Logger, wf, region_names: list, meshing=None, quality=None) -> None:
    log("=" * 78)
    log("STEP 7 - Generate the Volume Mesh (polyhedra, region based)")
    task = wf.TaskObject[TASK_VOLUME_MESH]
    args = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
            for k, v in P_VOLUME_MESH.items()}

    # The region names are written into the sizing lists in the documented order
    # (missile 25 mm, enclosure 500 mm) and read back from the generated mesh
    # afterwards, so the scope of the refinement is checked rather than assumed.
    enclosure = region_names[0] if region_names else FALLBACK_REGION_NAME
    missile = next((name for name in P_VOLUME_MESH["RegionTetNameList"] if name != FALLBACK_REGION_NAME), "missile")
    triplets = []
    for name, size, growth in zip(
        P_VOLUME_MESH["RegionTetNameList"],
        P_VOLUME_MESH["RegionTetMaxCellLengthList"],
        P_VOLUME_MESH["RegionTetGrowthRateList"],
    ):
        target = enclosure if name == FALLBACK_REGION_NAME else missile
        if target not in [t[0] for t in triplets]:
            triplets.append((target, size, growth))
    args["RegionTetNameList"] = [t[0] for t in triplets]
    args["RegionTetMaxCellLengthList"] = [t[1] for t in triplets]
    args["RegionTetGrowthRateList"] = [t[2] for t in triplets]
    log(f"    configured region sizing: "
        f"{list(zip(args['RegionTetNameList'], args['RegionTetMaxCellLengthList'], args['RegionTetGrowthRateList']))}")

    task.Arguments.set_state(args)
    started = time.time()
    task.Execute()
    show(log, TASK_VOLUME_MESH, task)
    log(f"    task state: {task.State()} ({time.time() - started:.1f} s)")
    if task.Errors():
        raise RuntimeError(f"Volume mesh errors: {task.Errors()}")
    if task.Warnings():
        log(f"    warning: {task.Warnings()}")


def report_mesh_statistics(log: Logger, meshing, run_dir: str) -> None:
    """Best effort verification of the mesh that was just generated."""
    log("=" * 78)
    log("STEP 8 - Verify the generated mesh")
    try:
        out = meshing.tui.mesh.check_mesh()
        for line in str(out).splitlines():
            if line.strip():
                log(f"    check-mesh: {line.strip()}")
    except Exception as exc:  # noqa: BLE001
        log(f"    check-mesh not available: {type(exc).__name__}: {exc}")

    try:
        log("    /boundary/manage/list:")
        out = meshing.tui.boundary.manage.list()
        for line in str(out).splitlines():
            if line.strip():
                log(f"      {line.rstrip()}")
    except Exception as exc:  # noqa: BLE001
        log(f"    zone listing not available: {type(exc).__name__}: {exc}")

    try:
        log("    /scoped_sizing/list:")
        out = meshing.tui.scoped_sizing.list()
        for line in str(out).splitlines():
            if line.strip():
                log(f"      {line.rstrip()}")
    except Exception as exc:  # noqa: BLE001
        log(f"    sizing listing not available: {type(exc).__name__}: {exc}")

    log_transcript_summary(log, run_dir)


def log_transcript_summary(log: Logger, run_dir: str) -> None:
    """Copy the Fluent transcript and log the mesh sizes it reports."""
    try:
        transcripts = sorted(glob.glob(os.path.join(run_dir, "*.trn")))
    except Exception:  # noqa: BLE001
        transcripts = []
    if not transcripts:
        log("    no Fluent transcript found")
        return
    src = transcripts[-1]
    dst = os.path.join(LOG_DIR, f"{os.path.basename(run_dir)}.trn")
    try:
        shutil.copyfile(src, dst)
        log(f"    Fluent transcript copied to {dst}")
    except Exception as exc:  # noqa: BLE001
        log(f"    could not copy transcript: {exc}")
    try:
        with open(src, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        for pattern in (
            r"^\s*(\d+) cells were created.*$",
            r"^\s*The mesh has a minimum Orthogonal Quality of:.*$",
            r"^\s*Overall Summary.*$",
            r"^\s*name\s+id cells .*$",
            r"^\s+zone----enclosure----\s+\d+.*$",
        ):
            for match in re.finditer(pattern, text, re.M):
                log(f"    trn | {match.group(0).strip()}")
    except Exception as exc:  # noqa: BLE001
        log(f"    could not parse transcript: {exc}")


def summarise_workflow(log: Logger, mesh_path: str) -> None:
    """Log the workflow state that was stored next to the written mesh."""
    folder = os.path.join(
        os.path.dirname(mesh_path),
        os.path.basename(mesh_path).replace(".msh.h5", "") + "_workflow_files",
    )
    wfts = sorted(glob.glob(os.path.join(folder, "*.wft")))
    if not wfts:
        log(f"    no workflow file found in {folder}")
        return
    src = wfts[-1]
    log(f"    workflow file: {src}")
    try:
        with open(src, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        root = data["workflow"]["ROOT"]
    except Exception as exc:  # noqa: BLE001
        log(f"    could not read workflow file: {exc}")
        return
    lines = []
    for key, task in root.items():
        if not key.startswith("TaskObject"):
            continue
        lines.append(
            {
                "task": task.get("_name_"),
                "command": task.get("CommandName"),
                "state": task.get("State"),
                "arguments": task.get("Arguments"),
            }
        )
    dst = os.path.join(LOG_DIR, os.path.basename(src).replace(".wft", ".json"))
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(lines, fh, indent=2, ensure_ascii=False)
    log(f"    workflow arguments saved to {dst}")
    for task in lines:
        if task["task"] in (
            TASK_LOCAL_SIZING, "missile_body", "missile_nose", "missile_nose_tip",
            "missile_fin", "boi_1", TASK_SURFACE_MESH, TASK_UPDATE_REGIONS,
            P_BOUNDARY_LAYER["BLControlName"], TASK_VOLUME_MESH,
        ):
            log(f"      {task['task']:<22} {task['state']:<12} "
                f"{json.dumps(task['arguments'], ensure_ascii=False)}")


def write_mesh(log: Logger, meshing, out_path: str) -> None:
    log("=" * 78)
    log(f"STEP 9 - Write mesh to {out_path}")
    if os.path.exists(out_path):
        raise FileExistsError(f"Refusing to overwrite mesh: {out_path}")
    written = False
    try:
        meshing.tui.file.write_mesh(out_path)
        written = os.path.exists(out_path)
    except Exception as exc:  # noqa: BLE001
        log(f"    write_mesh failed: {type(exc).__name__}: {exc}")
    if not written:
        expr = f'(ti-menu-load-string "file/write-mesh \\"{out_path}\\"")(newline)'
        log(f"    falling back to scheme: {expr}")
        meshing.scheme_eval.scheme_eval(expr)
        written = os.path.exists(out_path)
    if not written:
        raise RuntimeError(f"mesh file was not written: {out_path}")
    log(f"    wrote {out_path} ({os.path.getsize(out_path)} bytes)")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def ensure_writable_appdata(log: Logger, run_dir: str = None) -> None:
    """ANSYS licensing writes its client port file to ``%APPDATA%\\Ansys``.

    In a restricted environment that directory can be read-only; in that case
    ``%APPDATA%`` is redirected to a folder inside the project so that Fluent
    can start (otherwise it fails with "Cannot initialize ANSYS Licensing
    context").  The check must happen before Fluent is launched.
    """
    appdata = os.environ.get("APPDATA", "")
    probe_dir = os.path.join(appdata, "Ansys")
    try:
        os.makedirs(probe_dir, exist_ok=True)
        probe = os.path.join(probe_dir, "_dsh_write_probe.tmp")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("probe")
        os.remove(probe)
        log(f"    %APPDATA% is writable: {appdata}")
        return
    except Exception as exc:  # noqa: BLE001
        fallback = os.path.join(run_dir or os.path.join(os.path.dirname(PROJECT_DIR), "data_lib", "runtime"), "_appdata")
        os.makedirs(fallback, exist_ok=True)
        os.environ["APPDATA"] = fallback
        log(f"    WARNING: {appdata} is not writable ({type(exc).__name__}); "
            f"redirecting APPDATA to {fallback} so that ANSYS licensing works")


def main() -> int:
    from mesh_runner import main as managed_main
    return managed_main(sys.modules[__name__])


if __name__ == "__main__":
    sys.exit(main())
