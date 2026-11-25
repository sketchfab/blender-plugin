import atexit
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


active_workers = set()
retired_workers = set()


class WorkerJob:
    def __init__(self, root=None, prefix='sketchfab-'):
        self.directory = Path(tempfile.mkdtemp(dir=root or None, prefix=prefix)).resolve()
        self.process = None

    def start(self, command, env=None):
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, env=env, **flags)
        active_workers.add(self)

    def start_network(self, interpreter, blender, action, parameters, headers=None, password=None):
        script = str(Path(__file__).resolve().with_name('network_worker.py'))
        if interpreter:
            command = [interpreter, '-I', script, str(self.directory)]
        else:
            command = [blender, '--background', '--factory-startup', '--python-exit-code', '1',
                       '--python', script, '--', str(self.directory)]
        with (self.directory / 'request.json').open('x', encoding='utf-8') as request:
            json.dump({'action': action, 'parameters': parameters}, request)
        env = os.environ.copy()
        env['SKETCHFAB_WORKER_SECRETS'] = json.dumps({'headers': headers or {}, 'password': password})
        self.start(command, env)

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def read_result(self):
        if self.process.returncode:
            return {'error': 'The Sketchfab worker stopped unexpectedly'}
        try:
            with (self.directory / 'result.json').open(encoding='utf-8') as result:
                data = json.load(result)
            if isinstance(data, dict) and ('data' in data or 'error' in data):
                return data
        except (OSError, ValueError):
            pass
        return {'error': 'The Sketchfab worker returned no result'}

    def read_prepared_export(self):
        if self.process.returncode:
            raise ValueError(f'Background Blender could not prepare the file (exit {self.process.returncode})')
        with (self.directory / 'export-sketchfab.json').open(encoding='utf-8') as settings:
            result = json.load(settings)
        path = Path(result['filepath']).resolve()
        if path.parent != self.directory or path.name != result['filename'] or path.stat().st_size != result['size']:
            raise ValueError('Background Blender returned an invalid export file')
        return result

    def stop(self):
        if self.running:
            try:
                self.process.kill()
            except ProcessLookupError:
                pass
        if self.running:
            retired_workers.add(self)
        else:
            self.cleanup()

    def cleanup(self):
        if self.directory is not None:
            shutil.rmtree(self.directory, ignore_errors=True)
            self.directory = None
        active_workers.discard(self)
        retired_workers.discard(self)


def reap_workers():
    for job in tuple(retired_workers):
        if not job.running:
            job.cleanup()
    return 0.1 if retired_workers else None


@atexit.register
def stop_workers_at_exit():
    for job in tuple(active_workers):
        job.stop()
    for job in tuple(retired_workers):
        try:
            job.process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            continue
        job.cleanup()
