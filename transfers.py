import json
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile

import requests
from urllib3.fields import RequestField
from urllib3.filepost import choose_boundary, encode_multipart_formdata

from .model_links import model_endpoint
from .organization_api import fetch_organizations


REQUEST_TIMEOUT = (5, 15)
CHUNK_SIZE = 128 * 1024


class TransferError(Exception):
    pass


def api_json(url, headers):
    with requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT) as response:
        response.raise_for_status()
        return response.json()


def extract_gltf(archive_path, directory):
    with ZipFile(archive_path) as archive:
        for member in archive.infolist():
            # ZIP paths must also be safe when extracted on Windows.
            name = member.filename
            parts = name.rstrip('/').split('/')
            if any(part in {'', '.', '..'} or part.rstrip(' .') != part or ':' in part or '\\' in part for part in parts):
                raise ValueError('The model archive contains an invalid path')
            destination = directory.joinpath(*parts)
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open('xb') as target:
                while True:
                    chunk = source.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    target.write(chunk)

    gltf_path = directory / 'scene.gltf'
    if not gltf_path.is_file():
        raise ValueError('The model archive does not contain scene.gltf')
    with gltf_path.open(encoding='utf-8') as gltf:
        document = json.load(gltf)
    for entry in document.get('buffers', []) + document.get('images', []):
        if 'uri' not in entry or entry['uri'].startswith('data:'):
            continue
        uri = urlsplit(entry['uri'])
        path = unquote(uri.path)
        parts = path.split('/')
        if (uri.scheme or uri.netloc or any(part in {'', '..'} or part.rstrip(' .') != part or ':' in part or '\\' in part for part in parts)):
            raise ValueError('The model references a file outside the download')
    return gltf_path


def resolve_model(parameters, headers):
    org_name = parameters['organization']
    orgs = parameters['organizations']
    if org_name and orgs is None:
        orgs = fetch_organizations(parameters['memberships_url'], parameters['orgs_url'],
                                   lambda url: api_json(url, headers), include_projects=False)
    try:
        return model_endpoint(parameters['models_url'], parameters['orgs_url'], parameters['uid'], org_name, orgs or ())
    except ValueError as error:
        raise TransferError(str(error)) from error


def download_model(directory, parameters, headers):
    try:
        endpoint = resolve_model(parameters, headers)
        response = api_json(endpoint + '/download', headers)
        try:
            archive_url = response['gltf']['url']
        except (KeyError, TypeError):
            raise TransferError('No glTF download is available for this model')

        archive_path = directory / 'download.zip'
        progress = directory / 'progress'
        with requests.get(archive_url, stream=True, timeout=REQUEST_TIMEOUT) as response:
            response.raise_for_status()
            length = int(response.headers.get('Content-Length', 0))
            downloaded = 0
            previous = None
            with archive_path.open('wb') as archive:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    archive.write(chunk)
                    downloaded += len(chunk)
                    if length:
                        percent = min(100, int(100 * downloaded / length))
                        if percent != previous:
                            progress.write_text(str(percent), encoding='ascii')
                            previous = percent

        progress.write_text('100', encoding='ascii')
        path = extract_gltf(archive_path, directory / 'asset')
        metadata = {}
        try:
            result = api_json(endpoint, headers)
            if isinstance(result, dict):
                metadata = result
        except (requests.exceptions.RequestException, ValueError):
            pass
        return {'path': str(path), 'metadata': metadata}
    except requests.exceptions.HTTPError as error:
        raise TransferError(f'Sketchfab download failed (HTTP {error.response.status_code})') from error
    except requests.exceptions.RequestException as error:
        raise TransferError('The Sketchfab download was interrupted. Try again.') from error
    except (ValueError, BadZipFile, OSError, TypeError, AttributeError, RuntimeError, NotImplementedError) as error:
        raise TransferError('Could not prepare the Sketchfab glTF archive') from error


class MultipartUpload:
    def __init__(self, model, filepath, filename, fields):
        boundary = choose_boundary()
        self.content_type = f'multipart/form-data; boundary={boundary}'
        closing_boundary = f'--{boundary}--\r\n'.encode()
        self.epilogue = b'\r\n' + closing_boundary
        prefix, _ = encode_multipart_formdata(
            [(name, str(value)) for name, value in fields.items()], boundary=boundary)
        attachment = RequestField('modelFile', None, filename=filename)
        attachment.make_multipart(content_type='application/octet-stream')
        self.preamble = prefix[:-len(closing_boundary)] + f'--{boundary}\r\n'.encode() + attachment.render_headers().encode()
        self.model = model
        self.size = filepath.stat().st_size

    def __len__(self):
        return len(self.preamble) + self.size + len(self.epilogue)

    def __iter__(self):
        yield self.preamble
        while True:
            data = self.model.read(CHUNK_SIZE)
            if not data:
                break
            yield data
        yield self.epilogue


def send_model(filepath, filename, method, url, headers, fields, uid):
    with filepath.open('rb') as model:
        body = MultipartUpload(model, filepath, filename, fields)
        with requests.request(method, url, data=body, headers={**headers, 'Content-Type': body.content_type},
                              timeout=REQUEST_TIMEOUT, allow_redirects=False) as response:
            if response.status_code not in {200, 201, 204}:
                raise TransferError(f'Sketchfab upload failed (HTTP {response.status_code})')
            try:
                uploaded_uid = response.json().get('uid')
            except (ValueError, AttributeError):
                uploaded_uid = None
            return uploaded_uid or uid


def upload_model(parameters, headers, password):
    try:
        fields = parameters['fields']
        if password is not None:
            fields['password'] = password
        endpoint = resolve_model(parameters, headers) if parameters['uid'] else parameters['url']
        return send_model(Path(parameters['filepath']), parameters['filename'], parameters['method'],
                          endpoint, headers, fields, parameters['uid'])
    except requests.exceptions.RequestException as error:
        raise TransferError('Could not reach Sketchfab to upload the model') from error
