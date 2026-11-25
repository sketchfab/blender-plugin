import unittest

from organization_api import fetch_organizations, unique_pages


class OrganizationTests(unittest.TestCase):
    def test_organizations_and_projects_are_paginated_and_deduplicated(self):
        root = 'https://api.example.test'
        memberships = root + '/me/orgs'
        teams = root + '/orgs'
        first = {'uid': 'first', 'displayName': 'A', 'username': 'a'}
        second = {'uid': 'second', 'displayName': 'B', 'username': 'b'}
        project = {'uid': 'project', 'name': 'Project'}
        responses = {
            memberships: {'results': [second], 'next': '?page=2'},
            memberships + '?page=2': {'results': [second, first]},
            teams + '/first/projects': {'results': []},
            teams + '/second/projects': {'results': [project], 'next': '?page=2'},
            teams + '/second/projects?page=2': {'results': [project, {'uid': 'other', 'name': 'Other'}]},
        }

        orgs = fetch_organizations(memberships, teams, responses.pop)
        self.assertEqual(responses, {})
        self.assertEqual([org['uid'] for org in orgs], ['first', 'second'])
        self.assertEqual([item['uid'] for item in orgs[1]['projects']], ['other', 'project'])

    def test_pagination_refuses_cycles_and_changes_of_origin(self):
        url = 'https://api.example.test/orgs'
        for next_page in (url, 'https://other.example.test/orgs'):
            with self.subTest(next_page=next_page), self.assertRaises(ValueError):
                list(unique_pages(url, lambda _: {'results': [], 'next': next_page}))
