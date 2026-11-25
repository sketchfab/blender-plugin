import re
from urllib.parse import urlsplit


MODEL_UID = re.compile(r'[0-9a-fA-F]{32}')


def parse_model_link(link):
    url = urlsplit(link.strip())
    if (url.scheme not in {'http', 'https'}
            or url.netloc.lower() not in {'sketchfab.com', 'www.sketchfab.com'}):
        raise ValueError('Enter a full Sketchfab model URL')

    parts = url.path.strip('/').split('/')
    organization = None
    if len(parts) == 4 and parts[0] == 'orgs' and parts[1]:
        organization = parts[1]
        parts = parts[2:]
    if len(parts) != 2 or parts[0] not in {'models', '3d-models'}:
        raise ValueError('Enter a full Sketchfab model URL')

    uid = parts[1].rsplit('-', 1)[-1]
    if not MODEL_UID.fullmatch(uid):
        raise ValueError('The Sketchfab model URL must end with a valid model ID')
    return uid.lower(), organization


def model_endpoint(models_url, orgs_url, uid, organization=None, organizations=()):
    if not MODEL_UID.fullmatch(uid):
        raise ValueError('Invalid Sketchfab model ID')
    if organization is None:
        return f'{models_url}/{uid}'

    org = next((org for org in organizations if org['username'].lower() == organization.lower()), None)
    if org is None:
        raise ValueError('This model belongs to an organization your account cannot access')
    return f'{orgs_url}/{org["uid"]}/models/{uid}'
