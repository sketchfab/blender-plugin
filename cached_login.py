import json
import os
from pathlib import Path
import subprocess
import sys
import time


class CachedLogin:
    TIMEOUT = 15

    def __init__(self, addon):
        self.addon = addon
        self.registered = False
        self.job = None
        self.retired = []
        self.token = None
        self.started = 0
        self.wm_pointer = None
        self.was_enabled = False
        # Blender identifies timers by callable identity, including bound methods.
        self.restore_timer = self.restore
        self.poll_timer = self.poll
        self.reap_timer = self.reap

        @addon.bpy.app.handlers.persistent
        def before_load(*args):
            self.was_enabled = addon.is_plugin_enabled
            self.cancel()
            self.reset_account()

        @addon.bpy.app.handlers.persistent
        def after_load(*args):
            addon.is_plugin_enabled = self.was_enabled
            self.schedule_restore()

        @addon.bpy.app.handlers.persistent
        def before_quit(*args):
            self.cancel()

        self.before_load = before_load
        self.after_load = after_load
        self.before_quit = before_quit

    @property
    def pending(self):
        return self.job is not None

    def interpreter(self):
        name = 'python.exe' if os.name == 'nt' else 'python{}.{}'.format(*sys.version_info[:2])
        binary = Path(sys.prefix) / 'bin' / name
        return str(binary) if binary.is_file() else None

    def reset_account(self):
        api = self.addon.SketchfabLoginProps.skfb_api
        api.headers = {}
        api.username = api.display_name = api.plan_type = ''
        api.user_orgs = []
        api.user_has_orgs = False
        api.orgs_check_failed = False
        api.active_org = None
        api.use_org_profile = False

    def schedule_restore(self):
        timers = self.addon.bpy.app.timers
        if self.registered and not timers.is_registered(self.restore_timer):
            timers.register(self.restore_timer, first_interval=0.1)

    def register(self):
        if self.addon.bpy.app.background:
            return
        self.registered = True
        handlers = self.addon.bpy.app.handlers
        handlers.load_pre.append(self.before_load)
        handlers.load_post.append(self.after_load)
        if hasattr(handlers, 'quit_pre'):
            handlers.quit_pre.append(self.before_quit)
        self.schedule_restore()

    def unregister(self):
        self.registered = False
        self.cancel()
        handlers = self.addon.bpy.app.handlers
        for name, callback in (('load_pre', self.before_load), ('load_post', self.after_load),
                               ('quit_pre', self.before_quit)):
            callbacks = getattr(handlers, name, [])
            if callback in callbacks:
                callbacks.remove(callback)
        self.reset_account()

    def redraw(self):
        for window in self.addon.bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

    def restore(self):
        if not self.registered:
            return None
        if self.addon.bpy.context.window_manager is None:
            return 0.1
        try:
            token = self.addon.Cache.get_key('api_token')
        except (OSError, ValueError, TypeError):
            self.addon.set_login_status('ERROR', 'Could not read the cached API token')
            return None
        if token:
            self.start(token)
        return None

    def start(self, token):
        self.cancel()
        if not self.registered or not isinstance(token, str) or not token:
            return False
        binary = self.interpreter()
        if binary is None:
            return False
        args = {'url': self.addon.Config.SKETCHFAB_ME, 'token': token,
                'ca_file': self.addon.requests.certs.where()}
        payload = json.dumps(args).encode('utf-8')
        # A bounded payload fits in an empty pipe without waiting for the child.
        if len(payload) > 4096:
            return False
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        try:
            self.job = subprocess.Popen(
                [binary, '-I', str(Path(__file__).with_name('login_worker.py'))],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **flags)
            self.job.stdin.write(payload)
            self.job.stdin.close()
            self.job.stdin = None
        except OSError:
            self.cancel()
            return False
        self.reset_account()
        login = self.addon.get_sketchfab_login_props()
        login.api_token = token
        self.token = token
        self.wm_pointer = self.addon.bpy.context.window_manager.as_pointer()
        self.started = time.monotonic()
        self.addon.is_plugin_enabled = True
        self.addon.set_login_status('FILE_REFRESH', 'Logging in to Sketchfab...')
        self.addon.bpy.app.timers.register(self.poll_timer, first_interval=0.05)
        self.redraw()
        return True

    def cancel(self):
        timers = self.addon.bpy.app.timers
        for callback in (self.restore_timer, self.poll_timer):
            if timers.is_registered(callback):
                timers.unregister(callback)
        if self.job is not None:
            if self.job.poll() is None:
                try:
                    self.job.kill()
                except OSError:
                    pass
            self.retired.append(self.job)
            self.job = None
            if not timers.is_registered(self.reap_timer):
                timers.register(self.reap_timer, first_interval=0.05, persistent=True)
        self.token = None

    def reap(self):
        for child in self.retired[:]:
            if child.poll() is not None:
                child.communicate()
                self.retired.remove(child)
        return 0.05 if self.retired else None

    def poll(self):
        if not self.registered or self.job is None:
            return None
        wm = self.addon.bpy.context.window_manager
        if wm is None:
            self.cancel()
            return None
        login = self.addon.get_sketchfab_login_props()
        if wm.as_pointer() != self.wm_pointer or login.api_token != self.token:
            self.cancel()
            self.addon.set_login_status('INFO', 'Login cancelled; please try again')
            self.redraw()
            return None
        if self.job.poll() is None:
            if time.monotonic() - self.started < self.TIMEOUT:
                return 0.05
            self.cancel()
            self.addon.set_login_status('ERROR', 'Sketchfab login timed out; please try again')
            self.redraw()
            return None
        output, _ = self.job.communicate()
        returncode = self.job.returncode
        self.job = None
        try:
            if returncode or len(output) > 4096:
                raise ValueError('Invalid worker output')
            result = json.loads(output)
            if not isinstance(result, dict):
                raise ValueError('Invalid worker output')
            if 'error' not in result:
                for field in ('username', 'displayName', 'account'):
                    if not isinstance(result[field], str) or len(result[field]) > 256:
                        raise ValueError('Invalid account data')
                if not result['username'] or not result['account']:
                    raise ValueError('Missing account data')
                for field in ('user_has_orgs', 'orgs_check_failed'):
                    if not isinstance(result[field], bool):
                        raise ValueError('Invalid organization data')
        except (ValueError, KeyError, TypeError):
            result = {'error': 'unavailable'}
        api = login.skfb_api
        if 'error' not in result:
            api.username = result['username']
            api.display_name = result['displayName']
            api.plan_type = result['account']
            api.user_has_orgs = result['user_has_orgs']
            api.orgs_check_failed = result['orgs_check_failed']
            api.build_headers()
            if api.orgs_check_failed:
                self.addon.set_login_status('ERROR', 'Logged in; could not check organizations. Log in again to retry.')
            else:
                self.addon.set_login_status('INFO', '')
        elif result['error'] == 'rejected':
            try:
                if self.addon.Cache.get_key('api_token') == self.token:
                    self.addon.Cache.delete_key('api_token')
            except (OSError, ValueError, TypeError):
                self.addon.set_login_status('ERROR', 'Invalid API token; could not clear the cached token')
            else:
                self.addon.set_login_status('ERROR', 'Invalid API token')
        else:
            self.addon.set_login_status('ERROR', 'Could not verify your Sketchfab account; please try again')
        self.token = None
        self.redraw()
        return None
