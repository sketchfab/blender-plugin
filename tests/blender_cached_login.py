import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import bpy


ROOT = Path(__file__).resolve().parents[1]
WORK = Path(os.environ['SKFB_TEST_ROOT']).resolve()
scripts = Path(bpy.utils.user_resource('SCRIPTS')).resolve()
assert WORK in scripts.parents, 'Use an isolated BLENDER_USER_SCRIPTS directory'
spec = importlib.util.spec_from_file_location('sketchfab_test', ROOT / '__init__.py')
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.Cache.SKETCHFAB_CACHE_FILE = str(WORK / 'synthetic.cache')
addon.get_temporary_path = lambda: str(WORK / 'downloads')
addon.register()
old_cache = Path(addon.Config.SKETCHFAB_THUMB_DIR)
sentinel = old_cache / 'unmanaged.txt'
sentinel.write_text('keep this file')
addon.get_temporary_path = lambda: str(WORK / 'downloads-other')
addon.updateCacheDirectory(None, bpy.context)
assert sentinel.read_text() == 'keep this file'
assert old_cache != Path(addon.Config.SKETCHFAB_THUMB_DIR)
login = addon.cached_login
checks = []

if bpy.app.background:
    assert not login.registered and not login.pending
    assert not bpy.app.timers.is_registered(login.restore_timer)
    addon.unregister()
    print('BACKGROUND_LOGIN_SUPPRESSED')
    raise SystemExit(0)

server = subprocess.Popen([login.interpreter(), '-I', str(ROOT / 'tests/fake_login_api.py')],
                          stdout=subprocess.PIPE, text=True)
port = int(server.stdout.readline())
heartbeats = 0
initial_threads = len(threading.enumerate())
unexpected_calls = 0


def heartbeat():
    global heartbeats
    heartbeats += 1
    return 0.01


def unexpected_network(*args, **kwargs):
    global unexpected_calls
    unexpected_calls += 1
    raise AssertionError('Unexpected in-process startup request')


addon.requests.get = unexpected_network


def reset(token='synthetic-test-token', mode='ok'):
    login.cancel()
    login.reset_account()
    addon.is_plugin_enabled = False
    addon.get_sketchfab_login_props().api_token = ''
    Path(addon.Cache.SKETCHFAB_CACHE_FILE).write_text(json.dumps({'api_token': token}))
    addon.Config.SKETCHFAB_ME = 'http://127.0.0.1:{}/{}'.format(port, mode)
    login.TIMEOUT = 15


def finished():
    return not login.pending and not bpy.app.timers.is_registered(login.restore_timer)


