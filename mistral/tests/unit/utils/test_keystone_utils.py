# Copyright 2015 - Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

from mistral.tests.unit import base
from mistral.utils.openstack import keystone


class KeystoneUtilsTest(base.BaseTest):
    def setUp(self):
        super(KeystoneUtilsTest, self).setUp()

        self.values = {'id': 'my_id'}

    def test_format_url_dollar_sign(self):
        url_template = "http://host:port/v1/$(id)s"

        expected = "http://host:port/v1/my_id"

        self.assertEqual(
            expected,
            keystone.format_url(url_template, self.values)
        )

    def test_format_url_percent_sign(self):
        url_template = "http://host:port/v1/%(id)s"

        expected = "http://host:port/v1/my_id"

        self.assertEqual(
            expected,
            keystone.format_url(url_template, self.values)
        )
