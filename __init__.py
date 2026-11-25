"""
Copyright 2022 Sketchfab

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

import csv
import math
import os
import requests
import time
from collections import OrderedDict
import tempfile
import json
from pathlib import Path
import sys
from urllib.parse import quote_plus, urljoin, urlsplit

from .cached_login import CachedLogin
from .model_links import MODEL_UID, parse_model_link
from .token_cache import TokenCache
from .worker_process import WorkerJob, reap_workers, retired_workers

import bpy
import bpy.utils.previews
from mathutils import Matrix
from bpy.props import (StringProperty,
                       EnumProperty,
                       BoolProperty,
                       IntProperty,
                       PointerProperty)

bl_info = {
    'name': 'Sketchfab Plugin',
    'description': 'Browse and download free Sketchfab downloadable models',
    'author': 'Sketchfab',
    'license': 'APACHE2',
    'deps': '',
    'version': (1, 9, 0),
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
thumbnail_request = None
background_requests = {}
active_download = None
active_upload = None
uploaded_model_url = ''
is_plugin_enabled = False


class Config:

    GITHUB_REPOSITORY_URL = 'https://github.com/sketchfab/blender-plugin'
    GITHUB_REPOSITORY_API_URL = 'https://api.github.com/repos/sketchfab/blender-plugin'

    SKETCHFAB_URL = 'https://sketchfab.com'
    SKETCHFAB_API = 'https://api.sketchfab.com'
    SKETCHFAB_SEARCH = SKETCHFAB_API + '/v3/search'
    SKETCHFAB_MODEL = SKETCHFAB_API + '/v3/models'
    SKETCHFAB_ORGS = SKETCHFAB_API + '/v3/orgs'
    SKETCHFAB_SIGNUP = 'https://sketchfab.com/signup'

    BASE_SEARCH = SKETCHFAB_SEARCH + '?type=models&downloadable=true'
    DEFAULT_FLAGS = '&staffpicked=true&sort_by=-staffpickedAt'
    DEFAULT_SEARCH = SKETCHFAB_SEARCH + \
                     '?type=models&downloadable=true' + DEFAULT_FLAGS

    SKETCHFAB_ME = '{}/v3/me'.format(SKETCHFAB_API)
    BASE_SEARCH_OWN_MODELS = SKETCHFAB_ME + '/search?type=models&downloadable=true'
    PURCHASED_MODELS = SKETCHFAB_ME + "/models/purchases?type=models"

    SKETCHFAB_PLUGIN_VERSION = '{}/releases'.format(GITHUB_REPOSITORY_API_URL)

    # Those will be set during plugin initialization, or upon setting a new cache directory
    SKETCHFAB_TEMP_DIR = ""
    SKETCHFAB_THUMB_DIR = ""
    SKETCHFAB_MODEL_DIR = ""

    SKETCHFAB_CATEGORIES = (('ALL', 'All categories', 'All categories'),
                            ('animals-pets', 'Animals & Pets', 'Animals and Pets'),
                            ('architecture', 'Architecture', 'Architecture'),
                            ('art-abstract', 'Art & Abstract', 'Art & Abstract'),
                            ('cars-vehicles', 'Cars & vehicles', 'Cars & vehicles'),
                            ('characters-creatures', 'Characters & Creatures', 'Characters & Creatures'),
                            ('cultural-heritage-history', 'Cultural Heritage & History', 'Cultural Heritage & History'),
                            ('electronics-gadgets', 'Electronics & Gadgets', 'Electronics & Gadgets'),
                            ('fashion-style', 'Fashion & Style', 'Fashion & Style'),
                            ('food-drink', 'Food & Drink', 'Food & Drink'),
                            ('furniture-home', 'Furniture & Home', 'Furniture & Home'),
                            ('music', 'Music', 'Music'),
                            ('nature-plants', 'Nature & Plants', 'Nature & Plants'),
                            ('news-politics', 'News & Politics', 'News & Politics'),
                            ('people', 'People', 'People'),
                            ('places-travel', 'Places & Travel', 'Places & Travel'),
                            ('science-technology', 'Science & Technology', 'Science & Technology'),
                            ('sports-fitness', 'Sports & Fitness', 'Sports & Fitness'),
                            ('weapons-military', 'Weapons & Military', 'Weapons & Military'))

    SKETCHFAB_FACECOUNT = (('ANY', "All", ""),
                           ('10K', "Up to 10k", ""),
                           ('50K', "10k to 50k", ""),
                           ('100K', "50k to 100k", ""),
                           ('250K', "100k to 250k", ""),
                           ('250KP', "250k +", ""))

    SKETCHFAB_SORT_BY = (('RELEVANCE', "Relevance", ""),
                         ('LIKES', "Likes", ""),
                         ('VIEWS', "Views", ""),
                         ('RECENT', "Recent", ""))

    SKETCHFAB_SEARCH_DOMAIN = (('DEFAULT', "All site", "", 0),
                               ('OWN', "Own Models (PRO)", "", 1),
                               ('STORE', "Store purchases", "", 2))

    MAX_THUMBNAIL_HEIGHT = 256

    SKETCHFAB_UPLOAD_LIMITS = {
        "basic" : 100 * 1024 * 1024,
        "pro": 200 * 1024 * 1024,
        "prem": 500 * 1024 * 1024,
        "ent": 500 * 1024 * 1024
    }

class Utils:
    def humanify_size(size):
        suffix = 'B'
        readable = size

        # Megabyte
        if size > 1048576:
            suffix = 'MB'
            readable = size / 1048576.0
        # Kilobyte
        elif size > 1024:
            suffix = 'KB'
            readable = size / 1024.0

        readable = round(readable, 2)
        return '{}{}'.format(readable, suffix)

    def humanify_number(number):
        suffix = ''
        readable = number

        if number > 1000000:
            suffix = 'M'
            readable = number / 1000000.0

        elif number > 1000:
            suffix = 'K'
            readable = number / 1000.0

        readable = round(readable, 2)
        return '{}{}'.format(readable, suffix)

    def get_thumbnail_url(thumbnails_json):
        min_height  = 1e6
        min_thumbnail = None
        best_height = 0
        best_thumbnail = None
        if not isinstance(thumbnails_json, dict) or not isinstance(thumbnails_json.get('images'), list):
            return None
        for image in thumbnails_json['images']:
            if not isinstance(image, dict) or not isinstance(image.get('height'), (int, float)) or not image.get('url'):
                continue
            h = image['height']
            if h <= Config.MAX_THUMBNAIL_HEIGHT and h > best_height:
                best_height = h
                best_thumbnail = image['url']
            elif h < min_height:
                min_height = h
                min_thumbnail = image['url']
        # Ensure we have a thumbnail if available thumbnails are all above MAX_THUMBNAIL_HEIGHT
        if best_thumbnail is None and min_thumbnail is not None:
            return min_thumbnail
        return best_thumbnail

    def clean_node_hierarchy(objects, root_name):
        roots = [obj for obj in objects if obj.parent is None]
        if len(roots) != 1:
            return
        root = roots[0]
        while (root.type == 'EMPTY' and len(root.children) == 1
               and root.children[0].type in {'EMPTY', 'MESH'}
               and not (root.animation_data or root.constraints or root.instance_type != 'NONE'
                        or root.keys() or root.users > len(root.users_collection) + 1)):
            child = root.children[0]
            if child.animation_data or child.constraints:
                break
            matrix = child.matrix_world.copy()
            _, rotation, scale = matrix.decompose()
            linear = rotation.to_matrix() @ Matrix.Diagonal(scale)
            # Unparented object transforms cannot represent shear.
            if any(not math.isclose(matrix[row][col], linear[row][col], rel_tol=1e-5, abs_tol=1e-6)
                   for row in range(3) for col in range(3)):
                break
            child.parent = None
            child.matrix_world = matrix
            bpy.data.objects.remove(root, do_unlink=True)
            root = child

        root.name = root_name
        root.select_set(True)

class Cache(TokenCache):
    SKETCHFAB_CACHE_FILE = os.path.join(
        bpy.utils.user_resource('SCRIPTS', path='sketchfab_cache', create=True), '.cache')


# helpers
def get_sketchfab_login_props():
    return bpy.context.window_manager.sketchfab_api


def get_sketchfab_props():
    return bpy.context.window_manager.sketchfab_browser


def get_sketchfab_props_proxy():
    return bpy.context.window_manager.sketchfab_browser_proxy

def get_sketchfab_model(uid):
    skfb = get_sketchfab_props()
    if "current" in skfb.search_results and uid in skfb.search_results["current"]:
        return skfb.search_results['current'][uid]
    else:
        return None

def run_default_search():
    api = get_sketchfab_props().skfb_api
    api.start_search(Config.DEFAULT_SEARCH + api.restricted_search_filter())


def redraw_sketchfab():
    wm = bpy.context.window_manager
    if wm is not None:
        for window in wm.windows:
            if window.screen is not None:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()


def get_plugin_enabled():
    global is_plugin_enabled
    return is_plugin_enabled


def refresh_search(self, context):
    pprops = get_sketchfab_props_proxy()
    if pprops.is_refreshing:
        return

    props = get_sketchfab_props()

    if pprops.search_domain != props.search_domain:
        props.search_domain = pprops.search_domain
    if pprops.sort_by != props.sort_by:
        props.sort_by = pprops.sort_by

    props.query = pprops.query
    props.animated = pprops.animated
    props.pbr = pprops.pbr
    props.staffpick = pprops.staffpick
    props.categories = pprops.categories
    props.face_count = pprops.face_count
    bpy.ops.wm.sketchfab_search('EXEC_DEFAULT')


def set_login_status(status_type, status):
    login_props = get_sketchfab_login_props()
    login_props.status = status
    login_props.status_type = status_type


class SketchfabApi:
    # The API token itself lives in SketchfabLoginProps.api_token, which backs
    # the panel field. Authentication state here is derived: headers is set from
    # that token by build_headers() and cleared on logout or a rejected token.
    def __init__(self):
        self.headers = {}
        self.username = ''
        self.display_name = ''
        self.plan_type = ''
        self.allows_restricted = False
        self.next_results_url = None
        self.prev_results_url = None
        self.search_request = None
        self.search_url = None
        self.search_error = ''
        self.user_orgs = []
        self.orgs_loaded = False
        self.orgs_loading = False
        self.user_has_orgs = False
        self.orgs_check_failed = False
        self.active_org = None
        self.use_org_profile = False

    def build_headers(self):
        api_token = get_sketchfab_login_props().api_token
        if api_token:
            self.headers = {'Authorization': 'Token ' + api_token}
        else:
            print("Empty authorization header")
            self.headers = {}

    def login(self):
        cached_login.cancel()
        cached_login.reset_account()
        token = get_sketchfab_login_props().api_token
        if not token:
            set_login_status('ERROR', 'Enter your API token')
            return
        try:
            Cache.save_key('api_token', token)
        except (OSError, ValueError, TypeError):
            set_login_status('ERROR', 'Could not save the API token')
            return
        if cached_login.start(token):
            return
        set_login_status('FILE_REFRESH', 'Logging in to Sketchfab…')

        def apply(result):
            if get_sketchfab_login_props().api_token == token:
                cached_login.apply_result(result or {'error': 'unavailable'}, token)
            else:
                set_login_status('INFO', 'Login cancelled; please try again')

        queue_request('login', {'url': Config.SKETCHFAB_ME}, {'Authorization': 'Token ' + token}, apply)

    def is_user_logged(self):
        return bool(self.headers)

    def is_user_pro(self):
        return len(self.plan_type) and self.plan_type not in ['basic', 'plus']

    def logout(self):
        cached_login.cancel()
        cached_login.reset_account()
        try:
            Cache.delete_keys('api_token', 'access_token', 'username')
        except OSError:
            set_login_status('ERROR', 'Logged out. Could not remove the cached API token.')

        props = get_sketchfab_props()
        pprops = get_sketchfab_props_proxy()
        props.use_org_profile = False
        pprops.use_org_profile = False

        bpy.ops.wm.sketchfab_search('EXEC_DEFAULT')

    def get_user_info(self):
        if self.display_name and self.plan_type:
            return '{} ({})'.format(self.display_name, self.plan_type)
        else:
            return ('', '')

    def request_user_orgs(self):
        if self.orgs_loaded or self.orgs_loading or not self.is_user_logged():
            return
        self.orgs_loading = True

        def apply(orgs):
            self.orgs_loading = False
            if orgs is None:
                self.orgs_check_failed = True
                return
            self.user_orgs = orgs
            self.orgs_loaded = True
            self.orgs_check_failed = False
            self.user_has_orgs = bool(orgs)
            if get_sketchfab_props_proxy().use_org_profile:
                refresh_orgs(None, bpy.context)

        queue_request('orgs', {'url': Config.SKETCHFAB_ME + '/orgs', 'orgs_url': Config.SKETCHFAB_ORGS},
                      self.headers, apply)

    def request_model_info(self, model):
        model.info_requested = True
        url = f'{Config.SKETCHFAB_MODEL}/{model.uid}'
        if self.use_org_profile and self.active_org:
            url = f'{Config.SKETCHFAB_ORGS}/{self.active_org["uid"]}/models/{model.uid}'

        def apply(data):
            if get_sketchfab_model(model.uid) is not model:
                return
            if not isinstance(data, dict):
                model.license = 'Model details unavailable'
                return
            license = data.get('license')
            model.license = (license.get('fullName') if isinstance(license, dict) else None) or 'License unavailable'
            count = data.get('animationCount')
            if isinstance(count, int):
                model.animated = f'Yes ({count} animation(s))' if count > 0 else 'No'

        queue_request('json', {'url': url}, self.headers, apply)

    def restricted_search_filter(self):
        return '&restricted=1' if self.is_user_logged() and self.allows_restricted else ''

    def reset_search(self):
        stop_worker(self.search_request)
        self.search_request = None
        self.search_url = None
        self.search_error = ''
        self.next_results_url = None
        self.prev_results_url = None
        stop_thumbnails()
        for job in background_requests:
            stop_worker(job)
        background_requests.clear()
        if getattr(bpy.context.window_manager, 'sketchfab_browser', None) is not None:
            clear_search()

    def search(self, query):
        skfb = get_sketchfab_props()
        url = Config.BASE_SEARCH
        if skfb.search_domain == "OWN":
            url = Config.BASE_SEARCH_OWN_MODELS
        elif skfb.search_domain == "STORE":
            url = Config.PURCHASED_MODELS
        elif skfb.search_domain == "ACTIVE_ORG":
            url = Config.SKETCHFAB_ORGS + "/%s/models?isArchivesReady=true" % self.active_org["uid"]
        elif len(skfb.search_domain) == 32:
            url = Config.SKETCHFAB_ORGS + "/%s/models?isArchivesReady=true&projects=%s" % (self.active_org["uid"], skfb.search_domain)

        if skfb.search_domain in {'DEFAULT', 'OWN'}:
            query += self.restricted_search_filter()
        self.start_search(url + query)

    def start_search(self, url):
        self.search_error = ''
        stop_worker(self.search_request)
        self.search_url = url
        self.search_request = start_request('json', {'url': url}, self.headers)
        if self.search_request is None:
            self.search_error = 'Could not start the Sketchfab search. Try again.'
        elif not bpy.app.timers.is_registered(poll_search):
            bpy.app.timers.register(poll_search, first_interval=0.05)
        redraw_sketchfab()

class SketchfabLoginProps(bpy.types.PropertyGroup):
    api_token : StringProperty(
        name="API Token",
        description="User API Token — get it from https://sketchfab.com/settings/password",
        default=""
    )

    status : StringProperty(name='', default='')
    status_type : EnumProperty(
            name="Login status type",
            items=(('ERROR', "Error", ""),
                       ('INFO', "Information", ""),
                       ('FILE_REFRESH', "Progress", "")),
            description="Determines which icon to use",
            default='FILE_REFRESH'
            )

    skfb_api = SketchfabApi()

def get_user_orgs(self, context):
    api  = get_sketchfab_props().skfb_api
    return [(org["uid"], org["displayName"], "") for org in api.user_orgs]

def get_org_projects(self, context):
    api  = get_sketchfab_props().skfb_api
    return [(proj["uid"], proj["name"], proj["name"]) for proj in api.active_org["projects"]] if api.active_org else []

def get_available_search_domains(self, context):
    api = get_sketchfab_props().skfb_api

    search_domains = [domain for domain in Config.SKETCHFAB_SEARCH_DOMAIN]

    if api.user_has_orgs and api.use_org_profile:
        search_domains = [
            ("ACTIVE_ORG", "Active Organization", api.active_org["displayName"], 0)
        ]
        for p in get_org_projects(self, context):
            search_domains.append(p)

    return tuple(search_domains)

def refresh_orgs(self, context):

    pprops = get_sketchfab_props_proxy()
    if pprops.is_refreshing:
        return

    props = get_sketchfab_props()
    api   = props.skfb_api

    if pprops.use_org_profile and not api.orgs_loaded and api.is_user_logged():
        api.request_user_orgs()
        return

    if api.user_orgs:
        api.active_org = next((org for org in api.user_orgs if org["uid"] == pprops.active_org), api.user_orgs[0])
    api.use_org_profile = bool(pprops.use_org_profile and api.active_org)
    props.use_org_profile = api.use_org_profile
    if api.active_org:
        props.active_org = api.active_org['uid']

    domain = "ACTIVE_ORG" if api.use_org_profile else "DEFAULT"
    pprops.is_refreshing = True
    try:
        props.search_domain = domain
        pprops.search_domain = domain
    finally:
        pprops.is_refreshing = False
    refresh_search(self, context)


def get_sorting_options(self, context):
    api = get_sketchfab_props().skfb_api
    if api.user_has_orgs and api.use_org_profile:
        return (
            ('RELEVANCE', "Relevance", ""),
            ('RECENT', "Recent", "")
        )
    else:
        return Config.SKETCHFAB_SORT_BY

class SketchfabBrowserPropsProxy(bpy.types.PropertyGroup):
    # Search
    query : StringProperty(
            name="",
            update=refresh_search,
            description="Query to search",
            default="",
            options={'SKIP_SAVE'}
            )

    pbr : BoolProperty(
            name="PBR",
            description="Search for PBR model only",
            default=False,
            update=refresh_search,
            )

    categories : EnumProperty(
            name="Categories",
            items=Config.SKETCHFAB_CATEGORIES,
            description="Show only models of category",
            default='ALL',
            update=refresh_search
            )
    face_count : EnumProperty(
            name="Face Count",
            items=Config.SKETCHFAB_FACECOUNT,
            description="Determines which meshes are exported",
            default='ANY',
            update=refresh_search
            )

    sort_by : EnumProperty(
            name="Sort by",
            items=get_sorting_options,
            description="Sort ",
            update=refresh_search,
            )

    animated : BoolProperty(
            name="Animated",
            description="Show only models with animation",
            default=False,
            update=refresh_search
            )

    staffpick : BoolProperty(
            name="Staffpick",
            description="Show only staffpick models",
            default=False,
            update=refresh_search
            )

    search_domain : EnumProperty(
            name="",
            items=get_available_search_domains,
            description="Search domain ",
            update=refresh_search,
            default=None
            )

    use_org_profile : BoolProperty(
        name="Use organisation profile",
        description="Download/Upload as a member of an organization.\nSearch queries and uploads will be performed to\nthe organisation and project selected below",
        default=False,
        update=refresh_orgs
    )

    active_org : EnumProperty(
        name="Org",
        items=get_user_orgs,
        description="Active org",
        update=refresh_orgs
    )

    is_refreshing : BoolProperty(
        name="Refresh",
        description="Refresh",
        default=False,
    )
    expanded_filters : bpy.props.BoolProperty(default=False)

class SketchfabBrowserProps(bpy.types.PropertyGroup):
    # Search
    query : StringProperty(
            name="Search",
            description="Query to search",
            default=""
            )

    pbr : BoolProperty(
            name="PBR",
            description="Search for PBR model only",
            default=False
            )

    categories : EnumProperty(
            name="Categories",
            items=Config.SKETCHFAB_CATEGORIES,
            description="Show only models of category",
            default='ALL',
             )

    face_count : EnumProperty(
            name="Face Count",
            items=Config.SKETCHFAB_FACECOUNT,
            description="Determines which meshes are exported",
            default='ANY',
            )

    sort_by : EnumProperty(
            name="Sort by",
            items=get_sorting_options,
            description="Sort ",
            )

    animated : BoolProperty(
            name="Animated",
            description="Show only models with animation",
            default=False,
            )

    staffpick : BoolProperty(
            name="Staffpick",
            description="Show only staffpick models",
            default=False,
            )

    search_domain : EnumProperty(
            name="Search domain",
            items=get_available_search_domains,
            description="Search domain ",
            )

    use_org_profile : BoolProperty(
        name="Use organisation profile",
        description="Import/Export as a member of an organization\nLOL",
        default=False,
    )

    active_org : EnumProperty(
        name="Org",
        items=get_user_orgs,
        description="Active org",
    )

    use_preview : BoolProperty(
        name="Use Preview",
        description="Show results using preview widget instead of regular buttons with thumbnails as icons",
        default=True
        )

    search_results = {}
    current_key : StringProperty(name='current', default='current')
    has_searched_next : BoolProperty(name='next', default=False)
    has_searched_prev : BoolProperty(name='prev', default=False)

    skfb_api = SketchfabLoginProps.skfb_api
    custom_icons = None
    has_loaded_thumbnails : BoolProperty(default=False)

    is_latest_version : IntProperty(default=-1)

    manualImportBoolean : BoolProperty(
            name="Import from url",
            description="Import a downloadable model from a url",
            default=False,
            )
    manualImportPath : StringProperty(
            name="Url",
            description="Paste full model url:\n* https://sketchfab.com/models/mymodel-XXXX\n* https://sketchfab.com/orgs/XXXX/3d-models/mymodel-YYYY",
            default="",
            maxlen=1024,
            options={'TEXTEDIT_UPDATE'})


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

    layout.separator()

def draw_import_button(layout, model, context):

    import_ops = layout.row()
    skfb = get_sketchfab_props()

    import_ops.enabled = (active_download is None and skfb.skfb_api.is_user_logged()
                          and context.mode == 'OBJECT' and bool(MODEL_UID.fullmatch(model.uid)))
    if active_download is not None:
        progress = active_download.progress
        if progress is None:
            downloadlabel = 'Downloading model…'
        elif progress < 100:
            downloadlabel = f'Downloading model… {progress}%'
        else:
            downloadlabel = 'Preparing model…'
    elif not skfb.skfb_api.is_user_logged():
        downloadlabel = 'Log in to download models'
    elif context.mode != 'OBJECT':
        downloadlabel = 'Import is available only in object mode'
    elif skfb.manualImportBoolean and not model.uid:
        downloadlabel = 'Enter a Sketchfab model URL'
    else:
        downloadlabel = 'Import model'
        if model.download_size:
            downloadlabel += f' ({model.download_size})'

    download_icon = 'IMPORT' if import_ops.enabled else 'INFO'
    import_ops.scale_y = 2.0
    import_ops.operator("wm.sketchfab_download", icon=download_icon, text=downloadlabel,
                        translate=False, emboss=True).model_uid = model.uid


def import_gltf(path, title):
    old_objects = set(bpy.data.objects)
    if 'FINISHED' not in bpy.ops.import_scene.gltf(filepath=str(path), import_pack_images=True):
        raise RuntimeError('Blender cancelled the glTF import')
    bpy.context.view_layer.update()
    Utils.clean_node_hierarchy([obj for obj in bpy.context.scene.objects if obj not in old_objects], title)


def build_search_request(query, pbr, animated, staffpick, face_count, category, sort_by):
    final_query = '&q={}'.format(quote_plus(query)) if query else ''

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


def start_network(job, action, parameters, headers=None, password=None):
    job.start_network(cached_login.interpreter(), bpy.app.binary_path, action, parameters, headers, password)


def stop_worker(job):
    if job is not None:
        job.stop()
        if retired_workers and not bpy.app.timers.is_registered(reap_workers):
            bpy.app.timers.register(reap_workers, first_interval=0.0, persistent=True)


def start_request(action, parameters, headers=None, root=None):
    job = None
    try:
        job = WorkerJob(root)
        start_network(job, action, parameters, headers)
        return job
    except (OSError, ValueError):
        stop_worker(job)
        return None


def queue_request(action, parameters, headers, apply):
    job = start_request(action, parameters, headers)
    if job is None:
        apply(None)
        return
    background_requests[job] = apply
    if not bpy.app.timers.is_registered(poll_requests):
        bpy.app.timers.register(poll_requests, first_interval=0.05)


def poll_requests():
    updated = False
    for job, apply in list(background_requests.items()):
        if not job.running:
            result = job.read_result()
            del background_requests[job]
            stop_worker(job)
            apply(result.get('data'))
            updated = True
    if updated:
        redraw_sketchfab()
    return 0.05 if background_requests else None


def search_cursor(origin_url, cursor):
    if not isinstance(cursor, str) or not cursor:
        return None
    try:
        url = urljoin(origin_url, cursor)
        return url if urlsplit(url)[:2] == urlsplit(origin_url)[:2] else None
    except ValueError:
        return None


def poll_search():
    skfb = get_sketchfab_props()
    api = skfb.skfb_api
    request = api.search_request
    if request is None:
        return None
    if request.running:
        return 0.05
    api.search_request = None
    results = request.read_result().get('data')
    stop_worker(request)

    try:
        origin_url = api.search_url
        if not isinstance(results, dict) or not isinstance(results.get('results'), list):
            raise ValueError('Invalid search response')
        models = OrderedDict((result['uid'], SketchfabModel(result)) for result in results['results'])
        clear_search()
        skfb.search_results['current'] = models
        api.next_results_url = search_cursor(origin_url, results.get('next'))
        api.prev_results_url = search_cursor(origin_url, results.get('previous'))
        request_thumbnails(results['results'], models)
    except (KeyError, TypeError, ValueError):
        api.search_error = 'Could not load Sketchfab results. Try again.'

    redraw_sketchfab()
    return None


def stop_thumbnails():
    global thumbnail_request
    stop_worker(thumbnail_request)
    thumbnail_request = None


def request_thumbnails(results, models):
    global thumbnail_request
    stop_thumbnails()
    missing = {}
    for result in results:
        uid = result['uid']
        path = models[uid].thumbnail_url
        if path and not os.path.isfile(path):
            url = Utils.get_thumbnail_url(result.get('thumbnails'))
            if url:
                missing[uid] = url
    if missing:
        thumbnail_request = start_request('thumbnails', {'thumbnails': list(missing.items())},
                                          root=Config.SKETCHFAB_THUMB_DIR)
    if not bpy.app.timers.is_registered(poll_thumbnails):
        bpy.app.timers.register(poll_thumbnails, first_interval=0.0)


def poll_thumbnails():
    running = False
    if thumbnail_request is not None:
        running = thumbnail_request.running
        for image in thumbnail_request.directory.glob('*.jpeg'):
            try:
                os.replace(image, Path(Config.SKETCHFAB_THUMB_DIR) / image.name)
            except OSError:
                pass
        if not running:
            stop_thumbnails()

    skfb = get_sketchfab_props()
    updated = False
    for uid, model in skfb.search_results.get('current', {}).items():
        if uid not in skfb.custom_icons and model.thumbnail_url and os.path.isfile(model.thumbnail_url):
            try:
                skfb.custom_icons.load(uid, model.thumbnail_url, 'IMAGE')
                updated = True
            except RuntimeError:
                pass
    if updated:
        skfb.has_loaded_thumbnails = False
        redraw_sketchfab()
    return 0.1 if running else None


class View3DPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'TOOLS' if bpy.app.version < (2, 80, 0) else 'UI'
    bl_category = 'Sketchfab'

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
        global is_plugin_enabled
        if not is_plugin_enabled:
            self.layout.operator('wm.skfb_enable', text='Activate add-on', icon="LINKED").enable = True
        else:
            # LOGIN
            skfb_login = get_sketchfab_login_props()
            layout = self.layout.box().column(align=True)
            layout.enabled = get_plugin_enabled()
            if cached_login.pending:
                layout.label(text=skfb_login.status, icon='FILE_REFRESH')
                return
            if skfb_login.skfb_api.is_user_logged():
                login_row = layout.row()
                login_row.label(text='Logged in as {}'.format(skfb_login.skfb_api.get_user_info()))
                login_row.operator('wm.sketchfab_login', text='Logout', icon='DISCLOSURE_TRI_RIGHT').authenticate = False
                if skfb_login.status:
                    layout.prop(skfb_login, 'status', icon=skfb_login.status_type)
            else:
                layout.label(text="Login to your Sketchfab account", icon='INFO')
                layout.prop(skfb_login, "api_token")
                layout.operator("wm.url_open", text='Get API token', icon='URL').url = "https://sketchfab.com/settings/password"
                ops_row = layout.row()
                login_icon = "LINKED" if bpy.app.version < (2,80,0) else "USER"
                ops_row.operator('wm.sketchfab_login', text='Log in', icon=login_icon).authenticate = True
                if skfb_login.status:
                    layout.prop(skfb_login, 'status', icon=skfb_login.status_type)

class TeamsPanel(View3DPanel, bpy.types.Panel):
    bl_idname = "VIEW3D_PT_sketchfab_teams"
    bl_label = "Sketchfab for Teams"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):

        skfb = get_sketchfab_props()
        api  = skfb.skfb_api

        self.layout.enabled = get_plugin_enabled() and api.is_user_logged()

        if api.orgs_loading:
            self.layout.label(text="Loading organizations…", icon='TIME')
        elif api.orgs_check_failed:
            self.layout.label(text="Could not check organization membership", icon='ERROR')
            self.layout.operator('wm.sketchfab_login', text='Retry').authenticate = True
        elif not api.user_has_orgs:
            self.layout.label(text="You are not part of an organization", icon='INFO')
            self.layout.operator("wm.url_open", text='Learn about Sketchfab for Teams').url = "https://sketchfab.com/features/teams"
        else:
            props = get_sketchfab_props_proxy()

            use_org_profile_row = self.layout.row()
            use_org_profile_row.prop(props, "use_org_profile")

            org_row = self.layout.row()
            org_row.prop(props, "active_org")
            org_row.enabled = skfb.skfb_api.use_org_profile

            pprops = get_sketchfab_props()

class Model:
    def __init__(self, _uid):
        self.uid = _uid
        self.download_size = 0

class SketchfabBrowse(View3DPanel, bpy.types.Panel):
    bl_idname = "VIEW3D_PT_sketchfab_browse"
    bl_label = "Import"

    label = "Search results"

    def draw_search(self, layout, context):
        prop = get_sketchfab_props()
        props = get_sketchfab_props_proxy()
        skfb_api = prop.skfb_api

        # Add an option to import from url or uid
        col = layout.box().column(align=True)
        row = col.row()
        row.prop(prop, "manualImportBoolean")

        if prop.manualImportBoolean:
            row = col.row()
            row.prop(prop, "manualImportPath")

        else:
            col = layout.box().column(align=True)
            ro = col.row()
            ro.label(text="Search")
            domain_col = ro.column()
            domain_col.scale_x = 1.5
            domain_col.enabled = skfb_api.is_user_logged()
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
                if props.search_domain in ["DEFAULT", "OWN"]:
                    col.separator()
                    col.prop(props, "categories")
                    col.prop(props, "sort_by")
                    col.prop(props, "face_count")
                    row = col.row()
                    row.prop(props, "pbr")
                    row.prop(props, "staffpick")
                    row.prop(props, "animated")
                else:
                    col.separator()
                    col.prop(props, "sort_by")
                    col.prop(props, "face_count")

        pprops = get_sketchfab_props()

    def draw_results(self, layout, context):

        props = get_sketchfab_props()

        col = layout.box().column(align=True)

        if not props.manualImportBoolean:

            #results = layout.column(align=True)
            col.label(text=self.label)

            model = None

            result_pages_ops = col.row()
            if props.skfb_api.search_request is not None:
                result_pages_ops.enabled = False
            if props.skfb_api.prev_results_url:
                result_pages_ops.operator("wm.sketchfab_search_prev", text="Previous page", icon='FRAME_PREV')

            if props.skfb_api.next_results_url:
                result_pages_ops.operator("wm.sketchfab_search_next", text="Next page", icon='FRAME_NEXT')

            if props.skfb_api.search_request is not None:
                col.label(text="Searching Sketchfab…", icon='TIME')
            elif props.skfb_api.search_error:
                col.label(text=props.skfb_api.search_error, icon='ERROR')

            #result_label = 'Click below to see more results'
            #col.label(text=result_label, icon='INFO')
            try:
                col.template_icon_view(bpy.context.window_manager, 'result_previews', show_labels=True, scale=8, scale_popup=8)
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

                if not model.info_requested:
                    props.skfb_api.request_model_info(model)

                draw_model_info(col, model, context)
                draw_import_button(col, model, context)
        else:
            try:
                uid, _ = parse_model_link(props.manualImportPath)
            except ValueError:
                uid = ''
            m = Model(uid)
            draw_import_button(col, m, context)

    def draw(self, context):
        self.layout.enabled = get_plugin_enabled()
        self.draw_search(self.layout, context)
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
        self.layout.enabled = get_plugin_enabled()

        wm = context.window_manager
        props = wm.sketchfab_export

        layout = self.layout

        # Selection only
        layout.prop(props, "selection")

        # Model properties
        col = layout.box().column(align=True)
        col.enabled = api.is_user_logged()
        if not props.reuploadBoolean:
            col.prop(props, "title")
            col.prop(props, "description")
            col.prop(props, "tags")
            col.prop(props, "draft")
            col.prop(props, "private")
            if props.private:
                col.prop(props, "password")
        col.prop(props, "reuploadBoolean")
        if props.reuploadBoolean:
            col.prop(props, "reuploadPath")

        # Project selection if member of an org
        if api.active_org and api.use_org_profile and not props.reuploadBoolean:
            row = layout.row()
            row.prop(props, "active_project")

        # Upload button
        row = layout.row()
        row.scale_y = 2.0
        upload_label = "Reupload" if props.reuploadBoolean else "Upload"
        upload_icon  = "EXPORT"
        upload_enabled = api.is_user_logged() and context.mode == 'OBJECT'
        if not upload_enabled:
            if not api.is_user_logged():
                upload_label = "Log in to upload models"
            elif context.mode != 'OBJECT':
                upload_label = "Export is only available in object mode"
        if active_upload is not None:
            if active_upload.prepared is None:
                upload_label = 'Preparing model…'
            else:
                size = Utils.humanify_size(active_upload.prepared['size'])
                upload_label = f'Uploading {size}'
            upload_icon = 'TIME'
        row.enabled = upload_enabled and active_upload is None
        row.operator("wm.sketchfab_export", icon=upload_icon, text=upload_label)

        row = layout.row()
        row.enabled = context.mode == 'OBJECT' and active_upload is None
        row.operator("wm.sketchfab_export", text="Check File Size").size_only = True

        if uploaded_model_url:
            layout.operator("wm.url_open", text="View Online Model", icon='URL').url = uploaded_model_url

class SketchfabLogger(bpy.types.Operator):
    """Log in / out your Sketchab.com account"""
    bl_idname = 'wm.sketchfab_login'
    bl_label = 'Sketchfab Login'
    bl_options = {'INTERNAL'}

    authenticate : BoolProperty(default=True)

    def execute(self, context):
        set_login_status('FILE_REFRESH', 'Login to your Sketchfab account...')
        wm = context.window_manager
        if self.authenticate:
            wm.sketchfab_browser.skfb_api.login()
        else:
            set_login_status('INFO', '')
            wm.sketchfab_browser.skfb_api.logout()
        return {'FINISHED'}


class SketchfabModel:
    def __init__(self, json_data):
        self.title = str(json_data['name'])
        self.author = json_data['user']['displayName']
        self.uid = json_data['uid']
        if not MODEL_UID.fullmatch(self.uid):
            raise ValueError('Invalid model ID in search results')
        self.vertex_count = json_data['vertexCount']
        self.face_count = json_data['faceCount']

        gltf = (json_data.get('archives') or {}).get('gltf') or {}
        size = gltf.get('size')
        self.download_size = Utils.humanify_size(size) if isinstance(size, (int, float)) and size > 0 else None

        self.thumbnail_url = (os.path.join(Config.SKETCHFAB_THUMB_DIR, f'{self.uid}.jpeg')
                              if Config.SKETCHFAB_THUMB_DIR else None)

        # Model info request
        self.info_requested = False
        self.license = None
        self.animated = False


def model_request(api, uid, organization):
    return {
        'models_url': Config.SKETCHFAB_MODEL,
        'orgs_url': Config.SKETCHFAB_ORGS,
        'memberships_url': Config.SKETCHFAB_ME + '/orgs',
        'uid': uid,
        'organization': organization,
        'organizations': [{'uid': org['uid'], 'username': org['username']} for org in api.user_orgs]
                         if api.orgs_loaded else None,
    }


class SketchfabDownloadModel(bpy.types.Operator):
    """Import the selected model"""
    bl_idname = "wm.sketchfab_download"
    bl_label = "Import Sketchfab Model"
    bl_options = {'INTERNAL'}

    model_uid : StringProperty(name="uid")

    def execute(self, context):
        global active_download
        wm = context.window_manager
        props = wm.sketchfab_browser
        api = props.skfb_api
        if active_download is not None or context.mode != 'OBJECT' or not api.is_user_logged():
            return {'CANCELLED'}
        try:
            if props.manualImportBoolean:
                uid, organization = parse_model_link(props.manualImportPath)
                self.title = 'Sketchfab Model'
            else:
                uid = self.model_uid
                if not MODEL_UID.fullmatch(uid):
                    raise ValueError('Invalid Sketchfab model ID')
                organization = api.active_org['username'] if api.use_org_profile and api.active_org else None
                model = get_sketchfab_model(uid)
                self.title = model.title if model else 'Sketchfab Model'
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}

        self.uid = uid
        self.wm = wm
        self.scene = context.scene
        self.view_layer = context.view_layer
        self.timer = None
        self.progress = None
        self.job = start_request('download', model_request(api, uid, organization), api.headers,
                                 root=Config.SKETCHFAB_MODEL_DIR)
        if self.job is None:
            self.report({'ERROR'}, 'Could not start the Sketchfab download')
            return {'CANCELLED'}
        try:
            self.timer = wm.event_timer_add(0.2, window=context.window)
            wm.modal_handler_add(self)
        except RuntimeError:
            self.cancel(context)
            self.report({'ERROR'}, 'Could not start the Sketchfab download')
            return {'CANCELLED'}
        active_download = self
        redraw_sketchfab()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if active_download is not self:
            return {'CANCELLED'}
        if event.type == 'ESC':
            self.cancel(context)
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        if context.scene != self.scene or context.view_layer != self.view_layer:
            self.cancel(context)
            return {'CANCELLED'}
        if self.job.running:
            try:
                self.progress = int((self.job.directory / 'progress').read_text(encoding='ascii'))
            except (OSError, ValueError):
                pass
            redraw_sketchfab()
            return {'PASS_THROUGH'}

        try:
            result = self.job.read_result()
            if 'error' in result:
                self.report({'ERROR'}, result['error'])
                return {'CANCELLED'}
            if context.mode != 'OBJECT':
                self.report({'WARNING'}, 'Switch to Object Mode before importing a model')
                return {'CANCELLED'}
            data = result['data']
            metadata = data['metadata']
            title = metadata.get('name')
            if not isinstance(title, str) or not title:
                title = self.title
            import_gltf(data['path'], title)
            record_download(metadata, self.uid, title)
            return {'FINISHED'}
        except (KeyError, RuntimeError, TypeError, ValueError):
            self.report({'ERROR'}, 'Blender could not import the Sketchfab glTF model')
            return {'CANCELLED'}
        finally:
            self.cancel(context)

    def cancel(self, context):
        global active_download
        if active_download is self:
            active_download = None
        if self.timer is not None:
            self.wm.event_timer_remove(self.timer)
            self.timer = None
        stop_worker(self.job)
        redraw_sketchfab()


class ViewOnSketchfab(bpy.types.Operator):
    """Upload your model to Sketchfab"""
    bl_idname = "wm.sketchfab_view"
    bl_label = "View the model on Sketchfab"
    bl_options = {'INTERNAL'}

    model_uid : bpy.props.StringProperty(name="uid")

    def execute(self, context):
        import webbrowser
        webbrowser.open('{}/models/{}'.format(Config.SKETCHFAB_URL, self.model_uid))
        return {'FINISHED'}


def clear_search():
    skfb = get_sketchfab_props()
    skfb.has_loaded_thumbnails = False
    skfb.search_results.clear()
    skfb.custom_icons.clear()
    # Reset the previews enum without assuming the window manager is named "WinMan"
    # (it isn't in headless/background sessions, which raised a KeyError here).
    for wm in bpy.data.window_managers:
        wm['result_previews'] = 0


class SketchfabSearch(bpy.types.Operator):
    """Send a search query to Sketchfab
    Searches on the selected domain (all site, own models for PRO+ users, organization...)
    and takes into accounts various search filters"""
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
        skfb.skfb_api.search(final_query)
        return {'FINISHED'}


class SketchfabSearchNextResults(bpy.types.Operator):
    """Loads the next batch of 24 models from the search results"""
    bl_idname = "wm.sketchfab_search_next"
    bl_label = "Search Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        api = get_sketchfab_props().skfb_api
        if api.search_request is not None or not api.next_results_url:
            return {'CANCELLED'}
        api.start_search(api.next_results_url)
        return {'FINISHED'}


class SketchfabSearchPreviousResults(bpy.types.Operator):
    """Loads the previous batch of 24 models from the search results"""
    bl_idname = "wm.sketchfab_search_prev"
    bl_label = "Search Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        api = get_sketchfab_props().skfb_api
        if api.search_request is not None or not api.prev_results_url:
            return {'CANCELLED'}
        api.start_search(api.prev_results_url)
        return {'FINISHED'}

class SketchfabCreateAccount(bpy.types.Operator):
    """Create an account on sketchfab.com"""
    bl_idname = "wm.sketchfab_signup"
    bl_label = "Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        import webbrowser
        webbrowser.open(Config.SKETCHFAB_SIGNUP)
        return {'FINISHED'}


class SketchfabNewVersion(bpy.types.Operator):
    """Opens addon latest available release on github"""
    bl_idname = "wm.skfb_new_version"
    bl_label = "Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        import webbrowser
        webbrowser.open('{}/releases/latest'.format(Config.GITHUB_REPOSITORY_URL))
        return {'FINISHED'}


class SketchfabReportIssue(bpy.types.Operator):
    """Open an issue on github tracker"""
    bl_idname = "wm.skfb_report_issue"
    bl_label = "Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        import webbrowser
        webbrowser.open('{}/issues/new'.format(Config.GITHUB_REPOSITORY_URL))
        return {'FINISHED'}


class SketchfabHelp(bpy.types.Operator):
    """Opens the addon README on github"""
    bl_idname = "wm.skfb_help"
    bl_label = "Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        import webbrowser
        webbrowser.open('{}/releases/latest'.format(Config.GITHUB_REPOSITORY_URL))
        return {'FINISHED'}


def activate_plugin():
    cached_login.cancel()
    props = get_sketchfab_props()
    login_props = get_sketchfab_login_props()

    global is_plugin_enabled
    is_plugin_enabled = True
    try:
        api_token = Cache.get_key('api_token')
    except (OSError, ValueError, TypeError):
        api_token = None
        set_login_status('ERROR', 'Could not read the cached API token')
    if isinstance(api_token, str) and api_token:
        login_props.api_token = api_token
        props.skfb_api.login()

    queue_request('json', {'url': Config.SKETCHFAB_PLUGIN_VERSION}, {}, check_plugin_version)

    run_default_search()


class SketchfabEnable(bpy.types.Operator):
    """Activate the addon (checks login, cache folders...)"""
    bl_idname = "wm.skfb_enable"
    bl_label = "Sketchfab"
    bl_options = {'INTERNAL'}

    enable : BoolProperty(default=True)
    def execute(self, context):
        if self.enable:
            activate_plugin()

        return {'FINISHED'}


class SketchfabExportProps(bpy.types.PropertyGroup):
    description : StringProperty(
            name="Description",
            description="Description of the model (optional)",
            default="",
            maxlen=1024)
    selection : BoolProperty(
            name="Selection only",
            description="Determines which meshes are exported",
            default=False,
            )
    private : BoolProperty(
            name="Private",
            description="Upload as private (requires a pro account)",
            default=False,
            )
    draft : BoolProperty(
            name="Draft",
            description="Do not publish the model",
            default=True,
            )
    password : StringProperty(
            name="Password",
            description="Password-protect your model (requires a pro account)",
            default="",
            )
    tags : StringProperty(
            name="Tags",
            description="List of tags (42 max), separated by spaces (optional)",
            default="",
            )
    title : StringProperty(
            name="Title",
            description="Title of the model (determined automatically if left empty)",
            default="",
            maxlen=48
            )
    reuploadBoolean : BoolProperty(
            name="Reupload",
            description="Reupload the model over an existing one",
            default=False,
            )
    reuploadPath : StringProperty(
            name="Url",
            description="Paste full model url to reupload to",
            default="",
            maxlen=1024)
    active_project : EnumProperty(
        name="Project",
        items=get_org_projects,
        description="Active project",
    )


def capture_upload(context, api):
    props = context.window_manager.sketchfab_export
    uid = org_name = password = None
    method = 'POST'
    url = Config.SKETCHFAB_MODEL
    is_team_upload = bool(api.use_org_profile and api.active_org)
    if props.reuploadBoolean:
        uid, org_name = parse_model_link(props.reuploadPath)
        method = 'PUT'
        fields = {'uid': uid, 'source': 'blender-exporter'}
        is_team_upload = org_name is not None
    else:
        fields = {
            'name': props.title or Path(bpy.data.filepath).stem,
            'description': props.description,
            'tags': ' '.join(props.tags.split()[:42]),
            'private': props.private,
            'isPublished': not props.draft,
            'source': 'blender-exporter',
        }
        password = props.password
        if is_team_upload:
            url = f'{Config.SKETCHFAB_ORGS}/{api.active_org["uid"]}/models'
            fields['orgProject'] = props.active_project or ''

    parameters = model_request(api, uid, org_name)
    parameters.update({'method': method, 'url': url, 'fields': fields})
    return (parameters, api.headers.copy(), password), 'ent' if is_team_upload else api.plan_type


def save_upload_scene(directory, selection):
    scene_path = directory / 'export-sketchfab.blend'
    settings_path = directory / 'export-sketchfab.json'
    script_path = Path(__file__).resolve().with_name('pack_for_export.py')

    if 'FINISHED' not in bpy.ops.wm.save_as_mainfile(filepath=str(scene_path), compress=True, copy=True):
        raise RuntimeError('Blender cancelled saving the export copy')
    with settings_path.open('w', encoding='utf-8') as settings:
        json.dump({'selection': selection}, settings)
    return [bpy.app.binary_path, '--background', '--factory-startup', '-noaudio', str(scene_path),
            '--python-exit-code', '1', '--python', str(script_path), '--', str(directory)]


class ExportSketchfab(bpy.types.Operator):
    bl_idname = "wm.sketchfab_export"
    bl_label = "Upload"

    size_only : BoolProperty(default=False, options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        if properties.size_only:
            return 'Prepare the upload file and show its size'
        return 'Upload your model to Sketchfab'

    def execute(self, context):
        global active_upload, uploaded_model_url
        if active_upload is not None:
            self.report({'WARNING'}, 'Wait for the current export to finish')
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            self.report({'WARNING'}, 'Export is only available in Object Mode')
            return {'CANCELLED'}
        api = get_sketchfab_props().skfb_api
        if not self.size_only and not api.is_user_logged():
            self.report({'WARNING'}, 'Log in to upload models')
            return {'CANCELLED'}

        self.wm = context.window_manager
        self.timer = None
        self.job = None
        self.prepared = None
        try:
            if self.size_only:
                self.upload = None
                plan = 'ent' if api.use_org_profile else api.plan_type
            else:
                self.upload, plan = capture_upload(context, api)
            self.upload_limit = Config.SKETCHFAB_UPLOAD_LIMITS.get(plan)
            self.job = WorkerJob(prefix='sketchfab-export-')
            command = save_upload_scene(self.job.directory, self.wm.sketchfab_export.selection)
            self.timer = self.wm.event_timer_add(0.2, window=context.window)
            self.job.start(command)
            self.wm.modal_handler_add(self)
        except (OSError, ValueError, RuntimeError) as error:
            self.cancel(context)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}

        if not self.size_only:
            uploaded_model_url = ''
        active_upload = self
        redraw_sketchfab()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        global uploaded_model_url
        if active_upload is not self:
            return {'CANCELLED'}
        if event.type == 'ESC':
            self.cancel(context)
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        if self.job.running:
            redraw_sketchfab()
            return {'PASS_THROUGH'}

        if self.prepared is None:
            try:
                self.prepared = self.job.read_prepared_export()
                for warning in self.prepared.get('warnings', []):
                    self.report({'WARNING'}, warning)
                size = self.prepared['size']
                size_label = Utils.humanify_size(size)
                if self.prepared['filename'].endswith('.zip'):
                    size_label += ' (ZIP)'
                if self.upload_limit is not None and size > self.upload_limit:
                    self.report({'WARNING' if self.size_only else 'ERROR'},
                                f'Upload file is {size_label} and exceeds your '
                                f'{Utils.humanify_size(self.upload_limit)} upload limit')
                elif self.size_only:
                    self.report({'INFO'}, f'Upload file size is {size_label}')
                else:
                    parameters, headers, password = self.upload
                    parameters.update(filepath=self.prepared['filepath'], filename=self.prepared['filename'])
                    start_network(self.job, 'upload', parameters, headers, password)
                    self.upload = None
                    return {'PASS_THROUGH'}
            except (OSError, ValueError, KeyError, TypeError):
                self.report({'ERROR'}, 'Could not prepare the Sketchfab upload')
                self.cancel(context)
                return {'CANCELLED'}
            self.cancel(context)
            return {'FINISHED' if self.size_only else 'CANCELLED'}

        result = self.job.read_result()
        self.cancel(context)
        if 'error' in result:
            self.report({'ERROR'}, result['error'])
            return {'CANCELLED'}
        uid = result.get('data')
        if isinstance(uid, str) and MODEL_UID.fullmatch(uid):
            uploaded_model_url = f'{Config.SKETCHFAB_URL}/models/{uid}'
        self.report({'INFO'}, 'Upload complete. Available on your sketchfab.com dashboard.')
        redraw_sketchfab()
        return {'FINISHED'}

    def cancel(self, context):
        global active_upload
        if active_upload is self:
            active_upload = None
        if self.timer is not None:
            self.wm.event_timer_remove(self.timer)
            self.timer = None
        self.upload = None
        stop_worker(self.job)
        redraw_sketchfab()


def get_temporary_path():
    preferences = bpy.context.preferences
    addon = preferences.addons.get(__name__.split('.')[0])
    configured = addon.preferences.cachePath if addon else ''
    configured = configured or preferences.filepaths.temporary_directory
    if configured:
        return bpy.path.abspath(os.path.expanduser(configured))
    return bpy.app.tempdir or tempfile.gettempdir()


def updateCacheDirectory(self, context):
    path = os.path.abspath(get_temporary_path())

    # Create the temporary directories. If the configured path can't be created
    # (missing drive, permission denied, unusual characters, ...) fall back to the
    # system temp directory so that enabling the add-on never fails because of it.
    for candidate in (path, tempfile.gettempdir()):
        temp_dir  = os.path.join(candidate, "sketchfab_downloads")
        thumb_dir = os.path.join(temp_dir, 'thumbnails')
        model_dir = os.path.join(temp_dir, 'imports')
        try:
            os.makedirs(thumb_dir, exist_ok=True)
            os.makedirs(model_dir, exist_ok=True)
        except OSError as err:
            print("Sketchfab: cannot use cache directory '{}': {}".format(candidate, err))
            continue
        if Config.SKETCHFAB_THUMB_DIR != thumb_dir:
            stop_thumbnails()
        Config.SKETCHFAB_TEMP_DIR  = temp_dir
        Config.SKETCHFAB_THUMB_DIR = thumb_dir
        Config.SKETCHFAB_MODEL_DIR = model_dir
        return

    # Both the configured path and the system temp directory failed: keep the
    # add-on enabled rather than raising, and let the user set a writable folder.
    print("Sketchfab: failed to create a cache directory; downloads may not work "
          "until a writable Cache folder is set in the add-on preferences.")

class SketchfabAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__
    cachePath: StringProperty(
        name="Cache folder",
        description=(
            "Temporary directory for downloads from sketchfab.com\n"
            "Set by the OS by default, make sure to have write access\n"
            "to this directory if you set it manually"
        ),
        subtype='DIR_PATH',
        update=updateCacheDirectory
    )
    downloadHistory : StringProperty(
        name="Download history file",
        description=(
            ".csv file containing your downloads from sketchfab.com\n"
            "If valid, the name, license and url of every model you\n"
            "download through the plugin will be saved in this file"
        ),
        subtype='FILE_PATH'
    )
    def draw(self, context):
        layout = self.layout
        layout.prop(self, "cachePath", text="Download directory")
        layout.prop(self, "downloadHistory", text="Download history (.csv)")

classes = (
    SketchfabAddonPreferences,

    # Properties
    SketchfabBrowserProps,
    SketchfabLoginProps,
    SketchfabBrowserPropsProxy,
    SketchfabExportProps,

    # Panels
    LoginPanel,
    TeamsPanel,
    SketchfabBrowse,
    SketchfabExportPanel,
    SketchfabPanel,

    # Operators
    SketchfabEnable,
    SketchfabCreateAccount,
    SketchfabNewVersion,
    SketchfabHelp,
    SketchfabReportIssue,
    SketchfabSearch,
    SketchfabSearchPreviousResults,
    SketchfabSearchNextResults,
    ViewOnSketchfab,
    SketchfabDownloadModel,
    SketchfabLogger,
    ExportSketchfab,
    )

def check_plugin_version(releases):
    props = get_sketchfab_props()
    props.is_latest_version = -2
    if not isinstance(releases, list):
        return
    for release in releases:
        if not isinstance(release, dict) or release.get('draft') or release.get('prerelease'):
            continue
        tag = release.get('tag_name', '')
        try:
            version = tuple(int(part) for part in tag.lstrip('v').split('.'))
        except (AttributeError, ValueError):
            continue
        if len(version) == 3:
            props.is_latest_version = int(version <= bl_info['version'])
            return


cached_login = CachedLogin(sys.modules[__name__])


def register():
    global is_plugin_enabled
    is_plugin_enabled = False
    cached_login.reset_account()
    sketchfab_icon = bpy.utils.previews.new()
    icons_dir      = os.path.dirname(__file__)
    sketchfab_icon.load("skfb", os.path.join(icons_dir, "logo.png"), 'IMAGE')
    sketchfab_icon.load("0",    os.path.join(icons_dir, "placeholder.png"), 'IMAGE')

    res = []
    res.append(('NORESULTS', 'empty', "", sketchfab_icon['0'].icon_id, 0))
    preview_collection['default'] = tuple(res)
    preview_collection['skfb'] = sketchfab_icon
    bpy.types.WindowManager.result_previews = EnumProperty(items=list_current_results)

    for cls in classes:
        bpy.utils.register_class(cls)

    SketchfabBrowserProps.custom_icons = bpy.utils.previews.new()

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

    # If a cache path was set in preferences, use it. Never let cache setup
    # abort registration, otherwise the add-on fails to enable entirely.
    try:
        updateCacheDirectory(None, context=bpy.context)
    except Exception as err:
        print("Sketchfab: failed to initialize cache directory during registration: {}".format(err))

    cached_login.register()
    bpy.app.handlers.load_pre.append(cancel_transfers)
    if hasattr(bpy.app.handlers, 'quit_pre'):
        bpy.app.handlers.quit_pre.append(cancel_transfers)

def record_download(metadata, uid, title):
    user = metadata.get('user')
    if not isinstance(user, dict):
        user = {}
    license = metadata.get('license')
    if not isinstance(license, dict):
        license = {}
    url = metadata.get('viewerUrl') or f'{Config.SKETCHFAB_URL}/models/{uid}'
    username = user.get('username', '')
    license_name = license.get('fullName') or license.get('label') or ''
    if username and license_name:
        credit = f'"{title}" ({url}) by {username} is licensed under {license_name}'
        if license.get('url'):
            credit += f' ({license["url"]})'
        credit += '.'
    else:
        credit = f'"{title}" ({url}). Attribution unavailable.'
    text = bpy.data.texts.get('sf_attributions') or bpy.data.texts.new('sf_attributions')
    last_line = text.lines[-1].body
    text.cursor_set(len(text.lines) - 1, character=len(last_line))
    if last_line:
        text.write('\n')
    text.write(credit + '\n')

    addon = bpy.context.preferences.addons.get(__name__.split('.')[0])
    history = addon.preferences.downloadHistory if addon else ''
    if history:
        try:
            with open(os.path.abspath(history), 'a+', encoding='utf-8', newline='') as file:
                writer = csv.writer(file)
                if file.tell() == 0:
                    writer.writerow(('Model name', 'Author name', 'Author url', 'License', 'Model link'))
                author_url = f'{Config.SKETCHFAB_URL}/{username}' if username else ''
                writer.writerow((title, user.get('displayName', username), author_url, license_name, url))
        except OSError:
            print('Sketchfab: could not save download history')


@bpy.app.handlers.persistent
def cancel_transfers(*args):
    global uploaded_model_url
    if active_download is not None:
        active_download.cancel(bpy.context)
    if active_upload is not None:
        active_upload.cancel(bpy.context)
    uploaded_model_url = ''


def unregister():
    global is_plugin_enabled
    cancel_transfers()
    bpy.app.handlers.load_pre.remove(cancel_transfers)
    if hasattr(bpy.app.handlers, 'quit_pre'):
        bpy.app.handlers.quit_pre.remove(cancel_transfers)
    cached_login.unregister()
    for callback in (poll_search, poll_thumbnails, poll_requests):
        if bpy.app.timers.is_registered(callback):
            bpy.app.timers.unregister(callback)
    is_plugin_enabled = False
    bpy.utils.previews.remove(SketchfabBrowserProps.custom_icons)
    SketchfabBrowserProps.custom_icons = None
    for cls in classes:
        bpy.utils.unregister_class(cls)

    del bpy.types.WindowManager.sketchfab_api
    del bpy.types.WindowManager.sketchfab_browser
    del bpy.types.WindowManager.sketchfab_browser_proxy
    del bpy.types.WindowManager.sketchfab_export

    bpy.utils.previews.remove(preview_collection['skfb'])
    del bpy.types.WindowManager.result_previews


if __name__ == "__main__":
    register()
