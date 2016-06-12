# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Tests cors middleware."""

from mistral.tests.unit.api import base
from oslo_config import cfg as cfg
from oslo_middleware import cors as cors_middleware


class TestCORSMiddleware(base.APITest):
    """Provide a basic smoke test to ensure CORS middleware is active.

    The tests below provide minimal confirmation that the CORS middleware
    is active, and may be configured. For comprehensive tests, please consult
    the test suite in oslo_middleware.
    """

    def setUp(self):
        # Make sure the CORS options are registered
        cfg.CONF.register_opts(cors_middleware.CORS_OPTS, 'cors')

        # Load up our valid domain values before the application is created.
        cfg.CONF.set_override(
            "allowed_origin",
            "http://valid.example.com",
            group='cors'
        )

        # Create the application.
        super(TestCORSMiddleware, self).setUp()

    def test_valid_cors_options_request(self):
        response = self.app.options(
            '/',
            headers={
                'Origin': 'http://valid.example.com',
                'Access-Control-Request-Method': 'GET'
            }
        )

        self.assertEqual(200, response.status_code)
        self.assertIn('access-control-allow-origin', response.headers)
        self.assertEqual(
            'http://valid.example.com',
            response.headers['access-control-allow-origin']
        )

    def test_invalid_cors_options_request(self):
        response = self.app.options(
            '/',
            headers={
                'Origin': 'http://invalid.example.com',
                'Access-Control-Request-Method': 'GET'
            }
        )

        self.assertEqual(200, response.status_code)
        self.assertNotIn('access-control-allow-origin', response.headers)

    def test_valid_cors_get_request(self):
        response = self.app.get(
            '/',
            headers={
                'Origin': 'http://valid.example.com'
            }
        )

        self.assertEqual(200, response.status_code)
        self.assertIn('access-control-allow-origin', response.headers)
        self.assertEqual(
            'http://valid.example.com',
            response.headers['access-control-allow-origin']
        )

    def test_invalid_cors_get_request(self):
        response = self.app.get(
            '/',
            headers={
                'Origin': 'http://invalid.example.com'
            }
        )

        self.assertEqual(200, response.status_code)
        self.assertNotIn('access-control-allow-origin', response.headers)
