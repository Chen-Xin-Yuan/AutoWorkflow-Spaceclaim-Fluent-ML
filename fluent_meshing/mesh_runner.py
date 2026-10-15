"""One bounded launch/check/export path for the recorded meshing workflow."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time
import traceback
from workspace_runtime import RunRecord, supervise, workspace, relative
from mesh_validation import report_mesh, verify_export


def options(module):
    parser = argparse.ArgumentParser(description='Automated Fluent Meshing with provenance and strict export checks.')
    root, config = workspace(module.PROJECT_DIR)
    parser.add_argument('-n', '--processors', type=int, default=16)
    parser.add_argument('--geometry', required=True, help='Imported .scdoc geometry; there is no default model')
    parser.add_argument('--run-dir', help='Unique working directory; must not already exist')
    parser.add_argument('--out', help='Mesh output file; must not already exist')
    parser.add_argument('--start-timeout', type=int, default=300)
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--keep-fluent', action='store_true')
    parser.add_argument('--show-gui', action='store_true')
    parser.add_argument('--draw-size-control', choices=['true', 'false'], default='true')
    parser.add_argument('--stop-after', choices=['import', 'sizing', 'surface', 'describe', 'share', 'regions', 'update-regions', 'bl', 'volume'])
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parsed = parser.parse_args()
    # The invocation profile name only labels the run record now; the recorded
    # workflow is the single supported profile.
    parsed.profile = 'recorded'
    return parsed


def execute_task(module, log, wf, name, arguments=None):
    task = wf.TaskObject[name]
    if arguments is not None:
        task.Arguments.set_state(arguments)
    task.Execute()
    module.show(log, name, task)
    if task.Errors():
        raise RuntimeError(name + ' task failed: ' + str(task.Errors()))
    return task


def recorded_steps(module, log, meshing, wf):
    module.step_local_sizing(log, wf); module.check_stop('sizing')
    module.step_surface_mesh(log, wf); module.check_stop('surface')
    module.step_describe_geometry(log, wf); module.check_stop('describe')
    module.step_share_topology(log, wf); module.check_stop('share')
    module.step_create_regions(log, wf); module.check_stop('regions')
    regions = module.step_update_regions(log, wf); module.check_stop('update-regions')
    module.step_boundary_layers(log, meshing, wf, regions); module.check_stop('bl')
    module.step_volume_mesh(log, wf, regions, meshing); module.check_stop('volume')
    # The list of fluid region names is what the later checks need, not the raw
    # task argument state.
    return regions


def worker(module, opts):
    run = RunRecord(module.PROJECT_DIR, opts.profile, opts.geometry, run_dir=opts.run_dir, reopen=True)
    log = module.Logger(str(run.directory / 'pipeline.log'))
    meshing = wf = output = None
    status, verification, error = 'FAILED', {'runtimeVerified': False}, None
    started_at = time.time()
    owned_output = False
    try:
        if opts.processors <= 0 or opts.start_timeout <= 0 or opts.timeout <= 0:
            raise ValueError('Processor count and timeout limits must be positive')
        run.check_input(opts.geometry)
        (run.directory / 'request.json').write_text(json.dumps({key: value for key, value in vars(opts).items() if key != 'worker'}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        output = Path(opts.out).resolve() if opts.out else run.directory / module.MESH_NAME
        protected = run.root / 'missile_project'
        if output == protected or protected in output.parents:
            raise ValueError('Protected output path')
        if output.exists():
            raise FileExistsError('Mesh output already exists; use a new filename')
        output.parent.mkdir(parents=True, exist_ok=True)
        module.GEOMETRY_FILE = str(Path(opts.geometry).resolve())
        module.STOP_AFTER, module.DRAW_SIZE_CONTROL = opts.stop_after, opts.draw_size_control
        log('profile:', opts.profile, 'processors:', opts.processors)
        log('input:', relative(run.root, opts.geometry))
        import ansys.fluent.core as pyfluent
        module.ensure_writable_appdata(log, str(run.directory))
        meshing = pyfluent.launch_fluent(product_version='24.1', mode='meshing', version='3d', precision='double', processor_count=opts.processors, start_transcript=True, show_gui=opts.show_gui, start_watchdog=False, cleanup_on_exit=not opts.keep_fluent, cwd=str(run.directory), start_timeout=opts.start_timeout)
        wf = meshing.workflow
        wf.InitializeWorkflow(WorkflowType='Watertight Geometry')
        module.step_import(log, wf, meshing)
        module.check_stop('import')
        regions = recorded_steps(module, log, meshing, wf)
        quality = report_mesh(meshing, run.directory)
        # The mesh check reports the real fluid cell zones, so the region name the
        # workflow made fluid is confirmed against the generated mesh.
        expected_region = regions[0] if isinstance(regions, (list, tuple)) and regions else ''
        module.resolve_from_quality(quality, expected_region, log)
        owned_output = True
        module.write_mesh(log, meshing, str(output))
        verification = verify_export(output, started_at, quality)
        # The worker records where the file is; the supervisor inspects the file
        # itself with the isolated mesh-inspection environment.
        verification['outputPath'] = str(output)
        verification['output'] = relative(run.root, output)
        verification['regions'] = regions
        wf.SaveWorkflow(FilePath=str(run.directory / 'completed_workflow.wft'))
        status = 'PASSED'
    except module.StopHere:
        status = 'STOPPED'
        verification = {'runtimeVerified': False, 'stoppedAfter': opts.stop_after}
        if wf is not None:
            try:
                wf.SaveWorkflow(FilePath=str(run.directory / 'stopped_workflow.wft'))
            except Exception:
                log('Could not save stopped workflow')
        log('Deliberately stopped; full-run acceptance was not performed')
    except Exception as exc:
        error = {'type': type(exc).__name__, 'message': str(exc)}
        log(traceback.format_exc())
        if wf is not None:
            try:
                wf.SaveWorkflow(FilePath=str(run.directory / 'failed_workflow.wft'))
            except Exception:
                log('Could not save partial workflow')
    finally:
        if meshing is not None and not opts.keep_fluent:
            try:
                meshing.exit()
                log('Fluent session closed')
            except Exception as exc:
                status = 'FAILED'
                verification['runtimeVerified'] = False
                error = {'type': 'ShutdownError', 'message': str(exc)}
        log.close()
    if status == 'FAILED' and owned_output and output and output.is_file() and output.parent != run.directory:
        import shutil
        retained = run.directory / ('failed-' + output.name)
        shutil.copy2(output, retained)
        verification['failedOutputCopy'] = retained.name
    run.finish(status, verification, error, relocate=False)
    return 0 if status in ('PASSED', 'STOPPED') else 1


def main(module):
    opts = options(module)
    opts.geometry = str(Path(opts.geometry).resolve())
    if opts.out:
        opts.out = str(Path(opts.out).resolve())
    if opts.worker:
        return worker(module, opts)
    try:
        run = RunRecord(module.PROJECT_DIR, opts.profile, opts.geometry, run_dir=opts.run_dir)
    except Exception as exc:
        run = RunRecord(module.PROJECT_DIR, opts.profile, opts.geometry)
        run.finish('FAILED', error={'type': type(exc).__name__, 'message': str(exc)})
        print(json.dumps({'status': 'FAILED', 'runDir': relative(run.root, run.directory), 'error': run.record['error']}))
        return 2
    argv = [*sys.argv[1:], '--geometry', opts.geometry]
    if opts.out:
        argv.extend(['--out', opts.out])
    return supervise(Path(module.__file__).resolve(), argv, run, max(opts.timeout, 1))
