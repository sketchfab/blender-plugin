from http.server import ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from zipfile import ZipFile

import bpy


WORK = Path(os.environ['SKFB_TEST_ROOT']).resolve()
assert WORK in Path(bpy.utils.user_resource('SCRIPTS')).resolve().parents
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_transfers import DownloadHandler, UID, transfers

repo = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sketchfab_gui_test', repo / '__init__.py')
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.get_temporary_path = lambda: str(WORK / 'downloads')
addon.Cache.SKETCHFAB_CACHE_FILE = WORK / 'synthetic.cache'
addon.register()


class FixtureHandler(DownloadHandler):
    def do_POST(self):
        self.server.posted = self.rfile.read(int(self.headers['Content-Length']))
        self.server.upload_path = self.path
        body = json.dumps({'uid': UID}).encode()
        self.send_response(201)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
server.requests = []
server.mode = 'normal'
server.posted = None
server.started = threading.Event()
server.resume = threading.Event()
server.base = f'http://127.0.0.1:{server.server_port}'
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()

addon.Config.SKETCHFAB_MODEL = server.base + '/models'
addon.Config.SKETCHFAB_ME = server.base + '/me'
addon.Config.SKETCHFAB_ORGS = server.base + '/orgs'

original = WORK / 'original.blend'


def start_import():
    props = addon.get_sketchfab_props()
    props.skfb_api.headers = {'Authorization': 'Token synthetic'}
    props.manualImportBoolean = True
    props.manualImportPath = f'https://sketchfab.com/3d-models/fixture-{UID}/?share=1'
    assert bpy.ops.wm.sketchfab_download() == {'RUNNING_MODAL'}
    return addon.active_download.job


def tests():
    assert bpy.ops.wm.save_as_mainfile(filepath=str(original)) == {'FINISHED'}
    gltf = WORK / 'scene.gltf'
    assert bpy.ops.export_scene.gltf(filepath=str(gltf), export_format='GLTF_SEPARATE',
                                      use_selection=True) == {'FINISHED'}
    archive_path = WORK / 'fixture.zip'
    with ZipFile(archive_path, 'w') as archive:
        archive.write(gltf, 'scene.gltf')
        for buffer in json.loads(gltf.read_text())['buffers']:
            archive.write(gltf.parent / buffer['uri'], buffer['uri'])
        archive.writestr('padding.bin', b'x' * (2 * transfers.CHUNK_SIZE))
    server.archive = archive_path.read_bytes()

    assert bpy.ops.wm.sketchfab_export(size_only=True) == {'RUNNING_MODAL'}
    job = addon.active_upload.job
    yield lambda: addon.active_upload is None
    assert job.error is None and job.prepared['size'] > 0
    assert server.posted is None and not job.directory.exists()
    assert Path(bpy.data.filepath) == original

    api = addon.get_sketchfab_props().skfb_api
    api.headers = {'Authorization': 'Token synthetic'}
    assert bpy.ops.wm.sketchfab_export() == {'RUNNING_MODAL'}
    job = addon.active_upload.job
    yield lambda: addon.active_upload is None
    assert job.error is None and job.model_uid == UID and b'modelFile' in server.posted
    assert server.upload_path == '/models'
    assert addon.uploaded_model_url.endswith(UID)

    api.use_org_profile = True
    api.active_org = {'uid': 'fixture-team', 'username': 'example'}
    job = start_import()
    yield lambda: addon.active_download is None
    assert job.error is None and job.directory is None
    assert 'Fixture' in bpy.context.scene.objects
    assert 'Fixture' in bpy.data.texts['sf_attributions'].as_string()
    assert (f'/models/{UID}/download', 'Token synthetic') in server.requests

    server.mode = 'pause'
    job = start_import()
    yield server.started.is_set
    assert bpy.ops.wm.open_mainfile(filepath=str(original)) == {'FINISHED'}
    server.resume.set()
    yield lambda: not job.is_alive()
    assert addon.active_download is None and job.directory is None
    assert 'sf_attributions' not in bpy.data.texts and 'Fixture' not in bpy.data.objects

    server.started.clear()
    server.resume.clear()
    job = start_import()
    yield server.started.is_set
    addon.unregister()
    server.resume.set()
    yield lambda: not job.is_alive()
    assert addon.active_download is None and job.directory is None
    assert 'sf_attributions' not in bpy.data.texts


steps = tests()
waiting = None
deadline = time.monotonic() + 60


def drive():
    global waiting
    try:
        assert time.monotonic() < deadline, 'Transfer integration timed out'
        if waiting is not None and not waiting():
            return 0.02
        waiting = next(steps)
        return 0.02
    except StopIteration:
        result = 'passed'
    except Exception:
        traceback.print_exc()
        result = 'failed'
    server.resume.set()
    server.shutdown()
    server.server_close()
    (WORK / 'transfer-results.txt').write_text(result)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(drive, first_interval=0.1, persistent=True)
