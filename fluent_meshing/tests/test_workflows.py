import ast
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from mesh_validation import assert_missile_mesh, inspect_mesh, parse_report
from workspace_runtime import RunRecord, supervise, inspect_python
import mesh_missile
import mesh_runner
spec = importlib.util.spec_from_file_location('workflow_comparison', PROJECT / 'tools' / 'compare_with_reference.py')
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


class ValidationTests(unittest.TestCase):
    def test_native_orthogonal_quality_is_checked_and_covers_all_cells(self):
        text = 'Checking mesh...\nminimum volume: 78.4829\ntotal volume: 250000\nDone.\n 46 fluid_box fluid 634 0 0 0 0 634\n'
        result = parse_report(text, [634, 0.61717495, 0.99171967, 0.91985544, 0, 0])
        self.assertEqual(result['qualityCellCount'], 634)
        self.assertEqual(result['fluidRegions'][0]['name'], 'fluid_box')
        for bad in ([634, 0.001, 0.9, 0.8], [0, 0.6, 0.9, 0.8], [634, 0.9, 0.6, 0.8], None):
            with self.assertRaises(RuntimeError):
                parse_report(text, bad)

    def test_report_needs_quality_actual_check_and_a_fluid_zone(self):
        valid = 'Checking mesh...\nminimum volume = 2.5e2\ntotal volume = 2.5e5\nMinimum Orthogonal Quality = 0.8\n 12 zone----enclosure---- fluid 100 0 0 0 0 100\nDone.'
        result = parse_report(valid)
        self.assertEqual(result['totalVolume'], 250000)
        self.assertEqual(result['fluidRegions'], [{'id': 12, 'name': 'zone----enclosure----', 'cells': 100}])
        for bad in (valid.replace('0.8', '0.005'), valid.replace('2.5e2', '-2.5e2'), valid.replace('Minimum Orthogonal Quality = 0.8', ''), valid + '\nERROR: invalid cell', valid.replace(' 12 zone----enclosure---- fluid 100 0 0 0 0 100\n', '')):
            with self.assertRaises(RuntimeError):
                parse_report(bad)

    def test_boundary_zone_table_is_read_from_the_check_transcript(self):
        text = ('Checking mesh...\nminimum volume = 1\ntotal volume = 10\nMinimum Orthogonal Quality = 0.5\n'
                ' 12 zone----enclosure---- fluid 100 0 0 0 0 100\n'
                '      20                            wall                 wall      325        0        0      325\n'
                '      19                          outlet      pressure-outlet       45        0        0       45\n'
                '      18                           inlet       velocity-inlet       45        0        0       45\nDone.')
        boundaries = {zone['name']: zone for zone in parse_report(text)['boundaryZones']}
        self.assertEqual(boundaries['wall']['faces'], 325)
        self.assertEqual(boundaries['inlet']['type'], 'velocity-inlet')

    def test_comparison_ignores_only_recording_fields_in_known_tasks(self):
        task = lambda **args: {'Arguments': args}
        self.assertEqual(comparison.task_differences('Add Boundary Layers', task(), task(AddChild='yes')), [])
        self.assertTrue(comparison.task_differences('Unknown task', task(), task(AddChild='yes')))
        self.assertTrue(comparison.task_differences('Generate the Volume Mesh', task(VolumeFillControls={'GrowthRate': 1.2}), task(VolumeFillControls={'GrowthRate': 1.3})))
        self.assertTrue(comparison.task_differences('last-ratio_1', task(FirstHeight=1), task(FirstHeight=2)))

    def test_recorded_parameters_match_complete_original_backup(self):
        original = PROJECT.parent / 'archive/migration-20261014T084018Z/files/mcp_skill/Fluent_meshing_mcp/mesh_missile.py'
        def parameters(file):
            result = {}
            for node in ast.parse(file.read_text(encoding='utf-8')).body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id.startswith('P_'):
                            result[target.id] = ast.dump(node.value, include_attributes=False)
            return result
        self.assertEqual(parameters(PROJECT / 'mesh_missile.py'), parameters(original))

    def test_boundary_layer_zones_stay_the_recorded_ones(self):
        # These are read from the model, not from the recorded parameter block, so
        # the static comparison above keeps passing while the names stay explicit.
        self.assertEqual(mesh_missile.REQUIRED_BOUNDARY_ZONES, ['inlet', 'wall', 'outlet'])
        self.assertIn('missile-zone----enclosure----_43', mesh_missile.P_BOUNDARY_LAYER['BLZoneList'])

    def test_exported_mesh_must_carry_the_regions_and_boundary_groups(self):
        mesh = {
            'dimension': 3, 'units': 'mm', 'cellCount': 10, 'faceCount': 20, 'nodeCount': 30,
            'cellZones': {'zone----enclosure----': 10, 'dead-solid': 0},
            'faceZones': {'inlet': 4, 'outlet': 4, 'wall': 8, 'missile-zone----enclosure----_43': 40},
        }
        groups = assert_missile_mesh(mesh)
        self.assertEqual(groups['fluidCellZones'], {'zone----enclosure----': 10})
        self.assertEqual(groups['missileWallZones'], ['missile-zone----enclosure----_43'])
        for broken in (
            {**mesh, 'faceZones': {**mesh['faceZones'], 'wall': 0}},
            {**mesh, 'faceZones': {name: count for name, count in mesh['faceZones'].items() if not name.startswith('missile')}},
            {**mesh, 'cellZones': {'a': 1, 'b': 2}},
            {**mesh, 'cellZones': {'a': 0}},
        ):
            with self.assertRaises(RuntimeError):
                assert_missile_mesh(broken)

    def test_quality_report_must_agree_with_the_exported_file(self):
        mesh = {
            'dimension': 3, 'units': 'mm', 'cellCount': 10, 'faceCount': 20, 'nodeCount': 30,
            'cellZones': {'zone----enclosure----': 10},
            'faceZones': {'inlet': 4, 'outlet': 4, 'wall': 8, 'missile-zone----enclosure----_43': 40},
        }
        agreeing = {'qualityCellCount': 10, 'boundaryZones': [{'name': 'inlet', 'faces': 4}, {'name': 'outlet', 'faces': 4}, {'name': 'wall', 'faces': 8}]}
        assert_missile_mesh(mesh, agreeing)
        with self.assertRaises(RuntimeError):
            assert_missile_mesh(mesh, {**agreeing, 'qualityCellCount': 9})
        with self.assertRaises(RuntimeError):
            assert_missile_mesh(mesh, {**agreeing, 'boundaryZones': [{'name': 'inlet', 'faces': 4}]})

    def test_mesh_inspection_rejects_an_empty_or_non_metric_export(self):
        def fake_h5(attrs, cells, faces):
            class Node(dict):
                def __init__(self, attrs):
                    self.attrs = attrs
            mesh = Node(attrs)
            mesh['cells'] = {'zoneTopology': cells}
            mesh['faces'] = {'zoneTopology': faces}
            return {'meshes/1': mesh}
        import numpy as np
        good = fake_h5(
            {'dimension': np.int32(3), 'units': np.bytes_(b'mm'), 'cellCount': np.uint64(5), 'faceCount': np.uint64(6), 'nodeCount': np.uint64(7)},
            {'name': np.array([b'zone----enclosure----']), 'minId': np.array([0]), 'maxId': np.array([4])},
            {'name': np.array([b'inlet']), 'minId': np.array([0]), 'maxId': np.array([3])},
        )
        with patch.dict(sys.modules, {'h5py': SimpleNamespace(File=lambda *a, **k: FakeHandle(good))}):
            result = inspect_mesh('whatever.h5')
        self.assertEqual(result['cellZones'], {'zone----enclosure----': 5})
        self.assertEqual(result['faceZones'], {'inlet': 4})
        empty = fake_h5(
            {'dimension': np.int32(3), 'units': np.bytes_(b'm'), 'cellCount': np.uint64(0), 'faceCount': np.uint64(6), 'nodeCount': np.uint64(7)},
            {'name': np.array([b'zone----enclosure----']), 'minId': np.array([0]), 'maxId': np.array([4])},
            {'name': np.array([b'inlet']), 'minId': np.array([0]), 'maxId': np.array([3])},
        )
        with patch.dict(sys.modules, {'h5py': SimpleNamespace(File=lambda *a, **k: FakeHandle(empty))}):
            with self.assertRaises(RuntimeError):
                inspect_mesh('whatever.h5')


