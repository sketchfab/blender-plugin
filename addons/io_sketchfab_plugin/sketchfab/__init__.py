"""
 * ***** BEGIN GPL LICENSE BLOCK *****
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software Foundation,
 * Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
 *
 *
 * ***** END GPL LICENSE BLOCK *****
 """

import os
import bpy
import json
import shutil
import tempfile


class Config:

    # sometimes the path in preferences is empty
    def get_temp_path():
        if bpy.app.version == (2, 79, 0):
            if bpy.context.user_preferences.filepaths.temporary_directory:
                return bpy.context.user_preferences.filepaths.temporary_directory
            else:
                return tempfile.mkdtemp()
        else:
            if bpy.context.preferences.filepaths.temporary_directory:
                return bpy.context.preferences.filepaths.temporary_directory
            else:
                return tempfile.mkdtemp()

    ADDON_NAME = 'io_sketchfab'
    GITHUB_REPOSITORY_URL = 'https://github.com/sketchfab/blender-plugin'
    GITHUB_REPOSITORY_API_URL = 'https://api.github.com/repos/sketchfab/blender-plugin'
    SKETCHFAB_REPORT_URL = 'https://help.sketchfab.com/hc/en-us/requests/new?type=exporters&subject=Blender+Plugin'

    SKETCHFAB_URL = 'https://sketchfab.com'
    DUMMY_CLIENTID = 'hGC7unF4BHyEB0s7Orz5E1mBd3LluEG0ILBiZvF9'
    SKETCHFAB_OAUTH = SKETCHFAB_URL + '/oauth2/token/?grant_type=password&client_id=' + DUMMY_CLIENTID
    SKETCHFAB_API = 'https://api.sketchfab.com'
    SKETCHFAB_SEARCH = SKETCHFAB_API + '/v3/search'
    SKETCHFAB_MODEL = SKETCHFAB_API + '/v3/models'
    SKETCHFAB_SIGNUP = 'https://sketchfab.com/signup'

    BASE_SEARCH = SKETCHFAB_SEARCH + '?type=models&downloadable=true'
    DEFAULT_FLAGS = '&staffpicked=true&sort_by=-staffpickedAt'
    DEFAULT_SEARCH = SKETCHFAB_SEARCH + \
                     '?type=models&downloadable=true' + DEFAULT_FLAGS

    SKETCHFAB_ME = '{}/v3/me'.format(SKETCHFAB_URL)
    BASE_SEARCH_OWN_MODELS = SKETCHFAB_ME + '/search?type=models&downloadable=true'

    SKETCHFAB_PLUGIN_VERSION = '{}/releases'.format(GITHUB_REPOSITORY_API_URL)
    # PATH management
    SKETCHFAB_TEMP_DIR = os.path.join(get_temp_path(), 'sketchfab_downloads')
    SKETCHFAB_THUMB_DIR = os.path.join(SKETCHFAB_TEMP_DIR, 'thumbnails')
    SKETCHFAB_MODEL_DIR = os.path.join(SKETCHFAB_TEMP_DIR, 'imports')

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
    MAX_THUMBNAIL_HEIGHT = 512


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

    def build_download_url(uid):
        return '{}/{}/download'.format(Config.SKETCHFAB_MODEL, uid)


    def thumbnail_file_exists(uid):
        return os.path.exists(os.path.join(Config.SKETCHFAB_THUMB_DIR, '{}.jpeg'.format(uid)))


    def clean_thumbnail_directory():
        if not os.path.exists(Config.SKETCHFAB_THUMB_DIR):
            return

        from os import listdir
        for file in listdir(Config.SKETCHFAB_THUMB_DIR):
            os.remove(os.path.join(Config.SKETCHFAB_THUMB_DIR, file))


    def clean_downloaded_model_dir(uid):
        shutil.rmtree(os.path.join(Config.SKETCHFAB_MODEL_DIR, uid))


    def get_thumbnail_url(thumbnails_json):
        best_height = 0
        best_thumbnail = None
        for image in thumbnails_json['images']:
            if image['height'] <= Config.MAX_THUMBNAIL_HEIGHT and image['height'] > best_height:
                best_height = image['height']
                best_thumbnail = image['url']

        return best_thumbnail


    def make_model_name(gltf_data):
        if 'title' in gltf_data.asset.extras:
            return gltf_data.asset.extras['title']

        return 'GLTFModel'

    def setup_plugin():
        if not os.path.exists(Config.SKETCHFAB_THUMB_DIR):
            os.makedirs(Config.SKETCHFAB_THUMB_DIR)

    def get_uid_from_thumbnail_url(thumbnail_url):
        return thumbnail_url.split('/')[4]

    def get_uid_from_model_url(model_url):
        return model_url.split('/')[5]

    def get_uid_from_download_url(model_url):
        return model_url.split('/')[6]


class Cache:
    SKETCHFAB_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".cache")

    def read():
        if not os.path.exists(Cache.SKETCHFAB_CACHE_FILE):
            return {}

        with open(Cache.SKETCHFAB_CACHE_FILE, 'rb') as f:
            data = f.read().decode('utf-8')
            return json.loads(data)

    def get_key(key):
        cache_data = Cache.read()
        if key in cache_data:
            return cache_data[key]

    def save_key(key, value):
        cache_data = Cache.read()
        cache_data[key] = value
        with open(Cache.SKETCHFAB_CACHE_FILE, 'wb+') as f:
            f.write(json.dumps(cache_data).encode('utf-8'))

    def delete_key(key):
        cache_data = Cache.read()
        if key in cache_data:
            del cache_data[key]

        with open(Cache.SKETCHFAB_CACHE_FILE, 'wb+') as f:
            f.write(json.dumps(cache_data).encode('utf-8'))
