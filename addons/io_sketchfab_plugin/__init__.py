"""
Copyright 2019 Sketchfab

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import os
import urllib
import requests
import threading
import time
from collections import OrderedDict
import subprocess
import tempfile
import json

import bpy
import bpy.utils.previews
from bpy.props import (StringProperty,
                       EnumProperty,
                       BoolProperty,
                       IntProperty,
                       PointerProperty)

from .io import *
from .sketchfab import Config, Utils, Cache
from .blender.imp.gltf2_blender_gltf import *

from .blender.blender_version import Version


# Blender 2.79 has been shipped with openssl version 0.9.8 which uses a TLS protocol
# that is now blocked for security reasons on websites (github.com for example)
# In order to allow communication with github.com and other websites, the code will intend
# to use the updated openssl version distributed with the addon.
# Note: Blender 2.8 will have a more recent openssl version. This fix is only for 2.79 and olders
if bpy.app.version < (2, 80, 0) and not bpy.app.build_platform == b'Windows':
    try:
        sslib_path = None
        if bpy.app.build_platform == b'Darwin':
            sslib_path = os.path.join(os.path.dirname(__file__), 'dependencies/_ssl.cpython-35m-darwin.so')
        elif bpy.app.build_platform == b'Linux':
            sslib_path = os.path.join(os.path.dirname(__file__), '/io_sketchfab_plugin/_ssl.cpython-35m-x86_64-linux-gnu.so')

        import importlib.util
        spec = importlib.util.spec_from_file_location("_ssl", sslib_path)
        new_ssl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(new_ssl)


        from importlib import reload
        import ssl
        reload(ssl)
        from requests.packages.urllib3.util import ssl_
        reload(ssl_)
        print('SSL python module has been successfully overriden by Sketchfab addon')
        print('It might fix other addons having the same refused TLS protocol issue')
    except Exception as e:
        print(e)
        print("Failed to override SSL lib. The plugin will not be able to check for updates")


bl_info = {
    'name': 'Sketchfab Plugin',
    'description': 'Browse and download free Sketchfab downloadable models',
    'author': 'Sketchfab',
    'license': 'GPL',
    'deps': '',
    'version': (1, 3, 1),
    "blender": (2, 80, 0),
    'location': 'View3D > Tools > Sketchfab',
    'warning': '',
    'wiki_url': 'https://github.com/sketchfab/blender-plugin/releases',
    'tracker_url': 'https://github.com/sketchfab/blender-plugin/issues',
    'link': 'https://github.com/sketchfab/blender-plugin',
    'support': 'COMMUNITY',
    'category': 'Import-Export'
}
bl_info['blender'] = getattr(bpy.app, "version")


PLUGIN_VERSION = str(bl_info['version']).strip('() ').replace(',', '.')
preview_collection = {}
is_plugin_enabled = False

# helpers
def get_sketchfab_login_props():
    return bpy.context.window_manager.sketchfab_api


def get_sketchfab_props():
    return bpy.context.window_manager.sketchfab_browser


def get_sketchfab_props_proxy():
    return bpy.context.window_manager.sketchfab_browser_proxy

def get_sketchfab_model(uid):
    skfb = get_sketchfab_props()
    return skfb.search_results['current'][uid]

def run_default_search():
    searchthr = GetRequestThread(Config.DEFAULT_SEARCH, parse_results)
    searchthr.start()


def get_plugin_enabled():
    props = get_sketchfab_props()
    return props.is_plugin_enabled


def refresh_search(self, context):
    pprops = get_sketchfab_props_proxy()
    if pprops.is_refreshing:
        return

    props = get_sketchfab_props()

    if pprops.search_domain != props.search_domain:
        props.search_domain = pprops.search_domain

    if 'current' in props.search_results:
        del props.search_results['current']

    props.query = pprops.query
    props.animated = pprops.animated
    props.pbr = pprops.pbr
    props.staffpick = pprops.staffpick
    props.categories = pprops.categories
    props.face_count = pprops.face_count
    props.sort_by = pprops.sort_by
    bpy.ops.wm.sketchfab_search('EXEC_DEFAULT')


def set_login_status(status_type, status):
    login_props = get_sketchfab_login_props()
    login_props.status = status
    login_props.status_type = status_type


def set_import_status(status):
    props = get_sketchfab_props()
    props.import_status = status


class SketchfabApi:
    def __init__(self):
        self.access_token = ''
        self.headers = {}
        self.username = ''
        self.display_name = ''
        self.plan_type = ''
        self.next_results_url = None
        self.prev_results_url = None

    def build_headers(self):
        self.headers = {'Authorization': 'Bearer ' + self.access_token}

    def login(self, email, password):
        bpy.ops.wm.login_modal('INVOKE_DEFAULT')

    def is_user_logged(self):
        if self.access_token and self.headers:
            return True

        return False

    def is_user_pro(self):
        return len(self.plan_type) and self.plan_type not in ['basic', 'plus']

    def logout(self):
        self.access_token = ''
        self.headers = {}
        Cache.delete_key('username')
        Cache.delete_key('access_token')
        Cache.delete_key('key')

        props = get_sketchfab_props()
        props.search_domain = "DEFAULT"
        if 'current' in props.search_results:
            del props.search_results['current']
        pprops = get_sketchfab_props_proxy()
        pprops.search_domain = "DEFAULT"

        bpy.ops.wm.sketchfab_search('EXEC_DEFAULT')

    def request_user_info(self):
        requests.get(Config.SKETCHFAB_ME, headers=self.headers, hooks={'response': self.parse_user_info})

    def get_user_info(self):
        if self.display_name and self.plan_type:
            return '{} ({})'.format(self.display_name, self.plan_type)
        else:
            return ('', '')

    def parse_user_info(self, r, *args, **kargs):
        if r.status_code == 200:
            user_data = r.json()
            self.username = user_data['username']
            self.display_name = user_data['displayName']
            self.plan_type = user_data['account']
        else:
            print('Invalid access token')
            self.access_token = ''
            self.headers = {}

    def parse_login(self, r, *args, **kwargs):
        if r.status_code == 200 and 'access_token' in r.json():
            self.access_token = r.json()['access_token']
            self.build_headers()
            self.request_user_info()
        else:
            if 'error_description' in r.json():
                print("Failed to login: {}".format(r.json()['error_description']))
            else:
                print('Login failed.\n {}'.format(r.json()))

    def request_thumbnail(self, thumbnails_json):
        url = Utils.get_thumbnail_url(thumbnails_json)
        thread = ThumbnailCollector(url)
        thread.start()

    def request_model_info(self, uid):
        url = Config.SKETCHFAB_MODEL + '/' + uid
        model_infothr = GetRequestThread(url, self.handle_model_info)
        model_infothr.start()

    def handle_model_info(self, r, *args, **kwargs):
        skfb = get_sketchfab_props()
        uid = Utils.get_uid_from_model_url(r.url)

        # Dirty fix to avoid processing obsolete result data
        if 'current' not in skfb.search_results or uid not in skfb.search_results['current']:
            return

        model = skfb.search_results['current'][uid]
        json_data = r.json()
        model.license = json_data.get('license', {}).get('fullName', 'Personal (you own this model)')
        anim_count = int(json_data['animationCount'])
        model.animated = 'Yes ({} animation(s))'.format(anim_count) if anim_count > 0 else 'No'
        skfb.search_results['current'][uid] = model

    def search(self, query, search_cb):
        skfb = get_sketchfab_props()
        url = Config.BASE_SEARCH
        if skfb.search_domain == "OWN":
            url = Config.BASE_SEARCH_OWN_MODELS
        elif skfb.search_domain == "STORE":
            url = Config.PURCHASED_MODELS

        search_query = '{}{}'.format(url, query)

        searchthr = GetRequestThread(search_query, search_cb, self.headers)
        searchthr.start()

    def search_cursor(self, url, search_cb):
        requests.get(url, headers=self.headers, hooks={'response': search_cb})

    def download_model(self, uid):
        skfb_model = get_sketchfab_model(uid)
        if skfb_model.download_url:
            # Check url sanity
            if time.time() - skfb_model.time_url_requested < skfb_model.url_expires:
                self.get_archive(skfb_model.download_url)
            else:
                print("Download url is outdated, requesting a new one")
                skfb_model.download_url = None
                skfb_model.url_expires = None
                skfb_model.time_url_requested = None
                requests.get(Utils.build_download_url(uid), headers=self.headers, hooks={'response': self.handle_download})
        else:
            requests.get(Utils.build_download_url(uid), headers=self.headers, hooks={'response': self.handle_download})

    def handle_download(self, r, *args, **kwargs):
        if r.status_code != 200 or 'gltf' not in r.json():
            print('Download not available for this model')
            return

        skfb = get_sketchfab_props()
        uid = Utils.get_uid_from_model_url(r.url)

        gltf = r.json()['gltf']
        skfb_model = get_sketchfab_model(uid)
        skfb_model.download_url = gltf['url']
        skfb_model.time_url_requested = time.time()
        skfb_model.url_expires = gltf['expires']

        self.get_archive(gltf['url'])

    def get_archive(self, url):
        if url is None:
            print('Url is None')
            return

        r = requests.get(url, stream=True)
        uid = Utils.get_uid_from_download_url(url)
        temp_dir = os.path.join(Config.SKETCHFAB_MODEL_DIR, uid)
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)

        archive_path = os.path.join(temp_dir, '{}.zip'.format(uid))
        if not os.path.exists(archive_path):
            wm = bpy.context.window_manager
            wm.progress_begin(0, 100)
            set_log("Downloading model..")
            with open(archive_path, "wb") as f:
                total_length = r.headers.get('content-length')
                if total_length is None:  # no content length header
                    f.write(r.content)
                else:
                    dl = 0
                    total_length = int(total_length)
                    for data in r.iter_content(chunk_size=4096):
                        dl += len(data)
                        f.write(data)
                        done = int(100 * dl / total_length)
                        wm.progress_update(done)
                        set_log("Downloading model..{}%".format(done))

            wm.progress_end()
        else:
            print('Model already downloaded')

        gltf_path, gltf_zip = unzip_archive(archive_path)
        if gltf_path:
            try:
                import_model(gltf_path, uid)
            except Exception as e:
                import traceback
                print(traceback.format_exc())
        else:
            print("Failed to download model (url might be invalid)")
            model = get_sketchfab_model(uid)

            import_status = "Import model"
            if model.download_size:
                import_status += " ({})".format(model.download_size)
            set_import_status(import_status)


class SketchfabLoginProps(bpy.types.PropertyGroup):
    def update_tr(self, context):
        self.status = ''
        if self.email != self.last_username or self.password != self.last_password:
            self.last_username = self.email
            self.last_password = self.password
            if not self.password:
                set_login_status('ERROR', 'Password is empty')
            bpy.ops.wm.sketchfab_login('EXEC_DEFAULT')


    vars()["email"] = StringProperty(
        name="email",
        description="User email",
        default=""
    )


    vars()["password"] = StringProperty(
        name="password",
        description="User password",
        subtype='PASSWORD',
        default="",
        update=update_tr
    )

    vars()["access_token"] = StringProperty(
            name="access_token",
            description="oauth access token",
            subtype='PASSWORD',
            default=""
            )

    vars()["status"] = StringProperty(name='', default='')
    vars()["status_type"] = EnumProperty(
            name="Login status type",
            items=(('ERROR', "Error", ""),
                       ('INFO', "Information", ""),
                       ('FILE_REFRESH', "Progress", "")),
            description="Determines which icon to use",
            default='FILE_REFRESH'
            )

    vars()["last_username"] = StringProperty(default="default")
    vars()["last_password"] = StringProperty(default="default")

    skfb_api = SketchfabApi()


class SketchfabBrowserPropsProxy(bpy.types.PropertyGroup):
    # Search
    vars()["query"] = StringProperty(
            name="",
            update=refresh_search,
            description="Query to search",
            default="",
            options={'SKIP_SAVE'}
            )

    vars()["pbr"] = BoolProperty(
            name="PBR",
            description="Search for PBR model only",
            default=False,
            update=refresh_search,
            )

    vars()["categories"] = EnumProperty(
            name="Categories",
            items=Config.SKETCHFAB_CATEGORIES,
            description="Show only models of category",
            default='ALL',
            update=refresh_search
            )
    vars()["face_count"] = EnumProperty(
            name="Face Count",
            items=Config.SKETCHFAB_FACECOUNT,
            description="Determines which meshes are exported",
            default='ANY',
            update=refresh_search
            )

    vars()["sort_by"] = EnumProperty(
            name="Sort by",
            items=Config.SKETCHFAB_SORT_BY,
            description="Sort ",
            default='LIKES',
            update=refresh_search
            )

    vars()["animated"] = BoolProperty(
            name="Animated",
            description="Show only models with animation",
            default=False,
            update=refresh_search
            )

    vars()["staffpick"] = BoolProperty(
            name="Staffpick",
            description="Show only staffpick models",
            default=False,
            update=refresh_search
            )

    vars()["search_domain"] = EnumProperty(
            name="",
            items=Config.SKETCHFAB_SEARCH_DOMAIN,
            description="Search domain ",
            default='DEFAULT',
            update=refresh_search
            )

    vars()["is_refreshing"] = BoolProperty(
        name="Refresh",
        description="Refresh",
        default=False,
    )
    vars()["expanded_filters"] = bpy.props.BoolProperty(default=False)

class SketchfabBrowserProps(bpy.types.PropertyGroup):
    # Search
    vars()["query"] = StringProperty(
            name="Search",
            description="Query to search",
            default=""
            )

    vars()["pbr"] = BoolProperty(
            name="PBR",
            description="Search for PBR model only",
            default=False
            )

    vars()["categories"] = EnumProperty(
            name="Categories",
            items=Config.SKETCHFAB_CATEGORIES,
            description="Show only models of category",
            default='ALL',
             )

    vars()["face_count"] = EnumProperty(
            name="Face Count",
            items=Config.SKETCHFAB_FACECOUNT,
            description="Determines which meshes are exported",
            default='ANY',
            )

    vars()["sort_by"] = EnumProperty(
            name="Sort by",
            items=Config.SKETCHFAB_SORT_BY,
            description="Sort ",
            default='LIKES',
            )

    vars()["animated"] = BoolProperty(
            name="Animated",
            description="Show only models with animation",
            default=False,
            )

    vars()["staffpick"] = BoolProperty(
            name="Staffpick",
            description="Show only staffpick models",
            default=False,
            )

    vars()["search_domain"] = EnumProperty(
            name="Search domain",
            items=Config.SKETCHFAB_SEARCH_DOMAIN,
            description="Search domain ",
            default='DEFAULT',
            update=refresh_search
            )

    vars()["status"] = StringProperty(name='status', default='idle')

    vars()["use_preview"] = BoolProperty(
        name="Use Preview",
        description="Show results using preview widget instead of regular buttons with thumbnails as icons",
        default=True
        )

    search_results = {}
    vars()["current_key"] = StringProperty(name='current', default='current')
    vars()["has_searched_next"] = BoolProperty(name='next', default=False)
    vars()["has_searched_prev"] = BoolProperty(name='prev', default=False)

    skfb_api = SketchfabLoginProps.skfb_api
    custom_icons = bpy.utils.previews.new()
    vars()["has_loaded_thumbnails"] = BoolProperty(default=False)

    vars()["is_latest_version"] = IntProperty(default=-1)

    vars()["import_status"] = StringProperty(name='import', default='')
    vars()["is_plugin_enabled"] = BoolProperty(default=False)


def list_current_results(self, context):
    skfb = get_sketchfab_props()

    # No results:
    if 'current' not in skfb.search_results:
        return preview_collection['default']

    if skfb.has_loaded_thumbnails and 'thumbnails' in preview_collection:
        return preview_collection['thumbnails']

    res = []
    missing_thumbnail = False
    if 'current' in skfb.search_results and len(skfb.search_results['current']):
        skfb_results = skfb.search_results['current']
        for i, result in enumerate(skfb_results):
            if result in skfb_results:
                model = skfb_results[result]
                if model.uid in skfb.custom_icons:
                    res.append((model.uid, model.title, "", skfb.custom_icons[model.uid].icon_id, i))
                else:
                    res.append((model.uid, model.title, "", preview_collection['skfb']['0'].icon_id, i))
                    missing_thumbnail = True
            else:
                print('Result issue')

    # Default element to avoid having an empty preview collection
    if not res:
        res.append(('NORESULTS', 'empty', "", preview_collection['skfb']['0'].icon_id, 0))

    preview_collection['thumbnails'] = tuple(res)
    skfb.has_loaded_thumbnails = not missing_thumbnail
    return preview_collection['thumbnails']


def draw_search(layout, context):
    props = get_sketchfab_props_proxy()
    skfb_api = get_sketchfab_props().skfb_api
    col = layout.box().column(align=True)

    ro = col.row()
    ro.label(text="Search")
    domain_col = ro.column()
    domain_col.scale_x = 1.5
    domain_col.enabled = skfb_api.is_user_logged();
    domain_col.prop(props, "search_domain")

    ro = col.row()
    ro.scale_y = 1.25
    ro.prop(props, "query")
    ro.operator("wm.sketchfab_search", text="", icon='VIEWZOOM')

    # User selected own models but is not pro
    if props.search_domain == "OWN" and skfb_api.is_user_logged() and not skfb_api.is_user_pro():
        col.label(text='A PRO account is required', icon='QUESTION')
        col.label(text='to access your personal library')

    # Display a collapsible box for filters
    col = layout.box().column(align=True)
    col.enabled = (props.search_domain != "STORE")
    row = col.row()
    row.prop(props, "expanded_filters", icon="TRIA_DOWN" if props.expanded_filters else "TRIA_RIGHT", icon_only=True, emboss=False)
    row.label(text="Search filters")
    if props.expanded_filters:
        col.separator()
        col.prop(props, "categories")
        col.prop(props, "sort_by")
        col.prop(props, "face_count")
        row = col.row()
        row.prop(props, "pbr")
        row.prop(props, "staffpick")
        row.prop(props, "animated")

    pprops = get_sketchfab_props()


def draw_model_info(layout, model, context):
    ui_model_props = layout.box().column(align=True)

    row = ui_model_props.row()
    row.label(text="{}".format(model.title), icon='OBJECT_DATA')
    row.operator("wm.sketchfab_view", text="", icon='LINKED').model_uid = model.uid
    
    ui_model_props.label(text='{}'.format(model.author), icon='ARMATURE_DATA')

    if model.license:
        ui_model_props.label(text='{}'.format(model.license), icon='TEXT')
    else:
        ui_model_props.label(text='Fetching..')

    if model.vertex_count and model.face_count:
        ui_model_stats = ui_model_props.row()
        ui_model_stats.label(text='Verts: {}  |  Faces: {}'.format(Utils.humanify_number(model.vertex_count), Utils.humanify_number(model.face_count)), icon='MESH_DATA')

    if(model.animated):
        ui_model_props.label(text='Animated: ' + model.animated, icon='ANIM_DATA')

    import_ops = ui_model_props.column()
    skfb = get_sketchfab_props()
    import_ops.enabled = skfb.skfb_api.is_user_logged() and bpy.context.mode == 'OBJECT'
    if not skfb.skfb_api.is_user_logged():
        downloadlabel = 'Log in to download models'
    elif bpy.context.mode != 'OBJECT':
        downloadlabel = "Import is available only in object mode"
    else:
        downloadlabel = "Import model"
        if model.download_size:
            downloadlabel += " ({})".format(model.download_size)

    if skfb.import_status:
        downloadlabel = skfb.import_status

    download_icon = 'IMPORT' if import_ops.enabled else 'INFO'
    import_ops.label(text='')
    row = import_ops.row()
    row.scale_y = 2.0
    row.operator("wm.sketchfab_download", icon=download_icon, text=downloadlabel, translate=False, emboss=True).model_uid = model.uid


def set_log(log):
    get_sketchfab_props().status = log

def unzip_archive(archive_path):
    if os.path.exists(archive_path):
        set_import_status('Unzipping model')
        import zipfile
        try:
            zip_ref = zipfile.ZipFile(archive_path, 'r')
            extract_dir = os.path.dirname(archive_path)
            zip_ref.extractall(extract_dir)
            zip_ref.close()
        except zipfile.BadZipFile:
            print('Error when dezipping file')
            os.remove(archive_path)
            print('Invaild zip. Try again')
            set_import_status('')
            return None, None

        gltf_file = os.path.join(extract_dir, 'scene.gltf')
        return gltf_file, archive_path

    else:
        print('ERROR: archive doesn\'t exist')


def run_async(func):
    from threading import Thread
    from functools import wraps

    @wraps(func)
    def async_func(*args, **kwargs):
        func_hl = Thread(target=func, args=args, kwargs=kwargs)
        func_hl.start()
        return func_hl

    return async_func


def import_model(gltf_path, uid):
    bpy.ops.wm.import_modal('INVOKE_DEFAULT', gltf_path=gltf_path, uid=uid)


def build_search_request(query, pbr, animated, staffpick, face_count, category, sort_by):
    final_query = '&q={}'.format(query)

    if animated:
        final_query = final_query + '&animated=true'

    if staffpick:
        final_query = final_query + '&staffpicked=true'

    if sort_by == 'LIKES':
        final_query = final_query + '&sort_by=-likeCount'
    elif sort_by == 'RECENT':
        final_query = final_query + '&sort_by=-publishedAt'
    elif sort_by == 'VIEWS':
        final_query = final_query + '&sort_by=-viewCount'

    if face_count == '10K':
        final_query = final_query + '&max_face_count=10000'
    elif face_count == '50K':
        final_query = final_query + '&min_face_count=10000&max_face_count=50000'
    elif face_count == '100K':
        final_query = final_query + '&min_face_count=50000&max_face_count=100000'
    elif face_count == '250K':
        final_query = final_query + "&min_face_count=100000&max_face_count=250000"
    elif face_count == '250KP':
        final_query = final_query + "&min_face_count=250000"

    if category != 'ALL':
        final_query = final_query + '&categories={}'.format(category)

    if pbr:
        final_query = final_query + '&pbr_type=metalness'

    return final_query


def parse_results(r, *args, **kwargs):
    skfb = get_sketchfab_props()
    json_data = r.json()

    if 'current' in skfb.search_results:
        skfb.search_results['current'].clear()
        del skfb.search_results['current']

    skfb.search_results['current'] = OrderedDict()

    for result in list(json_data['results']):

        # Dirty fix to avoid parsing obsolete data
        if 'current' not in skfb.search_results:
            return

        uid = result['uid']
        skfb.search_results['current'][result['uid']] = SketchfabModel(result)

        if not os.path.exists(os.path.join(Config.SKETCHFAB_THUMB_DIR, uid) + '.jpeg'):
            skfb.skfb_api.request_thumbnail(result['thumbnails'])
        elif uid not in skfb.custom_icons:
            skfb.custom_icons.load(uid, os.path.join(Config.SKETCHFAB_THUMB_DIR, "{}.jpeg".format(uid)), 'IMAGE')

    if json_data['next']:
        skfb.skfb_api.next_results_url = json_data['next']
    else:
        skfb.skfb_api.next_results_url = None

    if json_data['previous']:
        skfb.skfb_api.prev_results_url = json_data['previous']
    else:
        skfb.skfb_api.prev_results_url = None


class ThumbnailCollector(threading.Thread):
    def __init__(self, url):
        self.url = url
        threading.Thread.__init__(self)

    def set_url(self, url):
        self.url = url

    def run(self):
        if not self.url:
            return
        requests.get(self.url, stream=True, hooks={'response': self.handle_thumbnail})

    def handle_thumbnail(self, r, *args, **kwargs):
        uid = r.url.split('/')[4]
        if not os.path.exists(Config.SKETCHFAB_THUMB_DIR):
            try:
                os.makedirs(Config.SKETCHFAB_THUMB_DIR)
            except:
                pass
        thumbnail_path = os.path.join(Config.SKETCHFAB_THUMB_DIR, uid) + '.jpeg'

        with open(thumbnail_path, "wb") as f:
            total_length = r.headers.get('content-length')

            if total_length is None and r.content:
                f.write(r.content)
            else:
                dl = 0
                total_length = int(total_length)
                for data in r.iter_content(chunk_size=4096):
                    dl += len(data)
                    f.write(data)

        props = get_sketchfab_props()
        if uid not in props.custom_icons:
            props.custom_icons.load(uid, os.path.join(Config.SKETCHFAB_THUMB_DIR, "{}.jpeg".format(uid)), 'IMAGE')


class LoginModal(bpy.types.Operator):
    bl_idname = "wm.login_modal"
    bl_label = "Import glTF model into Sketchfab"
    bl_options = {'INTERNAL'}

    is_logging = BoolProperty(default=False)
    error = BoolProperty(default=False)
    error_message = StringProperty(default='')

    def exectue(self, context):
        return {'FINISHED'}

    def handle_login(self, r, *args, **kwargs):
        browser_props = get_sketchfab_props()
        if r.status_code == 200 and 'access_token' in r.json():
            browser_props.skfb_api.access_token = r.json()['access_token']
            login_props = get_sketchfab_login_props()
            Cache.save_key('username', login_props.email)
            Cache.save_key('access_token', browser_props.skfb_api.access_token)

            browser_props.skfb_api.build_headers()
            set_login_status('INFO', '')
            browser_props.skfb_api.request_user_info()

        else:
            if 'error_description' in r.json():
                set_login_status('ERROR', 'Failed to authenticate: bad login/password')
            else:
                set_login_status('ERROR', 'Failed to authenticate: bad login/password')
                print('Cannot login.\n {}'.format(r.json()))

        self.is_logging = False

    def modal(self, context, event):
        if self.error:
            self.error = False
            set_login_status('ERROR', '{}'.format(self.error_message))
            return {"FINISHED"}

        if self.is_logging:
            set_login_status('FILE_REFRESH', 'Loging in to your Sketchfab account...')
            return {'RUNNING_MODAL'}
        else:
            return {'FINISHED'}

    def invoke(self, context, event):
        self.is_logging = True
        try:
            context.window_manager.modal_handler_add(self)
            login_props = get_sketchfab_login_props()
            url = '{}&username={}&password={}'.format(Config.SKETCHFAB_OAUTH, urllib.parse.quote_plus(login_props.email), urllib.parse.quote_plus(login_props.password))
            requests.post(url, hooks={'response': self.handle_login})
        except Exception as e:
            self.error = True
            self.error_message = str(e)

        return {'RUNNING_MODAL'}


class ImportModalOperator(bpy.types.Operator):
    bl_idname = "wm.import_modal"
    bl_label = "Import glTF model into Sketchfab"
    bl_options = {'INTERNAL'}

    gltf_path = StringProperty()
    uid = StringProperty()

    def exectue(self, context):
        print('IMPORT')
        return {'FINISHED'}

    def modal(self, context, event):
        bpy.context.scene.render.engine = Version.ENGINE
        gltf_importer = glTFImporter(self.gltf_path, Log.default())
        success, txt = gltf_importer.read()
        if not success:
            print('Failed to read GLTF')
        try:
            old_objects = [o.name for o in bpy.data.objects] # Get the current objects inorder to find the new node hierarchy
            BlenderGlTF.create(gltf_importer, root_name=Utils.make_model_name(gltf_importer.data))
            set_import_status('')
            Utils.clean_downloaded_model_dir(self.uid)
            Utils.clean_node_hierarchy([o for o in bpy.data.objects if o.name not in old_objects])
            return {'FINISHED'}
        except Exception:
            import traceback
            print(traceback.format_exc())
            set_import_status('')
            return {'FINISHED'}

        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        set_import_status('Importing...')
        return {'RUNNING_MODAL'}


class GetRequestThread(threading.Thread):
    def __init__(self, url, callback, headers={}):
        self.url = url
        self.callback = callback
        self.headers = headers
        threading.Thread.__init__(self)

    def run(self):
        requests.get(self.url, headers=self.headers, hooks={'response': self.callback})


class View3DPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'TOOLS' if bpy.app.version < (2, 80, 0) else 'UI'
    bl_category = 'Sketchfab'
    bl_context = 'objectmode'

class SketchfabPanel(View3DPanel, bpy.types.Panel):
    bl_options = {'DEFAULT_CLOSED'}
    bl_idname = "VIEW3D_PT_sketchfab_about"
    bl_label = "About"

    @classmethod
    def poll(cls, context):
        return (context.scene is not None)

    def draw(self, context):

        skfb = get_sketchfab_props()

        if skfb.is_latest_version == 1:
            self.bl_label = "Sketchfab plugin v{} (up-to-date)".format(PLUGIN_VERSION)
        elif skfb.is_latest_version == 0:
            self.bl_label = "Sketchfab plugin v{} (outdated)".format(PLUGIN_VERSION)
            self.layout.operator('wm.skfb_new_version', text='New version available', icon='ERROR')
        elif skfb.is_latest_version == -2:
            self.bl_label = "Sketchfab plugin v{}".format(PLUGIN_VERSION)

        # External links
        #doc_ui = self.layout.row()
        self.layout.operator('wm.skfb_help', text='Documentation', icon='QUESTION')
        self.layout.operator('wm.skfb_report_issue', text='Report an issue', icon='ERROR')
        self.layout.label(text="Download folder:")
        self.layout.label(text="  " + Config.SKETCHFAB_TEMP_DIR)

class LoginPanel(View3DPanel, bpy.types.Panel):
    bl_idname = "VIEW3D_PT_sketchfab_login"
    bl_label = "Activation / Log in"
    #bl_parent_id = "VIEW3D_PT_sketchfab"

    is_logged = BoolProperty()

    def draw(self, context):
        skfb = get_sketchfab_props()

        if not skfb.is_plugin_enabled:
            self.layout.operator('wm.skfb_enable', text='Activate add-on', icon="LINKED").enable = True
        else:
            # LOGIN
            skfb_login = get_sketchfab_login_props()
            layout = self.layout.box().column(align=True)
            layout.enabled = get_plugin_enabled()
            if skfb_login.skfb_api.is_user_logged():
                login_row = layout.row()
                login_row.label(text='Logged in as {}'.format(skfb_login.skfb_api.get_user_info()))
                login_row.operator('wm.sketchfab_login', text='Logout', icon='DISCLOSURE_TRI_RIGHT').authenticate = False
                if skfb_login.status:
                    layout.prop(skfb_login, 'status', icon=skfb_login.status_type)
            else:
                layout.label(text="Login to your Sketchfab account", icon='INFO')
                layout.prop(skfb_login, "email")
                layout.prop(skfb_login, "password")
                ops_row = layout.row()
                ops_row.operator('wm.sketchfab_signup', text='Create an account', icon='PLUS')
                login_icon = "LINKED" if bpy.app.version < (2,80,0) else "USER"
                ops_row.operator('wm.sketchfab_login', text='Log in', icon=login_icon).authenticate = True
                if skfb_login.status:
                    layout.prop(skfb_login, 'status', icon=skfb_login.status_type)

class SketchfabBrowse(View3DPanel, bpy.types.Panel):
    bl_idname = "VIEW3D_PT_sketchfab_browse"
    bl_label = "Import"

    uid   = ''
    label = "Search results"

    def draw_results(self, layout, context):

        props = get_sketchfab_props()

        col = layout.box().column(align=True)

        #results = layout.column(align=True)
        col.label(text=self.label)

        model = None

        result_pages_ops = col.row()
        if props.skfb_api.prev_results_url:
            result_pages_ops.operator("wm.sketchfab_search_prev", text="Previous page", icon='FRAME_PREV')

        if props.skfb_api.next_results_url:
            result_pages_ops.operator("wm.sketchfab_search_next", text="Next page", icon='FRAME_NEXT')

        #result_label = 'Click below to see more results'
        #col.label(text=result_label, icon='INFO')
        try:
            col.template_icon_view(bpy.context.window_manager, 'result_previews', show_labels=True, scale=5.)
        except Exception:
            print('ResultsPanel: Failed to display results')
            pass

        if 'current' not in props.search_results or not len(props.search_results['current']):
            self.label = 'No results'
            return
        else:
            self.label = "Search results"

        if "current" in props.search_results:

            if bpy.context.window_manager.result_previews not in props.search_results['current']:
                return

            model = props.search_results['current'][bpy.context.window_manager.result_previews]

            if not model:
                return

            if self.uid != model.uid:
                self.uid = model.uid

                if not model.info_requested:
                    props.skfb_api.request_model_info(model.uid)
                    model.info_requested = True

            draw_model_info(col, model, context)

    def draw(self, context):
        self.layout.enabled = get_plugin_enabled()

        draw_search(self.layout, context)

        self.draw_results(self.layout, context)

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=900, height=850)

class SketchfabExportPanel(View3DPanel, bpy.types.Panel):
    #bl_idname = "wm.sketchfab_export" if bpy.app.version == (2, 79, 0) else "VIEW3D_PT_sketchfab_export"
    bl_options = {'DEFAULT_CLOSED'}
    bl_label = "Export"
    bl_idname = "VIEW3D_PT_sketchfab_export"

    def draw(self, context):

        api = get_sketchfab_props().skfb_api
        self.layout.enabled = get_plugin_enabled() and api.is_user_logged()

        wm = context.window_manager
        props = wm.sketchfab_export

        layout = self.layout

        # Selection only
        layout.prop(props, "selection")

        # Model properties
        col = layout.box().column(align=True)
        col.prop(props, "title")
        col.prop(props, "description")
        col.prop(props, "tags")
        col.prop(props, "draft")
        col.prop(props, "private")
        if props.private:
            col.prop(props, "password")

        # Upload button
        row = layout.row()
        row.scale_y = 2.0
        upload_label = "Upload"
        upload_icon  = "EXPORT"
        upload_enabled = api.is_user_logged() and bpy.context.mode == 'OBJECT'
        if not upload_enabled:
            if not api.is_user_logged():
                upload_label = "Log in to upload models"
            elif bpy.context.mode != 'OBJECT':
                upload_label = "Export is only available in object mode"
        if sf_state.uploading:
            upload_label = "Uploading %s" % sf_state.size_label
            upload_icon  = "SORTTIME"
        row.operator("wm.sketchfab_export", icon=upload_icon, text=upload_label)

        model_url = sf_state.model_url
        if model_url:
            layout.operator("wm.url_open", text="View Online Model", icon='URL').url = model_url

def draw_results_icons(results, props, nbcol=4):
    props = get_sketchfab_props()
    current = props.search_results['current']

    dimx = nbcol if current else 0
    dimy = int(24 / nbcol) if current else 0
    if dimx is not 0 and dimy is not 0:
        for r in range(dimy):
            ro = results.row()
            for col in range(dimx):
                col2 = ro.column(align=True)
                index = r * dimx + col
                if index >= len(current.keys()):
                    return

                model = current[list(current.keys())[index]]

                if model.uid in props.custom_icons:
                    col2.operator("wm.sketchfab_modelview", icon_value=props.custom_icons[model.uid].icon_id, text="{}".format(model.title + ' by ' + model.author)).uid = list(current.keys())[index]
                else:
                    col2.operator("wm.sketchfab_modelview", text="{}".format(model.title + ' by ' + model.author)).uid = list(current.keys())[index]
    else:
        results.row()
        results.row().label(text='No results')
        results.row()


class SketchfabLogger(bpy.types.Operator):
    bl_idname = 'wm.sketchfab_login'
    bl_label = 'Sketchfab Login'
    bl_options = {'INTERNAL'}

    authenticate = BoolProperty(default=True)

    def execute(self, context):
        set_login_status('FILE_REFRESH', 'Login to your Sketchfab account...')
        wm = context.window_manager
        if self.authenticate:
            wm.sketchfab_browser.skfb_api.login(wm.sketchfab_api.email, wm.sketchfab_api.password)
        else:
            wm.sketchfab_browser.skfb_api.logout()
            wm.sketchfab_api.password = ''
            wm.sketchfab_api.last_password = "default"
            set_login_status('FILE_REFRESH', '')
        return {'FINISHED'}


class SketchfabModel:
    def __init__(self, json_data):
        self.title = str(json_data['name'])
        self.author = json_data['user']['displayName']
        self.uid = json_data['uid']
        self.vertex_count = json_data['vertexCount']
        self.face_count = json_data['faceCount']

        if 'archives' in json_data and  'gltf' in json_data['archives']:
            if 'size' in json_data['archives']['gltf'] and json_data['archives']['gltf']['size']:
                self.download_size = Utils.humanify_size(json_data['archives']['gltf']['size'])
        else:
            self.download_size = None

        self.thumbnail_url = os.path.join(Config.SKETCHFAB_THUMB_DIR, '{}.jpeg'.format(self.uid))

        # Model info request
        self.info_requested = False
        self.license = None
        self.animated = False

        # Download url data
        self.download_url = None
        self.time_url_requested = None
        self.url_expires = None


class SketchfabDownloadModel(bpy.types.Operator):
    bl_idname = "wm.sketchfab_download"
    bl_label = "Downloading"
    bl_options = {'INTERNAL'}

    model_uid = bpy.props.StringProperty(name="uid")

    def execute(self, context):
        skfb_api = context.window_manager.sketchfab_browser.skfb_api
        skfb_api.download_model(self.model_uid)
        return {'FINISHED'}


class ViewOnSketchfab(bpy.types.Operator):
    bl_idname = "wm.sketchfab_view"
    bl_label = "View the model on Sketchfab"
    bl_options = {'INTERNAL'}

    model_uid = bpy.props.StringProperty(name="uid")

    def execute(self, context):
        import webbrowser
        webbrowser.open('{}/models/{}'.format(Config.SKETCHFAB_URL, self.model_uid))
        return {'FINISHED'}


def clear_search():
    skfb = get_sketchfab_props()
    skfb.has_loaded_thumbnails = False
    skfb.search_results.clear()
    skfb.custom_icons.clear()
    bpy.data.window_managers['WinMan']['result_previews'] = 0


class SketchfabSearch(bpy.types.Operator):
    bl_idname = "wm.sketchfab_search"
    bl_label = "Search Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        # prepare request for search
        clear_search()
        skfb = get_sketchfab_props()
        skfb.skfb_api.prev_results_url = None
        skfb.skfb_api.next_results_url = None
        final_query = build_search_request(skfb.query, skfb.pbr, skfb.animated, skfb.staffpick, skfb.face_count, skfb.categories, skfb.sort_by)
        skfb.skfb_api.search(final_query, parse_results)
        return {'FINISHED'}


class SketchfabSearchNextResults(bpy.types.Operator):
    bl_idname = "wm.sketchfab_search_next"
    bl_label = "Search Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        # prepare request for search
        clear_search()
        skfb_api = get_sketchfab_props().skfb_api
        skfb_api.search_cursor(skfb_api.next_results_url, parse_results)
        return {'FINISHED'}


class SketchfabSearchPreviousResults(bpy.types.Operator):
    bl_idname = "wm.sketchfab_search_prev"
    bl_label = "Search Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        # prepare request for search
        clear_search()
        skfb_api = get_sketchfab_props().skfb_api
        skfb_api.search_cursor(skfb_api.prev_results_url, parse_results)
        return {'FINISHED'}


class SketchfabOpenModel(bpy.types.Operator):
    bl_idname = "wm.sketchfab_open"
    bl_label = "Downloading"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.label(text="I'm downloading your model!")

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_popup(self, width=550)


class SketchfabCreateAccount(bpy.types.Operator):
    bl_idname = "wm.sketchfab_signup"
    bl_label = "Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        import webbrowser
        webbrowser.open(Config.SKETCHFAB_SIGNUP)
        return {'FINISHED'}


class SketchfabNewVersion(bpy.types.Operator):
    bl_idname = "wm.skfb_new_version"
    bl_label = "Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        import webbrowser
        webbrowser.open('{}/releases/latest'.format(Config.GITHUB_REPOSITORY_URL))
        return {'FINISHED'}


class SketchfabReportIssue(bpy.types.Operator):
    bl_idname = "wm.skfb_report_issue"
    bl_label = "Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        import webbrowser
        webbrowser.open(Config.SKETCHFAB_REPORT_URL)
        return {'FINISHED'}


class SketchfabHelp(bpy.types.Operator):
    bl_idname = "wm.skfb_help"
    bl_label = "Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        import webbrowser
        webbrowser.open('{}/releases/latest'.format(Config.GITHUB_REPOSITORY_URL))
        return {'FINISHED'}


def activate_plugin():
    props = get_sketchfab_props()
    login = get_sketchfab_login_props()

    # Fill login/access_token
    cache_data = Cache.read()
    if 'username' in cache_data:
        login.email = cache_data['username']

    if 'access_token' in cache_data:
        props.skfb_api.access_token = cache_data['access_token']
        props.skfb_api.build_headers()
        props.skfb_api.request_user_info()

    props.is_plugin_enabled = True

    try:
        requests.get(Config.SKETCHFAB_PLUGIN_VERSION, hooks={'response': check_plugin_version})
    except Exception as e:
        print('Error when checking for version: {}'.format(e))

    run_default_search()


class SketchfabEnable(bpy.types.Operator):
    bl_idname = "wm.skfb_enable"
    bl_label = "Sketchfab"
    bl_options = {'INTERNAL'}

    enable = BoolProperty(default=True)
    def execute(self, context):
        if self.enable:
            activate_plugin()

        return {'FINISHED'}


class SketchfabExportProps(bpy.types.PropertyGroup):
    vars()["description"] = StringProperty(
            name="Description",
            description="Description of the model (optional)",
            default="",
            maxlen=1024)
    vars()["filepath"] = StringProperty(
            name="Filepath",
            description="internal use",
            default="",
            )
    vars()["selection"] = BoolProperty(
            name="Selection only",
            description="Determines which meshes are exported",
            default=False,
            )
    vars()["private"] = BoolProperty(
            name="Private",
            description="Upload as private (requires a pro account)",
            default=False,
            )
    vars()["draft"] = BoolProperty(
            name="Draft",
            description="Do not publish the model",
            default=True,
            )
    vars()["password"] = StringProperty(
            name="Password",
            description="Password-protect your model (requires a pro account)",
            default="",
            )
    vars()["tags"] = StringProperty(
            name="Tags",
            description="List of tags (42 max), separated by spaces (optional)",
            default="",
            )
    vars()["title"] = StringProperty(
            name="Title",
            description="Title of the model (determined automatically if left empty)",
            default="",
            maxlen=48
            )


class _SketchfabState:
    """Singleton to store state"""
    __slots__ = (
        "uploading",
        "size_label",
        "model_url",
        "report_message",
        "report_type",
        )

    def __init__(self):
        self.uploading = False
        self.size_label = ""
        self.model_url = ""
        self.report_message = ""
        self.report_type = ''

sf_state = _SketchfabState()
del _SketchfabState

# remove file copy
def terminate(filepath):
    print(filepath)
    os.remove(filepath)
    os.rmdir(os.path.dirname(filepath))

def upload_report(report_message, report_type):
    sf_state.report_message = report_message
    sf_state.report_type = report_type

# upload the blend-file to sketchfab
def upload(filepath, filename):

    wm = bpy.context.window_manager
    props = wm.sketchfab_export

    title = props.title
    if not title:
        title = os.path.splitext(os.path.basename(bpy.data.filepath))[0]

    # Limit the number of tags to 42
    props.tags = " ".join(props.tags.split(" ")[:42])

    _data = {
        "name": title,
        "description": props.description,
        "tags": props.tags,
        "private": props.private,
        "isPublished": not props.draft,
        "password": props.password,
        "source": "blender-exporter",
    }

    _files = {
        "modelFile": open(filepath, 'rb'),
    }

    _headers = get_sketchfab_props().skfb_api.headers

    try:
        r = requests.post(
            Config.SKETCHFAB_MODEL,
            data    = _data,
            files   = _files,
            headers = _headers
        )
    except requests.exceptions.RequestException as e:
        return upload_report("Upload failed. Error: %s" % str(e), 'WARNING')

    result = r.json()
    if r.status_code != requests.codes.created:
        return upload_report("Upload failed. Error: %s" % result["error"], 'WARNING')
    sf_state.model_url = Config.SKETCHFAB_URL + "/models/" + result["uid"]
    return upload_report("Upload complete. Available on your sketchfab.com dashboard.", 'INFO')


class ExportSketchfab(bpy.types.Operator):
    """Upload your model to Sketchfab"""
    bl_idname = "wm.sketchfab_export"
    bl_label = "Upload"

    _timer = None
    _thread = None

    def modal(self, context, event):
        if event.type == 'TIMER':
            if not self._thread.is_alive():
                wm = context.window_manager
                props = wm.sketchfab_export

                terminate(props.filepath)

                if context.area:
                    context.area.tag_redraw()

                # forward message from upload thread
                if not sf_state.report_type:
                    sf_state.report_type = 'ERROR'
                self.report({sf_state.report_type}, sf_state.report_message)

                wm.event_timer_remove(self._timer)
                self._thread.join()
                sf_state.uploading = False
                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def execute(self, context):

        if sf_state.uploading:
            self.report({'WARNING'}, "Please wait till current upload is finished")
            return {'CANCELLED'}

        wm = context.window_manager
        props = wm.sketchfab_export
        sf_state.model_url = ""

        # Prepare to save the file
        binary_path = bpy.app.binary_path
        script_path = os.path.dirname(os.path.realpath(__file__))
        basename, ext = os.path.splitext(bpy.data.filepath)
        if not basename:
            basename = os.path.join(basename, "temp")
        if not ext:
            ext = ".blend"
        tempdir = tempfile.mkdtemp()
        filepath = os.path.join(tempdir, "export-sketchfab" + ext)

        SKETCHFAB_EXPORT_DATA_FILE = os.path.join(tempdir, "export-sketchfab.json")

        try:
            # save a copy of actual scene but don't interfere with the users models
            bpy.ops.wm.save_as_mainfile(filepath=filepath, compress=True, copy=True)

            with open(SKETCHFAB_EXPORT_DATA_FILE, 'w') as s:
                json.dump({
                        "selection": props.selection,
                        }, s)

            subprocess.check_call([
                    binary_path,
                    "--background",
                    "-noaudio",
                    filepath,
                    "--python", os.path.join(script_path, "pack_for_export.py"),
                    "--", tempdir
                    ])

            os.remove(filepath)

            # read subprocess call results
            with open(SKETCHFAB_EXPORT_DATA_FILE, 'r') as s:
                r = json.load(s)
                size = r["size"]
                props.filepath = r["filepath"]
                filename = r["filename"]

            os.remove(SKETCHFAB_EXPORT_DATA_FILE)

        except Exception as e:
            self.report({'WARNING'}, "Error occured while preparing your file: %s" % str(e))
            return {'FINISHED'}

        sf_state.uploading = True
        sf_state.size_label = Utils.humanify_size(size)
        self._thread = threading.Thread(
                target=upload,
                args=(props.filepath, filename),
                )
        self._thread.start()

        wm.modal_handler_add(self)
        self._timer = wm.event_timer_add(1.0, window=context.window)
        
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        self._thread.join()


classes = (
    # Properties
    SketchfabBrowserProps,
    SketchfabLoginProps,
    SketchfabBrowserPropsProxy,
    SketchfabExportProps,

    # Panels
    LoginPanel,
    SketchfabBrowse,
    SketchfabExportPanel,
    SketchfabPanel,

    # Operators
    SketchfabEnable,
    SketchfabCreateAccount,
    LoginModal,
    SketchfabNewVersion,
    SketchfabHelp,
    SketchfabReportIssue,
    SketchfabSearch,
    SketchfabSearchPreviousResults,
    SketchfabSearchNextResults,
    ImportModalOperator,
    ViewOnSketchfab,
    SketchfabDownloadModel,
    SketchfabLogger,
    ExportSketchfab,
    )

def check_plugin_version(request, *args, **kwargs):
    response = request.json()
    skfb = get_sketchfab_props()
    if response and len(response):
        latest_release_version = response[0]['tag_name'].replace('.', '')
        current_version = str(bl_info['version']).replace(',', '').replace('(', '').replace(')', '').replace(' ', '')

        if latest_release_version == current_version:
            print('You are using the latest version({})'.format(response[0]['tag_name']))
            skfb.is_latest_version = 1
        else:
            print('A new version is available: {}'.format(response[0]['tag_name']))
            skfb.is_latest_version = 0
    else:
        print('Failed to retrieve plugin version')
        skfb.is_latest_version = -2

def register():
    sketchfab_icon = bpy.utils.previews.new()
    icons_dir      = os.path.join(os.path.dirname(__file__), "sketchfab", "resources")
    sketchfab_icon.load("skfb", os.path.join(icons_dir, "logo.png"), 'IMAGE')
    sketchfab_icon.load("0",    os.path.join(icons_dir, "placeholder.png"), 'IMAGE')

    res = []
    res.append(('NORESULTS', 'empty', "", sketchfab_icon['0'].icon_id, 0))
    preview_collection['default'] = tuple(res)
    preview_collection['skfb'] = sketchfab_icon
    bpy.types.WindowManager.result_previews = EnumProperty(items=list_current_results)

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.WindowManager.sketchfab_browser = PointerProperty(
                type=SketchfabBrowserProps)

    bpy.types.WindowManager.sketchfab_browser_proxy = PointerProperty(
                type=SketchfabBrowserPropsProxy)

    bpy.types.WindowManager.sketchfab_api = PointerProperty(
                type=SketchfabLoginProps,
                )

    bpy.types.WindowManager.sketchfab_export = PointerProperty(
                type=SketchfabExportProps,
                )

def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)

    del bpy.types.WindowManager.sketchfab_api
    del bpy.types.WindowManager.sketchfab_browser
    del bpy.types.WindowManager.sketchfab_browser_proxy
    del bpy.types.WindowManager.sketchfab_export

    bpy.utils.previews.remove(preview_collection['skfb'])
    del bpy.types.WindowManager.result_previews
    Utils.clean_thumbnail_directory()


if __name__ == "__main__":
    register()