class FakeHandle:
    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self.content

    def __exit__(self, *args):
        return False


class MappingTests(unittest.TestCase):
    def test_region_names_are_resolved_not_guessed(self):
        log = Mock()
        self.assertEqual(mesh_missile.resolve_name('inlet', ['inlet', 'outlet'], 'zone', log), 'inlet')
        self.assertEqual(mesh_missile.resolve_name('inlet', ['inlet-1', 'outlet'], 'zone', log), 'inlet-1')
        with self.assertRaises(mesh_missile.MappingError):
            mesh_missile.resolve_name('inlet', ['inlet-1', 'inlet-2'], 'zone', log)
        with self.assertRaises(mesh_missile.MappingError):
            mesh_missile.resolve_name('wall', ['inlet', 'outlet'], 'zone', log)

    def test_both_region_sizing_targets_must_resolve_uniquely(self):
        log = Mock()
        self.assertEqual(mesh_missile.resolve_name('missile', ['missile', 'zone----enclosure----'], 'region', log), 'missile')
        self.assertEqual(mesh_missile.resolve_name('enclosure', ['missile', 'enclosure-solid'], 'region', log), 'enclosure-solid')
        with self.assertRaises(mesh_missile.MappingError):
            mesh_missile.resolve_name('enclosure', ['missile', 'a-enclosure', 'b-enclosure'], 'region', log)
        with self.assertRaises(mesh_missile.MappingError):
            mesh_missile.resolve_name('enclosure', ['missile'], 'region', log)

    def test_cell_zones_are_read_from_the_live_mesh_listing(self):
        listing = (
            '      id                                name      type     count      tet      hex  pyramid    wedge     poly\n'
            '  ______       _____________________________    ______    ______   ______   ______   ______   ______   ______\n'
            '       1                              missile     fluid    120000        0        0        0        0   120000\n'
            '       2                zone----enclosure----     fluid   2518996        0        0        0        0  2518996\n'
        )
        meshing = SimpleNamespace(tui=SimpleNamespace(mesh=SimpleNamespace(manage=SimpleNamespace(list=lambda: listing))))
        self.assertEqual(mesh_missile.cell_zone_names(meshing), ['missile', 'zone----enclosure----'])

        def unsupported():
            raise RuntimeError('unsupported')

        broken = SimpleNamespace(tui=SimpleNamespace(mesh=SimpleNamespace(manage=SimpleNamespace(list=unsupported))))
        with self.assertRaises(mesh_missile.MappingError):
            mesh_missile.cell_zone_names(broken)

    def test_the_generated_mesh_must_hold_the_region_that_was_sized(self):
        log = Mock()
        quality = {'fluidRegions': [{'name': 'zone----enclosure----', 'cells': 2495263}]}
        self.assertEqual(mesh_missile.assert_region_scope(quality, 'zone----enclosure----', log), 'zone----enclosure----')
        self.assertEqual(mesh_missile.resolve_from_quality(quality, 'zone----enclosure----', log), 'zone----enclosure----')
        for bad in (
            {'fluidRegions': []},
            {'fluidRegions': [{'name': 'missile', 'cells': 1}, {'name': 'zone----enclosure----', 'cells': 2}]},
            {'fluidRegions': [{'name': 'dead', 'cells': 0}]},
        ):
            with self.assertRaises(mesh_missile.MappingError):
                mesh_missile.assert_region_scope(bad, 'zone----enclosure----', log)
        with self.assertRaises(mesh_missile.MappingError):
            mesh_missile.assert_region_scope(quality, 'other-region', log)

    def test_import_labels_are_required_when_the_session_exposes_them(self):
        log = Mock()
        state = {'value': ';'.join(mesh_missile.REQUIRED_FACE_LABELS)}

        def geometry_labels():
            value = state['value']
            if callable(value):
                value()
            return value

        class Session:
            def __init__(self):
                self.tui = SimpleNamespace(geometry=SimpleNamespace(labels=geometry_labels))

        mesh_missile.step_check_import(log, Session())
        state['value'] = 'missile_body;missile_nose'
        with self.assertRaises(mesh_missile.MappingError):
            mesh_missile.step_check_import(log, Session())
        state['value'] = lambda: (_ for _ in ()).throw(RuntimeError('unsupported'))
        mesh_missile.step_check_import(log, Session())

    def test_import_zones_are_reported_when_the_session_exposes_them(self):
        log = Mock()
        state = {'labels': 'unsupported'}

        def geometry_labels():
            value = state['labels']
            if callable(value):
                value()
            return value

        class Session:
            def __init__(self):
                self.tui = SimpleNamespace(
                    geometry=SimpleNamespace(labels=geometry_labels),
                    boundary=SimpleNamespace(list=lambda: 'inlet\noutlet\nwall'),
                )

        state['labels'] = lambda: (_ for _ in ()).throw(RuntimeError('unsupported'))
        inspected = mesh_missile.inspect_names(Session(), log)
        self.assertEqual(inspected['zones'], ['inlet', 'outlet', 'wall'])


