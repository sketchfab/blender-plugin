from urllib.parse import urljoin, urlsplit


def unique_pages(url, fetch):
    origin = urlsplit(url)[:2]
    pages = set()
    uids = set()
    while url:
        if url in pages or urlsplit(url)[:2] != origin:
            raise ValueError('Invalid Sketchfab pagination link')
        pages.add(url)
        page = fetch(url)
        for item in page['results']:
            uid = item['uid']
            if uid not in uids:
                uids.add(uid)
                yield item
        next_page = page.get('next')
        url = urljoin(url, next_page) if next_page else None


def fetch_organizations(url, orgs_url, fetch, include_projects=True):
    organizations = []
    for item in unique_pages(url, fetch):
        org = {'uid': item['uid'], 'displayName': item['displayName'], 'username': item['username'],
               'url': item.get('publicProfileUrl'), 'projects': []}
        organizations.append(org)

    if include_projects:
        for org in organizations:
            projects = unique_pages(f'{orgs_url}/{org["uid"]}/projects', fetch)
            org['projects'] = sorted(projects, key=lambda project: project['name'])
    return sorted(organizations, key=lambda org: org['displayName'])
