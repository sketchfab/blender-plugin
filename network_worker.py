from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
from types import ModuleType

import requests


# Import the add-on's helpers without loading its Blender UI.
package = ModuleType('sketchfab_worker')
package.__path__ = [str(Path(__file__).resolve().parent)]
sys.modules[package.__name__] = package

from sketchfab_worker.login_worker import verify_login
from sketchfab_worker.model_links import MODEL_UID
from sketchfab_worker.organization_api import fetch_organizations
from sketchfab_worker.transfers import api_json, download_model, REQUEST_TIMEOUT, TransferError, upload_model


def download_thumbnails(directory, thumbnails):
    def download(item):
        uid, url = item
        if not MODEL_UID.fullmatch(uid):
            return
        try:
            with requests.get(url, timeout=REQUEST_TIMEOUT) as response:
                response.raise_for_status()
                temporary = directory / (uid + '.part')
                temporary.write_bytes(response.content)
                temporary.replace(directory / (uid + '.jpeg'))
        except (OSError, requests.exceptions.RequestException):
            pass

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(download, thumbnails))


def run(directory, action, parameters, headers, password):
    if action == 'json':
        return {'data': api_json(parameters['url'], headers)}
    if action == 'orgs':
        return {'data': fetch_organizations(parameters['url'], parameters['orgs_url'],
                                            lambda url: api_json(url, headers))}
    if action == 'thumbnails':
        download_thumbnails(directory, parameters['thumbnails'])
        return {'data': None}
    if action == 'download':
        return {'data': download_model(directory, parameters, headers)}
    if action == 'upload':
        return {'data': upload_model(parameters, headers, password)}
    if action == 'login':
        args = {'url': parameters['url'], 'token': headers['Authorization'].split(' ', 1)[1],
                'ca_file': requests.certs.where()}
        return {'data': verify_login(args)}
    raise ValueError('Unknown Sketchfab worker action')


if __name__ == '__main__':
    directory = Path(sys.argv[-1])
    try:
        path = directory / 'request.json'
        request = json.loads(path.read_text(encoding='utf-8'))
        path.unlink()
        secrets = json.loads(os.environ.pop('SKETCHFAB_WORKER_SECRETS'))
        result = run(directory, request['action'], request['parameters'], secrets['headers'], secrets['password'])
    except TransferError as error:
        result = {'error': str(error)}
    except Exception:
        result = {'error': 'The Sketchfab request failed'}
    (directory / 'result.json').write_text(json.dumps(result), encoding='utf-8')