class RunTests(unittest.TestCase):
    def setUp(self):
        support = PROJECT.parent / 'data_lib' / 'runtime' / 'test-support'
        support.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=str(support))
        self.root = Path(self.temp.name)
        self.project = self.root / 'workflow'
        self.project.mkdir()
        (self.root / 'workspace.json').write_text(json.dumps({'dataRoot': 'data_lib', 'feedbackRoot': 'feedback', 'inputRoot': 'data_lib/inputs', 'legacyRoot': 'data_lib/legacy'}))
        self.input = self.root / 'model.scdoc'
        self.input.write_bytes(b'neutral test input')

    def tearDown(self):
        self.temp.cleanup()

    def run_record(self):
        return RunRecord(self.project, 'recorded', self.input)

    def test_failed_artifacts_are_retained_with_input_hash(self):
        run = self.run_record()
        run.check_input(self.input)
        (run.directory / 'partial.mesh').write_bytes(b'failed artifact')
        run.finish('FAILED', error={'type': 'TestError', 'message': 'intentional'})
        self.assertTrue((run.directory / 'partial.mesh').exists())
        self.assertIn('feedback', run.directory.parts)
        self.assertEqual(len(run.record['inputs'][0]['sha256']), 64)
        self.assertFalse(run.record['verification']['runtimeVerified'])

    def test_missing_input_has_failure_manifest(self):
        run = self.run_record()
        with self.assertRaises(FileNotFoundError):
            run.check_input(self.root / 'missing.scdoc')
        run.finish('FAILED', error={'type': 'FileNotFoundError', 'message': 'missing input'})
        self.assertEqual(json.loads((run.directory / 'run.json').read_text())['status'], 'FAILED')

    def test_supervisor_timeout_collects_feedback(self):
        run = self.run_record()
        script = self.root / 'sleep.py'
        script.write_text('import time\ntime.sleep(20)\n')
        self.assertEqual(supervise(script, [], run, 0.2), 1)
        self.assertEqual(run.record['error']['type'], 'TimeoutError')
        self.assertTrue((run.directory / 'supervisor.log').exists())

    def test_zero_exit_without_validation_is_not_pass(self):
        run = self.run_record()
        script = self.root / 'no-evidence.py'
        script.write_text('pass\n')
        self.assertEqual(supervise(script, [], run, 5), 1)
        self.assertEqual(run.record['status'], 'FAILED')

    def test_a_worker_that_exits_before_recording_completion_fails(self):
        run = self.run_record()
        script = self.root / 'no-record.py'
        script.write_text('print("worker finished")\n')
        self.assertEqual(supervise(script, [], run, 30), 1)
        self.assertEqual(run.record['status'], 'FAILED')
        self.assertFalse(run.record['verification']['runtimeVerified'])

    def test_inspection_interpreter_is_configurable_and_isolated(self):
        (self.project / '.mesh-inspect-python').write_text(sys.executable)
        self.assertEqual(inspect_python(self.project), Path(sys.executable))
        (self.project / '.mesh-inspect-python').unlink()
        with patch.dict('os.environ', {'MESH_INSPECT_PYTHON': sys.executable}):
            self.assertEqual(inspect_python(self.project), Path(sys.executable))
        with patch.dict('os.environ', {'MESH_INSPECT_PYTHON': ''}):
            self.assertIsNone(inspect_python(self.project))

    def fake(self, run):
        module = SimpleNamespace(PROJECT_DIR=str(self.project), Logger=mesh_missile.Logger, StopHere=mesh_missile.StopHere, MESH_NAME='test.msh.h5', ensure_writable_appdata=Mock(), step_import=Mock())
        opts = SimpleNamespace(profile='recorded', geometry=str(self.input), run_dir=str(run.directory), processors=2, start_timeout=300, timeout=900, out=None, keep_fluent=False, show_gui=False, draw_size_control='true', stop_after=None, worker=True)
        session = Mock()
        session.workflow = Mock()
        return module, opts, session

    def test_launch_failure_is_recorded(self):
        run = self.run_record()
        module, opts, session = self.fake(run)
        with patch('ansys.fluent.core.launch_fluent', side_effect=RuntimeError('intentional launch failure')):
            self.assertEqual(mesh_runner.worker(module, opts), 1)
        manifest = json.loads((run.directory / 'run.json').read_text())
        self.assertEqual(manifest['status'], 'FAILED')
        self.assertIn('launch failure', manifest['error']['message'])

    def test_stop_is_not_pass_and_session_is_closed(self):
        run = self.run_record()
        module, opts, session = self.fake(run)
        opts.stop_after = 'import'
        module.step_import.side_effect = mesh_missile.StopHere()
        with patch('ansys.fluent.core.launch_fluent', return_value=session):
            self.assertEqual(mesh_runner.worker(module, opts), 0)
        manifest = json.loads((run.directory / 'run.json').read_text())
        self.assertEqual(manifest['status'], 'STOPPED')
        self.assertFalse(manifest['verification']['runtimeVerified'])
        session.exit.assert_called_once()

    def test_check_failure_is_recorded_and_session_is_closed(self):
        run = self.run_record()
        module, opts, session = self.fake(run)
        module.check_stop = Mock()
        with patch('ansys.fluent.core.launch_fluent', return_value=session), patch.object(mesh_runner, 'recorded_steps', return_value={'RegionNameList': ['zone----enclosure----'], 'RegionTypeList': ['fluid']}), patch.object(mesh_runner, 'report_mesh', side_effect=RuntimeError('intentional check failure')):
            self.assertEqual(mesh_runner.worker(module, opts), 1)
        manifest = json.loads((run.directory / 'run.json').read_text())
        self.assertIn('check failure', manifest['error']['message'])
        self.assertEqual(manifest['status'], 'FAILED')
        session.exit.assert_called_once()

    def test_geometry_has_no_default_model(self):
        with patch.object(sys, 'argv', ['mesh_missile.py']):
            with self.assertRaises(SystemExit):
                mesh_runner.options(mesh_missile)


if __name__ == '__main__':
    unittest.main()
