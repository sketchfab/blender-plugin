import json
import shutil
import subprocess
import tempfile
import threading
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


class TransferCancelled(Exception):
    pass


class DownloadError(Exception):
    pass


def api_json(url, headers):
    with requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT) as response:
        response.raise_for_status()
        return response.json()


def check_cancelled(cancelled):
    if cancelled.is_set():
        raise TransferCancelled()


def extract_gltf(archive_path, directory, cancelled):
    with ZipFile(archive_path) as archive:
        for member in archive.infolist():
            check_cancelled(cancelled)
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
                    check_cancelled(cancelled)
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


class DownloadJob(threading.Thread):
    def __init__(self, directory, models_url, orgs_url, memberships_url, headers, uid, org_name, orgs):
        super().__init__(daemon=True)
        self.directory = Path(tempfile.mkdtemp(prefix='model-', dir=directory or None))
        self.models_url = models_url
        self.orgs_url = orgs_url
        self.memberships_url = memberships_url
        self.headers = headers.copy()
        self.uid = uid
        self.org_name = org_name
        self.orgs = orgs
        self.path = None
        self.metadata = {}
        self.error = None
        self.progress = None
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.done = False

    def cleanup(self):
        with self.lock:
            directory, self.directory = self.directory, None
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)

    def cancel(self):
        with self.lock:
            self.cancelled.set()
            done = self.done
        if done:
            self.cleanup()

    def fetch(self, url):
        check_cancelled(self.cancelled)
        return api_json(url, self.headers)

    def run(self):
        try:
            orgs = self.orgs
            if self.org_name and orgs is None:
                orgs = fetch_organizations(self.memberships_url, self.orgs_url, self.fetch, include_projects=False)
            try:
                endpoint = model_endpoint(self.models_url, self.orgs_url, self.uid, self.org_name, orgs or ())
            except ValueError as error:
                raise DownloadError(str(error)) from error
            response = self.fetch(endpoint + '/download')
            try:
                archive_url = response['gltf']['url']
            except (KeyError, TypeError):
                raise DownloadError('No glTF download is available for this model')

            archive_path = self.directory / 'download.zip'
            check_cancelled(self.cancelled)
            with requests.get(archive_url, stream=True, timeout=REQUEST_TIMEOUT) as response:
                response.raise_for_status()
                length = int(response.headers.get('Content-Length', 0))
                downloaded = 0
                with archive_path.open('wb') as archive:
                    for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                        check_cancelled(self.cancelled)
                        archive.write(chunk)
                        downloaded += len(chunk)
                        if length:
                            self.progress = min(100, int(100 * downloaded / length))

            self.progress = 100
            path = extract_gltf(archive_path, self.directory / 'asset', self.cancelled)
            try:
                metadata = self.fetch(endpoint)
                if isinstance(metadata, dict):
                    self.metadata = metadata
            except (requests.exceptions.RequestException, ValueError):
                pass
            self.path = path
        except TransferCancelled:
            pass
        except DownloadError as error:
            self.error = str(error)
        except requests.exceptions.HTTPError as error:
            status = error.response.status_code
            self.error = f'Sketchfab download failed (HTTP {status})'
        except requests.exceptions.RequestException:
            self.error = 'The Sketchfab download was interrupted. Try again.'
        except (ValueError, BadZipFile, OSError, TypeError, AttributeError, RuntimeError, NotImplementedError):
            self.error = 'Could not prepare the Sketchfab glTF archive'
        finally:
            with self.lock:
                self.done = True
                discard = self.cancelled.is_set() or self.path is None
            if discard:
                self.cleanup()


class MultipartUpload:
    def __init__(self, model, filepath, filename, fields, cancelled):
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
        self.cancelled = cancelled

    def __len__(self):
        return len(self.preamble) + self.size + len(self.epilogue)

    def __iter__(self):
        check_cancelled(self.cancelled)
        yield self.preamble
        while True:
            check_cancelled(self.cancelled)
            data = self.model.read(CHUNK_SIZE)
            if not data:
                break
            yield data
        check_cancelled(self.cancelled)
        yield self.epilogue


def send_model(filepath, filename, method, url, headers, fields, uid, cancelled):
    with filepath.open('rb') as model:
        body = MultipartUpload(model, filepath, filename, fields, cancelled)
        with requests.request(method, url, data=body, headers={**headers, 'Content-Type': body.content_type},
                              timeout=REQUEST_TIMEOUT, allow_redirects=False) as response:
            if response.status_code not in {200, 201, 204}:
                raise UploadError(f'Sketchfab upload failed (HTTP {response.status_code})')
            check_cancelled(cancelled)
            try:
                uploaded_uid = response.json().get('uid')
            except (ValueError, AttributeError):
                uploaded_uid = None
            return uploaded_uid or uid


class UploadError(Exception):
    pass


def pack_scene(command, directory, cancelled):
    check_cancelled(cancelled)
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL) as process:
        try:
            while process.poll() is None:
                if cancelled.wait(0.1):
                    raise TransferCancelled()
        finally:
            if process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
        if process.returncode:
            raise UploadError(f'Background Blender could not prepare the file (exit {process.returncode})')
    check_cancelled(cancelled)
    with (directory / 'export-sketchfab.json').open(encoding='utf-8') as settings:
        result = json.load(settings)
    path = Path(result['filepath']).resolve()
    if (path.parent != directory.resolve() or path.name != result['filename']
            or path.stat().st_size != result['size']):
        raise UploadError('Background Blender returned an invalid export file')
    return result


class UploadJob(threading.Thread):
    def __init__(self, directory, command, upload_limit, send=None):
        super().__init__(daemon=True)
        self.directory = directory
        self.command = command
        self.upload_limit = upload_limit
        self.send = send
        self.cancelled = threading.Event()
        self.prepared = None
        self.model_uid = None
        self.error = None

    def run(self):
        try:
            self.prepared = pack_scene(self.command, self.directory, self.cancelled)
            check_cancelled(self.cancelled)
            if self.send is not None and (self.upload_limit is None or self.prepared['size'] <= self.upload_limit):
                self.model_uid = self.send(Path(self.prepared['filepath']), self.prepared['filename'], self.cancelled)
        except TransferCancelled:
            pass
        except UploadError as error:
            self.error = str(error)
        except requests.exceptions.RequestException:
            self.error = 'Could not reach Sketchfab to upload the model'
        except (OSError, ValueError, KeyError, TypeError):
            self.error = 'Could not prepare the Sketchfab upload file'
        finally:
            shutil.rmtree(self.directory, ignore_errors=True)
