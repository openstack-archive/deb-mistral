# Copyright 2016 NEC Corporation. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from oslo_log import log as logging
from tempest.lib import exceptions
from tempest import test

from mistral import utils
from mistral_tempest_tests.tests import base


LOG = logging.getLogger(__name__)


class ActionTestsV2(base.TestCase):

    _service = 'workflowv2'

    def get_field_value(self, body, act_name, field):
        return [body['actions'][i][field]
                for i in range(len(body['actions']))
                if body['actions'][i]['name'] == act_name][0]

    def tearDown(self):
        for act in self.client.actions:
            self.client.delete_obj('actions', act)
        self.client.actions = []

        super(ActionTestsV2, self).tearDown()

    @test.attr(type='smoke')
    def test_get_list_actions(self):
        resp, body = self.client.get_list_obj('actions')

        self.assertEqual(200, resp.status)
        self.assertNotEqual([], body['actions'])
        self.assertNotIn('next', body)

    @test.attr(type='smoke')
    def test_get_list_actions_with_pagination(self):
        resp, body = self.client.get_list_obj(
            'actions?limit=1&sort_keys=name&sort_dirs=desc'
        )

        self.assertEqual(200, resp.status)
        self.assertEqual(1, len(body['actions']))
        self.assertIn('next', body)

        name_1 = body['actions'][0].get('name')
        next = body.get('next')

        param_dict = utils.get_dict_from_string(
            next.split('?')[1],
            delimiter='&'
        )

        expected_sub_dict = {
            'limit': 1,
            'sort_keys': 'name',
            'sort_dirs': 'desc'
        }

        self.assertDictContainsSubset(expected_sub_dict, param_dict)

        # Query again using 'next' hint
        url_param = next.split('/')[-1]
        resp, body = self.client.get_list_obj(url_param)

        self.assertEqual(200, resp.status)
        self.assertEqual(1, len(body['actions']))

        name_2 = body['actions'][0].get('name')

        self.assertGreater(name_1, name_2)

    @test.attr(type='negative')
    def test_get_list_actions_nonexist_sort_dirs(self):
        context = self.assertRaises(
            exceptions.BadRequest,
            self.client.get_list_obj,
            'actions?limit=1&sort_keys=id&sort_dirs=nonexist'
        )

        self.assertIn(
            'Unknown sort direction',
            context.resp_body.get('faultstring')
        )

    @test.attr(type='negative')
    def test_get_list_actions_invalid_limit(self):
        context = self.assertRaises(
            exceptions.BadRequest,
            self.client.get_list_obj,
            'actions?limit=-1&sort_keys=id&sort_dirs=asc'
        )

        self.assertIn(
            'Limit must be positive',
            context.resp_body.get('faultstring')
        )

    @test.attr(type='negative')
    def test_get_list_actions_duplicate_sort_keys(self):
        context = self.assertRaises(
            exceptions.BadRequest,
            self.client.get_list_obj,
            'actions?limit=1&sort_keys=id,id&sort_dirs=asc,asc'
        )

        self.assertIn(
            'Length of sort_keys must be equal or greater than sort_dirs',
            context.resp_body.get('faultstring')
        )

    @test.attr(type='sanity')
    def test_create_and_delete_few_actions(self):
        resp, body = self.client.create_action('action_v2.yaml')
        self.assertEqual(201, resp.status)

        created_acts = [action['name'] for action in body['actions']]

        resp, body = self.client.get_list_obj('actions')
        self.assertEqual(200, resp.status)

        actions = [action['name'] for action in body['actions']]

        for act in created_acts:
            self.assertIn(act, actions)
            self.client.delete_obj('actions', act)

        _, body = self.client.get_list_obj('actions')
        actions = [action['name'] for action in body['actions']]

        for act in created_acts:
            self.assertNotIn(act, actions)
            self.client.actions.remove(act)

    @test.attr(type='sanity')
    def test_get_action(self):
        _, body = self.client.create_action('action_v2.yaml')
        action_name = body['actions'][0]['name']
        resp, body = self.client.get_object('actions', action_name)

        self.assertEqual(200, resp.status)
        self.assertEqual(action_name, body['name'])

    @test.attr(type='sanity')
    def test_update_action(self):
        _, body = self.client.create_action('action_v2.yaml')
        action = body['actions'][0]['name']

        act_created_at = self.get_field_value(
            body=body, act_name=action, field='created_at')

        self.assertNotIn('updated at', body['actions'])

        resp, body = self.client.update_request('actions', 'action_v2.yaml')
        self.assertEqual(200, resp.status)

        actions = [act['name'] for act in body['actions']]
        self.assertIn(action, actions)

        updated_act_created_at = self.get_field_value(
            body=body, act_name=action, field='created_at')

        self.assertEqual(act_created_at.split(".")[0], updated_act_created_at)
        self.assertTrue(all(['updated_at' in item
                             for item in body['actions']]))

    @test.attr(type='sanity')
    def test_get_action_definition(self):
        _, body = self.client.create_action('action_v2.yaml')
        act_name = body['actions'][0]['name']

        resp, body = self.client.get_definition('actions', act_name)
        self.assertEqual(200, resp.status)
        self.assertIsNotNone(body)
        self.assertIn(act_name, body)

    @test.attr(type='negative')
    def test_get_nonexistent_action(self):
        self.assertRaises(
            exceptions.NotFound,
            self.client.get_object,
            'actions', 'nonexist'
        )

    @test.attr(type='negative')
    def test_double_creation(self):
        self.client.create_action('action_v2.yaml')

        self.assertRaises(
            exceptions.Conflict,
            self.client.create_action,
            'action_v2.yaml'
        )

    @test.attr(type='negative')
    def test_create_action_invalid_def(self):
        self.assertRaises(
            exceptions.BadRequest,
            self.client.create_action,
            'wb_v2.yaml'
        )

    @test.attr(type='negative')
    def test_update_action_invalid_def(self):
        self.assertRaises(
            exceptions.BadRequest,
            self.client.update_request,
            'actions', 'wb_v2.yaml'
        )

    @test.attr(type='negative')
    def test_delete_nonexistent_action(self):
        self.assertRaises(
            exceptions.NotFound,
            self.client.delete_obj,
            'actions', 'nonexist'
        )

    @test.attr(type='negative')
    def test_delete_standard_action(self):
        self.assertRaises(
            exceptions.BadRequest,
            self.client.delete_obj,
            'actions', 'nova.servers_create'
        )
