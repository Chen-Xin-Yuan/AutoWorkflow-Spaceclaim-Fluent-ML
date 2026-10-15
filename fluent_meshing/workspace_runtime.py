"""Run provenance and bounded execution for local workflow processes."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from datetime import datetime, timezone
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256(file):
    digest = hashlib.sha256()
    with open(file, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def workspace(project_dir):
    root = Path(project_dir).resolve().parent
    config_path = root / 'workspace.json'
    if config_path.is_file():
        config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    else:
        import tempfile
        root = Path(tempfile.gettempdir()) / 'ansys-workflow'
        config = {'dataRoot': 'data_lib', 'feedbackRoot': 'feedback', 'inputRoot': 'data_lib/inputs', 'legacyRoot': 'data_lib/legacy'}
    return root, config


def checked(root, relative):
    target = (root / relative).resolve()
    try:
        local = target.relative_to(root)
    except ValueError:
        raise ValueError('Output path outside workspace')
    if not local.parts or local.parts[0].lower() == 'missile_project':
        raise ValueError('Protected or root output path')
    return target


def relative(root, file):
    return os.path.relpath(str(Path(file).resolve()), str(root)).replace('\\', '/')


class RunRecord:
    def __init__(self, project_dir, profile, geometry, run_dir=None, reopen=False):
        self.root, self.config = workspace(project_dir)
        if reopen:
            self.directory = Path(run_dir).resolve()
            self.record = json.loads((self.directory / 'run.json').read_text(encoding='utf-8'))
            if self.record['stage'] != 'fluent_meshing':
                raise ValueError('Unexpected run owner')
            return
        run_id = 'run-' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S') + '-' + uuid.uuid4().hex[:8]
        self.directory = Path(run_dir).resolve() if run_dir else checked(self.root, Path(self.config['dataRoot']) / 'runs' / run_id / 'fluent_meshing')
        protected = self.root / 'missile_project'
        if self.directory == protected or protected in self.directory.parents:
            raise ValueError('Protected output path')
        if self.directory.exists():
            raise FileExistsError('Run directory already exists; use a new directory')
        self.directory.mkdir(parents=True, exist_ok=False)
        self.record = {'schemaVersion': 1, 'runId': run_id, 'stage': 'fluent_meshing', 'profile': profile, 'status': 'RUNNING', 'startedAt': now(), 'finishedAt': None, 'inputs': [{'path': relative(self.root, geometry), 'sha256': None}], 'artifacts': [], 'verification': {'runtimeVerified': False}, 'error': None}
        self.save()

    def save(self):
        file = self.directory / 'run.json'
        temp = self.directory / 'run.json.tmp'
        temp.write_text(json.dumps(self.record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(temp, file)

    def check_input(self, geometry):
        file = Path(geometry).resolve()
        if self.root / 'missile_project' == file or self.root / 'missile_project' in file.parents:
            raise ValueError('Protected project input')
        if not file.is_file():
            raise FileNotFoundError('Geometry input does not exist: ' + relative(self.root, file))
        self.record['inputs'] = [{'path': relative(self.root, file), 'size': file.stat().st_size, 'sha256': sha256(file)}]
        self.save()

    def finish(self, status, verification=None, error=None, relocate=True):
        if status == 'FAILED' and relocate:
            destination = checked(self.root, Path(self.config['feedbackRoot']) / 'fluent_meshing' / self.record['runId'])
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise FileExistsError('Feedback run already exists')
            original = self.directory
            # Source is exactly the directory initialized by this run record.
            if not (original / 'run.json').is_file():
                raise ValueError('Missing run ownership record')
            moved = False
            for attempt in range(10):
                try:
                    original.rename(destination)
                    moved = True
                    break
                except PermissionError:
                    time.sleep(0.2)
            if not moved:
                # Some ANSYS helpers briefly retain the directory as cwd after
                # RPC shutdown. Retain a real feedback copy without discarding
                # the source or terminating any unrelated process.
                shutil.copytree(original, destination, copy_function=shutil.copy2)
                redirect = dict(self.record)
                redirect['redirectManifest'] = relative(self.root, destination / 'run.json')
                (original / 'run.json').write_text(json.dumps(redirect, indent=2)+'\n', encoding='utf-8')
            self.directory = destination
            self.record['pathMapping'] = {'from': relative(self.root, original), 'to': relative(self.root, destination)}
        self.record.update(status=status, finishedAt=now(), verification=verification or {'runtimeVerified': False}, error=error)
        if status != 'PASSED':
            self.record['verification']['runtimeVerified'] = False
        artifacts = []
        for directory, directories, files in os.walk(self.directory):
            if any((Path(directory) / name).is_symlink() for name in directories):
                raise ValueError('Unexpected link in generated run')
            for name in files:
                file = Path(directory) / name
                if name in ('run.json', 'run.json.tmp'):
                    continue
                if file.is_symlink():
                    raise ValueError('Unexpected linked artifact')
                artifacts.append({'path': relative(self.root, file), 'size': file.stat().st_size, 'sha256': sha256(file)})
        self.record['artifacts'] = artifacts
        self.save()


def inspect_python(project_dir):
    """Locate the interpreter of the isolated mesh-inspection environment.

    h5py must not be mixed into the PyFluent environment, so the exported mesh is
    inspected by a separate interpreter whose dependency folder is recorded in a
    local (git-ignored) file, exactly like the PyFluent interpreter is.
    """
    project = Path(project_dir).resolve()
    configured = os.environ.get('MESH_INSPECT_PYTHON')
    if configured and Path(configured).is_file():
        return Path(configured)
    for name in ('.mesh-inspect-python', '.mesh_inspect_python'):
        candidate = project / name
        if candidate.is_file():
            value = candidate.read_text(encoding='utf-8-sig').strip()
            if value and Path(value).is_file():
                return Path(value)
    return None


def inspect_exported_mesh(project_dir, run_dir, mesh_file):
    """Verify the exported mesh file with the isolated inspection environment."""
    file = Path(mesh_file).resolve()
    if not file.is_file() or file.stat().st_size <= 0:
        raise RuntimeError('Exported mesh file is missing or empty: ' + relative(Path(project_dir).resolve().parent, file))
    interpreter = inspect_python(project_dir)
    if interpreter is None:
        raise RuntimeError('The isolated mesh-inspection interpreter is not configured; restore .mesh-inspect-python or set MESH_INSPECT_PYTHON.')
    project = Path(project_dir).resolve()
    dependency = project / '.mesh_inspect_deps_313'
    environment = dict(os.environ)
    if dependency.is_dir():
        environment['PYTHONPATH'] = str(dependency)
    else:
        environment.pop('PYTHONPATH', None)
    report = Path(run_dir) / 'mesh-inspection.json'
    script = project / 'tools' / 'inspect_mesh_file.py'
    completed = subprocess.run(
        [str(interpreter), str(script), str(file)],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        cwd=str(project),
        env=environment,
        timeout=600,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    if completed.returncode != 0:
        report.write_text((completed.stdout or '') + (completed.stderr or ''), encoding='utf-8')
        raise RuntimeError('Mesh file inspection failed: ' + (completed.stderr or completed.stdout or '').strip()[-500:])
    payload = json.loads(completed.stdout)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return payload


def stop_owned_tree(child):
    """Only stop the process started by this invocation and its descendants."""
    if child.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
        if child.poll() is None:
            child.kill()
    else:
        child.kill()
    try:
        child.wait(timeout=20)
    except subprocess.TimeoutExpired:
        child.kill()


def supervise(script, argv, run, timeout_seconds):
    import sys
    output = run.directory / 'supervisor.log'
    timed_out = False
    with output.open('w', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, str(script), *argv, '--worker', '--run-dir', str(run.directory)], stdout=log, stderr=subprocess.STDOUT, cwd=str(run.directory), creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        run.record['workerPid'] = child.pid
        run.save()
        try:
            code = child.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop_owned_tree(child)
            code = 1
    record_file = run.directory / 'run.json'
    if record_file.exists():
        run.record = json.loads(record_file.read_text(encoding='utf-8'))
    if timed_out:
        run.finish('FAILED', error={'type': 'TimeoutError', 'message': 'Workflow exceeded total runtime limit'})
    elif run.record['status'] == 'RUNNING' or (code != 0 and run.record['status'] != 'FAILED'):
        run.finish('FAILED', error={'type': 'WorkerError', 'message': 'Worker exited without a valid completion record'})
    else:
        verification, error = run.record.get('verification') or {}, run.record.get('error')
        # A passed worker only proves the in-session checks; the written file is
        # verified independently, in the isolated inspection environment.
        if run.record['status'] == 'PASSED':
            try:
                from mesh_validation import assert_missile_mesh
                exported = inspect_exported_mesh(script.parent, run.directory, verification['outputPath'])
                group = assert_missile_mesh(exported, verification.get('quality'))
                verification = {**verification, 'exportedMesh': exported, 'groups': group}
            except Exception as exc:  # noqa: BLE001
                verification['runtimeVerified'] = False
                error = {'type': type(exc).__name__, 'message': str(exc)}
                run.finish('FAILED', verification=verification, error=error)
                print(json.dumps({'status': 'FAILED', 'runId': run.record['runId'], 'runDir': relative(run.root, run.directory), 'verification': run.record['verification']}, ensure_ascii=False))
                return 1
        run.finish(run.record['status'], verification, error)
    print(json.dumps({'status': run.record['status'], 'runId': run.record['runId'], 'runDir': relative(run.root, run.directory), 'verification': run.record['verification']}, ensure_ascii=False))
    return 0 if run.record['status'] in ('PASSED', 'STOPPED') and code == 0 else 1
