import unittest

from model_links import model_endpoint, parse_model_link


UID = '0123456789abcdef0123456789abcdef'


class ModelLinksTests(unittest.TestCase):
    def test_personal_and_team_share_links(self):
        self.assertEqual(parse_model_link(f'https://www.sketchfab.com/3d-models/slug-{UID}/?share=1#preview'),
                         (UID, None))
        self.assertEqual(parse_model_link(f'http://sketchfab.com/orgs/Team/3d-models/slug-{UID}?x=1'),
                         (UID, 'Team'))
        self.assertEqual(parse_model_link(f'https://sketchfab.com/models/{UID.upper()}'), (UID, None))

    def test_rejects_lookalike_hosts_and_invalid_model_paths(self):
        for url in (f'https://unrelated.com/sketchfab.com/models/{UID}',
                    f'https://sketchfab.com.unrelated.com/models/{UID}',
                    f'https://sketchfab.com@unrelated.com/models/{UID}',
                    f'https://sketchfab.com/anything/{UID}',
                    f'https://sketchfab.com/models/slug-x{UID}',
                    'https://sketchfab.com/models/' + 'g' * 32):
            with self.subTest(url=url), self.assertRaises(ValueError):
                parse_model_link(url)

    def test_endpoint_depends_on_link_scope(self):
        organizations = [{'username': 'team', 'uid': 'org-id'}]
        self.assertEqual(model_endpoint('models', 'orgs', UID, organizations=organizations), f'models/{UID}')
        self.assertEqual(model_endpoint('models', 'orgs', UID, 'Team', organizations), f'orgs/org-id/models/{UID}')
        with self.assertRaises(ValueError):
            model_endpoint('models', 'orgs', UID, 'other', organizations)
