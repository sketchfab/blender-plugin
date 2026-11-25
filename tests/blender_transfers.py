import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
import time
import traceback
from unittest.mock import patch
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


server = subprocess.Popen([addon.cached_login.interpreter(), '-I',
                           str(repo / 'tests/fake_transfer_api.py'), str(WORK)], stdout=subprocess.PIPE, text=True)
base = f'http://127.0.0.1:{int(server.stdout.readline())}'
addon.Config.SKETCHFAB_MODEL = base + '/models'
addon.Config.SKETCHFAB_ME = base + '/me'
addon.Config.SKETCHFAB_ORGS = base + '/orgs'

original = WORK / 'original.blend'


def start_import():
    props = addon.get_sketchfab_props()
    props.skfb_api.headers = {'Authorization': 'Token synthetic'}
    props.manualImportBoolean = True
    props.manualImportPath = f'https://sketchfab.com/3d-models/fixture-{UID}/?share=1'
    assert bpy.ops.wm.sketchfab_download() == {'RUNNING_MODAL'}
    return addon.active_download.job


def tests():
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=64, y_subdivisions=64)
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

    assert bpy.ops.wm.sketchfab_export(size_only=True) == {'RUNNING_MODAL'}
    job = addon.active_upload.job
    yield lambda: addon.active_upload is None
    assert job.process.returncode == 0 and job.directory is None
    assert not (WORK / 'upload.json').exists()
    assert Path(bpy.data.filepath) == original

    api = addon.get_sketchfab_props().skfb_api
    api.headers = {'Authorization': 'Token synthetic'}
    api.plan_type = 'basic'
    api.active_org = {'uid': '1' * 32, 'username': 'example', 'projects': []}
    export = bpy.context.window_manager.sketchfab_export
    export.reuploadBoolean = True
    for use_team, path, expected_mb in (
        (True, f'https://sketchfab.com/models/{UID}', 100),
        (False, f'https://sketchfab.com/orgs/example/models/{UID}', 500),
    ):
        api.use_org_profile = use_team
        export.reuploadPath = path
        _, plan = addon.capture_upload(bpy.context, api)
        assert bpy.ops.wm.sketchfab_export(size_only=True) == {'RUNNING_MODAL'}
        expected_limit = expected_mb * 1024 * 1024
        assert addon.active_upload.upload_limit == addon.Config.SKETCHFAB_UPLOAD_LIMITS[plan] == expected_limit
        job = addon.active_upload.job
        yield lambda: addon.active_upload is None
        assert job.process.returncode == 0 and job.directory is None
        assert not (WORK / 'upload.json').exists()
    export.reuploadPath = ''
    assert addon.capture_upload(bpy.context, api, size_only=True) == (None, None)
    export.reuploadBoolean = False

    with patch.dict(addon.Config.SKETCHFAB_UPLOAD_LIMITS, {'basic': 1}):
        assert bpy.ops.wm.sketchfab_export() == {'RUNNING_MODAL'}
        job = addon.active_upload.job
        yield lambda: addon.active_upload is None
        assert job.process.returncode == 0 and job.directory is None
        assert not (WORK / 'upload.json').exists()

    assert bpy.ops.wm.sketchfab_export() == {'RUNNING_MODAL'}
    job = addon.active_upload.job
    prepare_pid = job.process.pid
    read_prepared_export = job.read_prepared_export
    limit = None

    def prepare_at_upload_limit():
        nonlocal limit
        prepared = read_prepared_export()
        with ZipFile(prepared['filepath']) as archive:
            source, = archive.infolist()
        limit = prepared['size']
        assert limit < source.file_size
        addon.active_upload.upload_limit = limit
        return prepared

    with patch.object(job, 'read_prepared_export', prepare_at_upload_limit):
        yield lambda: addon.active_upload is None
    assert job.process.pid != prepare_pid
    uploaded = json.loads((WORK / 'upload.json').read_text())
    assert uploaded['path'] == '/models' and uploaded['authenticated']
    assert uploaded['filename'].endswith('.zip') and uploaded['size'] == limit < uploaded['source_size']
    assert uploaded['source_name'].endswith('.glb' if bpy.app.version >= (4, 0, 0) else '.blend')
    assert addon.uploaded_model_url.endswith(UID)

    api.use_org_profile = True
    api.active_org = {'uid': 'fixture-team', 'username': 'example'}
    job = start_import()
    assert job.process.pid != os.getpid()
    yield lambda: addon.active_download is None
    assert job.process.returncode == 0 and job.directory is None
    assert 'Fixture' in bpy.context.scene.objects
    assert 'Fixture' in bpy.data.texts['sf_attributions'].as_string()
    assert (WORK / 'personal-download').is_file()

    (WORK / 'pause').write_text('load')
    job = start_import()
    yield (WORK / 'load-started').is_file
    assert bpy.ops.wm.open_mainfile(filepath=str(original)) == {'FINISHED'}
    yield lambda: job.directory is None
    assert addon.active_download is None and job.process.returncode != 0
    assert 'sf_attributions' not in bpy.data.texts and 'Fixture' not in bpy.data.objects
    (WORK / 'load-resume').touch()

    (WORK / 'pause').write_text('disable')
    job = start_import()
    yield (WORK / 'disable-started').is_file
    addon.unregister()
    yield lambda: job.directory is None
    assert addon.active_download is None and job.process.returncode != 0
    assert 'sf_attributions' not in bpy.data.texts
    (WORK / 'disable-resume').touch()


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
    server.kill()
    server.communicate()
    (WORK / 'transfer-results.txt').write_text(result)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(drive, first_interval=0.1, persistent=True)
