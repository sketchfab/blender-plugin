import json
import ssl
import sys
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, *args, **kwargs):
        raise urllib.error.URLError('Redirect refused')


def fetch(opener, url, token):
    request = urllib.request.Request(url, headers={'Authorization': 'Token ' + token})
    with opener.open(request, timeout=5) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError('Response too large')
    return json.loads(data)


def verify_login(args):
    context = ssl.create_default_context(cafile=args['ca_file'])
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))
    try:
        user = fetch(opener, args['url'], args['token'])
    except urllib.error.HTTPError as error:
        return {'error': 'rejected' if error.code == 401 else 'unavailable'}
    fields = {key: user[key] for key in ('username', 'displayName', 'account')}
    if any(not isinstance(value, str) or len(value) > 256 for value in fields.values()):
        raise ValueError('Invalid account data')
    if not fields['username'] or not fields['account']:
        raise ValueError('Missing account data')

    try:
        orgs = fetch(opener, args['url'] + '/orgs', args['token'])
        if not isinstance(orgs['results'], list):
            raise ValueError('Invalid organization data')
        fields['user_has_orgs'] = bool(orgs['results'])
        fields['orgs_check_failed'] = False
    except Exception:
        fields['user_has_orgs'] = False
        fields['orgs_check_failed'] = True
    return fields


def main():
    try:
        data = sys.stdin.buffer.read(4097)
        if len(data) > 4096:
            raise ValueError('Input too large')
        result = verify_login(json.loads(data))
        output = json.dumps(result, ensure_ascii=False).encode('utf-8')
        if len(output) > 4096:
            raise ValueError('Output too large')
    except Exception:
        # Network exceptions can contain request details, including credentials.
        output = b'{"error": "unavailable"}'
    sys.stdout.buffer.write(output)


if __name__ == '__main__':
    main()
