###########################################################################
#          (C) Vrije Universiteit, Amsterdam (the Netherlands)            #
#                                                                         #
# This file is part of AmCAT - The Amsterdam Content Analysis Toolkit     #
#                                                                         #
# AmCAT is free software: you can redistribute it and/or modify it under  #
# the terms of the GNU Affero General Public License as published by the  #
# Free Software Foundation, either version 3 of the License, or (at your  #
# option) any later version.                                              #
#                                                                         #
# AmCAT is distributed in the hope that it will be useful, but WITHOUT    #
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or   #
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public     #
# License for more details.                                               #
#                                                                         #
# You should have received a copy of the GNU Affero General Public        #
# License along with AmCAT.  If not, see <http://www.gnu.org/licenses/>.  #
###########################################################################
import json
from django.test import Client
from amcat.models import ROLE_PROJECT_WRITER, Query
from amcat.models import ProjectRole
from amcat.tools import amcattest
from api.rest.viewsets import QueryViewSet


class TestQueryViewSet(amcattest.AmCATTestCase):
    def setUp(self):
        self.project = amcattest.create_test_project()
        self.non_member = amcattest.create_test_user()
        self.member = amcattest.create_test_user()
        self.owner = amcattest.create_test_user()

        ProjectRole.objects.create(project=self.project, user=self.member, role_id=ROLE_PROJECT_WRITER)
        ProjectRole.objects.create(project=self.project, user=self.owner, role_id=ROLE_PROJECT_WRITER)

        self.private_query = amcattest.create_test_query(user=self.owner, project=self.project)
        self.public_query = amcattest.create_test_query(user=self.owner, project=self.project, private=False)

        self.client = Client()

    def get_url(self, query_id=None):
        url = "/api/v4/projects/{self.project.id}/querys/".format(**locals())

        if query_id is not None:
            url += str(query_id)
            url += "/"

        return url

    def json(self, url, data=None, method="get"):
        method = getattr(self.client, method)
        url += "?format=json"

        if data is not None:
            data = json.dumps(data)
            content_type = "application/json"
            result = method(url, content_type=content_type, data=data)
        else:
            result = method(url)

        status_code, results = result.status_code, json.loads(result.content)

        if "results" in results:
            return status_code, results["results"]

        return status_code, results

    def test_get_unauthenticated(self):
        """Unauthenticated users shouldn't be able to access private queries"""
        status_code, results = self.json(self.get_url())
        self.assertEqual(200, status_code)
        self.assertEqual(1, len(results))
        self.assertEqual(self.public_query.id, results[0]["id"])

    def test_get_authenticated(self):
        """Non-owners (even though they are project members) should not be able to access
        private queries of other project members."""
        self.assertTrue(self.client.login(username=self.member.username, password="test"))

        status_code, results = self.json(self.get_url())
        self.assertEqual(200, status_code)
        self.assertEqual(1, len(results))
        self.assertEqual(self.public_query.id, results[0]["id"])

    def test_get_authorised(self):
        self.assertTrue(self.client.login(username=self.owner.username, password="test"))
        status_code, results = self.json(self.get_url())
        self.assertEqual(200, status_code)
        self.assertEqual(2, len(results))

    def test_post_unauthenticated(self):
        """Anonymous users should not be able to use POST"""
        status_code, results = self.json(self.get_url(), method="post")
        self.assertEqual(401, status_code)

    def test_post_unauthorised(self):
        """Non-members should not be able to use POST"""
        self.client.login(username=self.non_member.username, password="test")
        status_code, results = self.json(self.get_url(), method="post")
        self.assertEqual(403, status_code)

    def test_post_empty(self):
        self.client.login(username=self.member.username, password="test")
        status_code, results = self.json(self.get_url(), method="post")

        self.assertEqual(400, status_code)
        self.assertIn("name", results)
        self.assertIn("parameters", results)
        self.assertEqual(2, len(results))

    def test_post(self):
        self.client.login(username=self.member.username, password="test")
        data = {"name": "test_post", "parameters": "[1, 2, 3]"}
        status_code, results = self.json(self.get_url(), data=data, method="post")

        self.assertEqual(201, status_code)
        self.assertEqual("test_post", results["name"])
        self.assertEqual(self.project.id, results["project"])
        self.assertEqual(self.member.id, results["user"])
        self.assertEqual(True, results["private"])

    def test_put_unauthorised(self):
        """PUT with an object which 'does not exist' for current user (but does in DB)
        should result in new object."""
        self.client.login(username=self.member.username, password="test")
        data = {"name": "test", "parameters": "[1,2,null]"}
        status_code, results =self.json(self.get_url(self.private_query.id), data=data, method="put")
        self.assertEqual(201, status_code)
        self.assertNotEqual(results["id"], self.private_query.id)

    def test_put_empty(self):
        self.client.login(username=self.owner.username, password="test")
        status_code, results = self.json(self.get_url(self.private_query.id), data={}, method="put")

        self.assertIn("name", results)
        self.assertIn("parameters", results)
        self.assertEqual(400, status_code)

    def test_put(self):
        self.client.login(username=self.owner.username, password="test")
        data = {"name": "blaat", "parameters": "[1, 2]"}
        status_code, results = self.json(self.get_url(self.private_query.id), data=data, method="put")

        self.assertEqual(results["name"], "blaat")
        self.assertEqual(results["parameters"], "[1, 2]")
        self.assertEqual(Query.objects.get(id=self.private_query.id).name, "blaat")
        self.assertEqual(Query.objects.get(id=self.private_query.id).parameters, [1, 2])
        self.assertEqual(200, status_code)

    def test_patch_unauthorised(self):
        self.client.login(username=self.member.username, password="test")
        status_code, results = self.json(self.get_url(self.private_query.id), data={}, method="patch")
        self.assertEqual(404, status_code)

    def test_patch_empty(self):
        self.client.login(username=self.owner.username, password="test")
        status_code, results = self.json(self.get_url(self.private_query.id), data={}, method="patch")
        self.assertEqual(200, status_code)
