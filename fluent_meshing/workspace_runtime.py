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


# ---------------------------------------------------------------------------
# 输入 / 输出 / 参数配置（相对路径按 workspace() 的工作区根目录解析）
# ---------------------------------------------------------------------------
# 输入：仓库父目录的 WORKSPACE_CONFIG_NAME；存在时使用其中的 dataRoot、
# feedbackRoot 等值。不存在时仅回退到系统临时目录 / FALLBACK_WORKSPACE_NAME。
# 几何文件、run_dir、mesh_file 均由调用方传入；显式 run_dir 优先于自动目录。
# 输出：dataRoot/RUNS_SUBDIR/<run-id>/fluent_meshing；失败后保留到
# feedbackRoot/fluent_meshing/<run-id>。日志和报告名相对每次运行目录。
# 核验解释器优先 MESH_INSPECT_PYTHON，再依次读取本地配置文件；依赖目录
# 相对 project_dir，与 _deps 隔离。所有动态路径均在调用时生成。
WORKSPACE_CONFIG_NAME = 'workspace.json'
FALLBACK_WORKSPACE_NAME = 'ansys-workflow'
DEFAULT_DATA_ROOT = 'data_lib'
DEFAULT_FEEDBACK_ROOT = 'feedback'
DEFAULT_INPUT_ROOT = 'data_lib/inputs'
DEFAULT_LEGACY_ROOT = 'data_lib/legacy'
RUNS_SUBDIR = 'runs'
RUN_ID_PREFIX = 'run-'
RUN_ID_TIME_FORMAT = '%Y%m%d%H%M%S'
RUN_MANIFEST_NAME = 'run.json'
RUN_MANIFEST_TEMP_NAME = 'run.json.tmp'
SUPERVISOR_LOG_NAME = 'supervisor.log'
MESH_INSPECTION_NAME = 'mesh-inspection.json'
INSPECT_PYTHON_CONFIG_NAMES = ('.mesh-inspect-python', '.mesh_inspect_python')
INSPECT_DEPENDENCY_DIR = '.mesh_inspect_deps_313'
INSPECT_TOOLS_DIR = 'tools'
INSPECT_SCRIPT_NAME = 'inspect_mesh_file.py'
INSPECT_TIMEOUT_SECONDS = 600
RELOCATE_ATTEMPTS = 10
RELOCATE_RETRY_SECONDS = 0.2
PROCESS_KILL_TIMEOUT_SECONDS = 30
PROCESS_WAIT_TIMEOUT_SECONDS = 20
HASH_CHUNK_BYTES = 1024 * 1024
# run.json / run.json.tmp 是运行记录协议名，调整时须同步所有记录消费者。


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256(file):
    digest = hashlib.sha256()
    with open(file, 'rb') as stream:
        for chunk in iter(lambda: stream.read(HASH_CHUNK_BYTES), b''):
            digest.update(chunk)
    return digest.hexdigest()


def workspace(project_dir):
    root = Path(project_dir).resolve().parent
    config_path = root / WORKSPACE_CONFIG_NAME
    if config_path.is_file():
        config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    else:
        import tempfile
        root = Path(tempfile.gettempdir()) / FALLBACK_WORKSPACE_NAME
        config = {'dataRoot': DEFAULT_DATA_ROOT, 'feedbackRoot': DEFAULT_FEEDBACK_ROOT, 'inputRoot': DEFAULT_INPUT_ROOT, 'legacyRoot': DEFAULT_LEGACY_ROOT}
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
            self.record = json.loads((self.directory / RUN_MANIFEST_NAME).read_text(encoding='utf-8'))
            if self.record['stage'] != 'fluent_meshing':
                raise ValueError('Unexpected run owner')
            return
        run_id = RUN_ID_PREFIX + datetime.now(timezone.utc).strftime(RUN_ID_TIME_FORMAT) + '-' + uuid.uuid4().hex[:8]
        self.directory = Path(run_dir).resolve() if run_dir else checked(self.root, Path(self.config['dataRoot']) / RUNS_SUBDIR / run_id / 'fluent_meshing')
        protected = self.root / 'missile_project'
        if self.directory == protected or protected in self.directory.parents:
            raise ValueError('Protected output path')
        if self.directory.exists():
            raise FileExistsError('Run directory already exists; use a new directory')
        self.directory.mkdir(parents=True, exist_ok=False)
        self.record = {'schemaVersion': 1, 'runId': run_id, 'stage': 'fluent_meshing', 'profile': profile, 'status': 'RUNNING', 'startedAt': now(), 'finishedAt': None, 'inputs': [{'path': relative(self.root, geometry), 'sha256': None}], 'artifacts': [], 'verification': {'runtimeVerified': False}, 'error': None}
        self.save()

    def save(self):
        file = self.directory / RUN_MANIFEST_NAME
        temp = self.directory / RUN_MANIFEST_TEMP_NAME
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
            if not (original / RUN_MANIFEST_NAME).is_file():
                raise ValueError('Missing run ownership record')
            moved = False
            for attempt in range(RELOCATE_ATTEMPTS):
                try:
                    original.rename(destination)
                    moved = True
                    break
                except PermissionError:
                    time.sleep(RELOCATE_RETRY_SECONDS)
            if not moved:
                # Some ANSYS helpers briefly retain the directory as cwd after
                # RPC shutdown. Retain a real feedback copy without discarding
                # the source or terminating any unrelated process.
                shutil.copytree(original, destination, copy_function=shutil.copy2)
                redirect = dict(self.record)
                redirect['redirectManifest'] = relative(self.root, destination / RUN_MANIFEST_NAME)
                (original / RUN_MANIFEST_NAME).write_text(json.dumps(redirect, indent=2)+'\n', encoding='utf-8')
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
                if name in (RUN_MANIFEST_NAME, RUN_MANIFEST_TEMP_NAME):
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
    for name in INSPECT_PYTHON_CONFIG_NAMES:
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
    dependency = project / INSPECT_DEPENDENCY_DIR
    environment = dict(os.environ)
    if dependency.is_dir():
        environment['PYTHONPATH'] = str(dependency)
    else:
        environment.pop('PYTHONPATH', None)
    report = Path(run_dir) / MESH_INSPECTION_NAME
    script = project / INSPECT_TOOLS_DIR / INSPECT_SCRIPT_NAME
    completed = subprocess.run(
        [str(interpreter), str(script), str(file)],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        cwd=str(project),
        env=environment,
        timeout=INSPECT_TIMEOUT_SECONDS,
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
        subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=PROCESS_KILL_TIMEOUT_SECONDS, check=False)
        if child.poll() is None:
            child.kill()
    else:
        child.kill()
    try:
        child.wait(timeout=PROCESS_WAIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        child.kill()


def supervise(script, argv, run, timeout_seconds):
    import sys
    output = run.directory / SUPERVISOR_LOG_NAME
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
    record_file = run.directory / RUN_MANIFEST_NAME
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