def tests():
    reset('')
    login.schedule_restore()
    yield finished
    assert not addon.is_plugin_enabled and not login.pending
    checks.append('No cached token: manual activation available')

    reset()
    login.schedule_restore()
    yield finished
    api = addon.get_sketchfab_props().skfb_api
    assert addon.is_plugin_enabled and api.is_user_logged()
    assert api.get_user_info() == 'Probe (basic)'
    assert not addon.get_sketchfab_props().search_results
    assert addon.get_sketchfab_props().is_latest_version == -1
    checks.append('Cached account restored without search or update requests')

    with patch.object(addon, 'requests', SimpleNamespace(get=unexpected_network)):
        reset()
        login.schedule_restore()
        yield finished
        assert api.is_user_logged()
        api.login()
        yield finished
        assert api.is_user_logged()
    checks.append('Cached and manual login work without requests.certs')

    reset(mode='restricted')
    login.schedule_restore()
    yield finished
    uid = 'fdecba9876543210fedcba9876543210'
    base = 'http://127.0.0.1:{}'.format(port)
    query = addon.build_search_request('mesh & rig', False, False, False, 'ANY', 'ALL', 'RELEVANCE')

    with patch.object(addon.Config, 'BASE_SEARCH', base + '/search?type=models'), \
            patch.object(addon.Config, 'SKETCHFAB_MODEL', base + '/models'):
        api.search(query)
        request = api.search_request
        assert request.process.pid != os.getpid()
        yield lambda: api.search_request is None
        assert api.next_results_url == base + '/search?page=2'
        yield lambda: addon.thumbnail_request is None and uid in addon.get_sketchfab_props().custom_icons
        assert bpy.ops.wm.sketchfab_search_next() == {'FINISHED'}
        yield lambda: api.search_request is None
        assert api.prev_results_url is None
        result = addon.get_sketchfab_model(uid)
        assert result.download_size is None
        api.request_model_info(result)
        yield lambda: not addon.background_requests
        assert result.license == 'Fixture license' and result.animated == 'Yes (1 animation(s))'
    checks.append('Restricted search, pagination, and model metadata use background requests')

    addon.check_plugin_version([{'tag_name': '2.0.0', 'prerelease': True}, {'tag_name': '1.7.20'}])
    assert addon.get_sketchfab_props().is_latest_version == 1
    major, minor, _ = addon.bl_info['version']
    addon.check_plugin_version([{'tag_name': f'v{major}.{minor + 1}.0'}])
    assert addon.get_sketchfab_props().is_latest_version == 0
    checks.append('Version check ignores prereleases and compares numeric versions')

    reset(mode='organizations')
    login.schedule_restore()
    yield finished
    assert api.is_user_logged() and api.user_has_orgs and not api.orgs_check_failed
    checks.append('Existing personal-token organization membership check preserved')

    org_uid = '0123456789abcdef0123456789abcdef'
    project_uid = 'fedcba9876543210fedcba9876543210'
    with patch.object(api, 'search'), patch.object(addon.Config, 'SKETCHFAB_ORGS', base + '/fixture-teams'):
        addon.get_sketchfab_props_proxy().use_org_profile = True
        yield lambda: not api.orgs_loading
    assert api.active_org['uid'] == org_uid and api.use_org_profile
    assert addon.get_user_orgs(None, None)[0][0] == org_uid
    assert project_uid in [item[0] for item in addon.get_available_search_domains(None, None)]
    checks.append('Organization toggle still loads organizations and projects with the same token')

    reset(mode='org-failure')
    login.schedule_restore()
    yield finished
    assert api.is_user_logged() and api.orgs_check_failed
    assert addon.get_sketchfab_login_props().status_type == 'ERROR'
    checks.append('Organization lookup failure preserves verified login and reports failure')

    reset(mode='rejected')
    login.schedule_restore()
    yield finished
    assert not addon.Cache.get_key('api_token') and not api.is_user_logged()
    assert addon.is_plugin_enabled and addon.get_sketchfab_login_props().api_token
    checks.append('401 removes cache, leaves token field editable')

    reset(mode='unavailable')
    login.schedule_restore()
    yield finished
    assert addon.Cache.get_key('api_token') and not api.is_user_logged()
    checks.append('Server failure retains cached token and exposes retry')
    addon.Config.SKETCHFAB_ME = 'http://127.0.0.1:{}/ok'.format(port)
    api.login()
    yield finished
    assert api.is_user_logged()
    checks.append('Manual retry uses the asynchronous login path')

    reset(mode='slow')
    login.TIMEOUT = 0.4
    login.schedule_restore()
    count = heartbeats
    yield finished
    assert heartbeats - count >= 20 and not api.is_user_logged()
    assert addon.Cache.get_key('api_token')
    checks.append('Wall-clock deadline cancels slow child while Blender remains responsive')

    reset(mode='slow')
    login.schedule_restore()
    yield lambda: login.pending
    child = login.job
    addon.get_sketchfab_login_props().api_token = 'replacement-test-token'
    yield finished
    assert not api.is_user_logged()
    yield lambda: child.poll() is not None
    checks.append('Token changes discard pending login')

    reset(mode='slow')
    login.schedule_restore()
    yield lambda: login.pending
    child = login.job
    addon.Config.SKETCHFAB_ME = 'http://127.0.0.1:{}/ok'.format(port)
    api.login()
    yield finished
    assert child.poll() is not None and api.is_user_logged()
    checks.append('Manual login with the same token cancels the previous attempt')

    reset(mode='slow')
    login.schedule_restore()
    yield lambda: login.pending
    child = login.job
    old_search = api.search
    api.search = lambda *args, **kwargs: None
    api.logout()
    api.search = old_search
    yield lambda: child.poll() is not None
    assert not api.is_user_logged() and not addon.Cache.get_key('api_token')
    checks.append('Logout cancels pending login and removes credentials')

    reset()
    binary = login.interpreter
    login.interpreter = lambda: None
    login.schedule_restore()
    yield finished
    assert not addon.is_plugin_enabled
    addon.get_sketchfab_login_props().api_token = 'synthetic-test-token'
    api.login()
    yield lambda: not addon.background_requests
    login.interpreter = binary
    assert api.is_user_logged()
    checks.append('Manual login uses a background Blender process when standalone Python is unavailable')

    reset()
    original_popen = subprocess.Popen
    def denied_spawn(*args, **kwargs):
        raise OSError('Synthetic launch failure')
    subprocess.Popen = denied_spawn
    login.schedule_restore()
    yield finished
    subprocess.Popen = original_popen
    assert not addon.is_plugin_enabled
    checks.append('Failed child launch retains manual activation')

    reset()
    Path(addon.Cache.SKETCHFAB_CACHE_FILE).write_text('broken json')
    login.schedule_restore()
    yield finished
    assert not addon.is_plugin_enabled
    addon.get_sketchfab_login_props().api_token = 'synthetic-test-token'
    api.login()
    yield finished
    assert api.is_user_logged()
    checks.append('Manual login repairs a corrupt cache')

    reset()
    addon.get_sketchfab_login_props().api_token = 'synthetic-test-token'
    bpy.ops.wm.save_as_mainfile(filepath=str(WORK / 'synthetic.blend'))
    login.schedule_restore()
    yield finished
    assert api.is_user_logged()
    bpy.ops.wm.open_mainfile(filepath=str(WORK / 'synthetic.blend'))
    yield finished
    assert api.is_user_logged() and addon.get_sketchfab_login_props().api_token
    checks.append('Opening a file restores authentication into new WindowManager state')

    reset(mode='slow')
    login.schedule_restore()
    yield lambda: login.pending
    child = login.job
    addon.Config.SKETCHFAB_ME = 'http://127.0.0.1:{}/ok'.format(port)
    bpy.ops.wm.open_mainfile(filepath=str(WORK / 'synthetic.blend'))
    yield finished
    assert child.poll() is not None and api.is_user_logged()
    checks.append('Opening a file during login cancels the old child and starts fresh')

    reset(mode='slow')
    login.schedule_restore()
    yield lambda: login.pending
    child = login.job
    login.unregister()
    yield lambda: child.poll() is not None
    assert finished() and not api.is_user_logged()
    assert login.before_load not in bpy.app.handlers.load_pre
    assert login.after_load not in bpy.app.handlers.load_post
    checks.append('Disable removes lifecycle callbacks and kills pending children')

    login.register()
    reset()
    login.schedule_restore()
    yield finished
    assert api.is_user_logged()
    checks.append('Re-enable restores cached authentication')
    yield lambda: not login.retired
    assert len(threading.enumerate()) == initial_threads
    assert unexpected_calls == 0, 'An RNA callback attempted synchronous network I/O'
    checks.append('Login creates no Python threads in Blender')


steps = tests()
waiting = None
deadline = time.monotonic() + 45


def drive():
    global waiting
    try:
        assert time.monotonic() < deadline, 'Integration suite timed out'
        if waiting is not None and not waiting():
            return 0.01
        waiting = next(steps)
        return 0.01
    except StopIteration:
        passed = True
    except Exception:
        import traceback
        traceback.print_exc()
        passed = False
    login.cancel()
    server.kill()
    server.communicate()
    addon.unregister()
    result = {'blender': list(bpy.app.version), 'passed': passed, 'checks': checks}
    (WORK / 'results.json').write_text(json.dumps(result, indent=2))
    print('LOGIN_TEST_RESULT', json.dumps(result))
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(heartbeat, persistent=True)
bpy.app.timers.register(drive, persistent=True)
