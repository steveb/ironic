#   Copyright 2025 Red Hat, Inc.
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

import datetime

from oslo_config import cfg
from oslo_utils import timeutils

from ironic.common import exception
from ironic.common import vnc as vnc_utils
from ironic.tests.unit.db import base as db_base
from ironic.tests.unit.objects import utils as obj_utils

CONF = cfg.CONF


class VncTestCase(db_base.DbTestCase):

    def setUp(self):
        super(VncTestCase, self).setUp()
        self.node = obj_utils.create_test_node(self.context)

    def test_novnc_authorize(self):
        then = timeutils.utcnow()

        vnc_utils.novnc_authorize(self.node)

        # assert token and timestamp were created
        self.assertIsNotNone(
            self.node.driver_internal_info['novnc_secret_token'])
        created = self.node.driver_internal_info['novnc_secret_token_created']
        self.assertIsNotNone(created)
        created_dt = datetime.datetime.strptime(created,
                                                '%Y-%m-%dT%H:%M:%S.%f')
        self.assertLessEqual(then, created_dt)

    def test_novnc_authorize_clears_last_active(self):
        self.node.set_driver_internal_info(
            'console_last_active', timeutils.utcnow().isoformat())

        vnc_utils.novnc_authorize(self.node)

        # a new session must not inherit activity from a previous one
        self.assertNotIn('console_last_active',
                         self.node.driver_internal_info)

    def test_novnc_unauthorize(self):
        vnc_utils.novnc_authorize(self.node)
        self.node.set_driver_internal_info(
            'console_last_active', timeutils.utcnow().isoformat())
        self.assertIn('novnc_secret_token', self.node.driver_internal_info)
        self.assertIn('novnc_secret_token_created',
                      self.node.driver_internal_info)
        self.assertIn('console_last_active',
                      self.node.driver_internal_info)

        vnc_utils.novnc_unauthorize(self.node)

        # assert token and timestamps were removed
        self.assertNotIn('novnc_secret_token', self.node.driver_internal_info)
        self.assertNotIn('novnc_secret_token_created',
                         self.node.driver_internal_info)
        self.assertNotIn('console_last_active',
                         self.node.driver_internal_info)

    def test_set_console_last_active(self):
        then = timeutils.utcnow()

        vnc_utils.set_console_last_active(self.node)

        last_active = self.node.driver_internal_info['console_last_active']
        parsed = timeutils.normalize_time(
            timeutils.parse_isotime(last_active))
        self.assertLessEqual(then, parsed)

    def test_novnc_validate(self):

        vnc_utils.novnc_authorize(self.node)
        token = self.node.driver_internal_info['novnc_secret_token']

        # assert successful validate
        self.assertIsNone(vnc_utils.novnc_validate(self.node, token))

        # assert wrong token
        self.assertRaises(exception.NotAuthorized, vnc_utils.novnc_validate,
                          self.node, 'wrong token')

        vnc_utils.novnc_unauthorize(self.node)
        # assert unauthorized
        self.assertRaises(exception.NotAuthorized, vnc_utils.novnc_validate,
                          self.node, token)

    def test_novnc_validate_expired(self):

        vnc_utils.novnc_authorize(self.node)
        token = self.node.driver_internal_info['novnc_secret_token']

        # assert successful validate
        self.assertIsNone(vnc_utils.novnc_validate(self.node, token))

        # set the created date to one second before the timeout
        now = timeutils.utcnow()
        timeout = CONF.vnc.token_timeout
        time_delta = datetime.timedelta(seconds=timeout + 1)
        then = now - time_delta
        self.node.set_driver_internal_info('novnc_secret_token_created',
                                           then.isoformat())

        # assert expired token
        self.assertRaises(exception.NotAuthorized, vnc_utils.novnc_validate,
                          self.node, token)

    def test_novnc_validate_hard_limit(self):
        self.config(max_session_timeout=300, group='vnc')

        vnc_utils.novnc_authorize(self.node)
        token = self.node.driver_internal_info['novnc_secret_token']

        now = timeutils.utcnow()
        created = now - datetime.timedelta(seconds=400)
        self.node.set_driver_internal_info('novnc_secret_token_created',
                                           created.isoformat())
        self.node.set_driver_internal_info('console_last_active',
                                           now.isoformat())

        # recent activity must not override the hard session limit
        self.assertRaises(exception.NotAuthorized, vnc_utils.novnc_validate,
                          self.node, token)

    def test_novnc_validate_last_active(self):
        vnc_utils.novnc_authorize(self.node)
        token = self.node.driver_internal_info['novnc_secret_token']

        now = timeutils.utcnow()
        created = now - datetime.timedelta(
            seconds=CONF.vnc.token_timeout + 100)
        self.node.set_driver_internal_info('novnc_secret_token_created',
                                           created.isoformat())
        self.node.set_driver_internal_info('console_last_active',
                                           now.isoformat())

        # an established session is valid after the original token timeout
        self.assertIsNone(vnc_utils.novnc_validate(self.node, token))

    def test_token_valid_until(self):
        now = timeutils.utcnow()
        self.node.set_driver_internal_info('novnc_secret_token_created',
                                           now.isoformat())
        timeout = CONF.vnc.token_timeout
        time_delta = datetime.timedelta(seconds=timeout)

        # assert that the valid until date is exactly timeout after now
        self.assertEqual(now + time_delta,
                         vnc_utils.token_valid_until(self.node))

    def test_token_idle_deadline_last_active(self):
        now = timeutils.utcnow()
        last_active = now + datetime.timedelta(seconds=100)
        self.node.set_driver_internal_info('novnc_secret_token_created',
                                           now.isoformat())
        self.node.set_driver_internal_info('console_last_active',
                                           last_active.isoformat())

        self.assertEqual(
            last_active + datetime.timedelta(
                seconds=CONF.vnc.token_timeout),
            vnc_utils.token_idle_deadline(self.node))

    def test_token_idle_deadline_last_active_before_created(self):
        now = timeutils.utcnow()
        last_active = now - datetime.timedelta(seconds=100)
        self.node.set_driver_internal_info('novnc_secret_token_created',
                                           now.isoformat())
        self.node.set_driver_internal_info('console_last_active',
                                           last_active.isoformat())

        # activity before creation must not shorten the session
        self.assertEqual(
            now + datetime.timedelta(seconds=CONF.vnc.token_timeout),
            vnc_utils.token_idle_deadline(self.node))

    def test_token_valid_until_malformed_last_active(self):
        now = timeutils.utcnow()
        self.node.set_driver_internal_info('novnc_secret_token_created',
                                           now.isoformat())
        self.node.set_driver_internal_info('console_last_active',
                                           'not-a-timestamp')

        self.assertEqual(
            now + datetime.timedelta(seconds=CONF.vnc.token_timeout),
            vnc_utils.token_valid_until(self.node))

    def test_token_hard_deadline(self):
        self.config(max_session_timeout=300, group='vnc')
        now = timeutils.utcnow()
        self.node.set_driver_internal_info('novnc_secret_token_created',
                                           now.isoformat())

        self.assertEqual(
            now + datetime.timedelta(seconds=300),
            vnc_utils.token_hard_deadline(self.node))

    def test_token_hard_deadline_disabled(self):
        self.config(max_session_timeout=0, group='vnc')
        now = timeutils.utcnow()
        self.node.set_driver_internal_info('novnc_secret_token_created',
                                           now.isoformat())

        self.assertIsNone(vnc_utils.token_hard_deadline(self.node))

    def test_token_valid_until_hard_limit(self):
        self.config(max_session_timeout=500, group='vnc')
        now = timeutils.utcnow()
        created = now - datetime.timedelta(seconds=100)
        self.node.set_driver_internal_info('novnc_secret_token_created',
                                           created.isoformat())
        self.node.set_driver_internal_info('console_last_active',
                                           now.isoformat())

        # the hard limit is sooner than the idle deadline
        self.assertEqual(
            created + datetime.timedelta(seconds=500),
            vnc_utils.token_valid_until(self.node))

    def test_token_valid_until_hard_limit_disabled(self):
        self.config(max_session_timeout=0, group='vnc')
        now = timeutils.utcnow()
        created = now - datetime.timedelta(seconds=1000)
        self.node.set_driver_internal_info('novnc_secret_token_created',
                                           created.isoformat())
        self.node.set_driver_internal_info('console_last_active',
                                           now.isoformat())

        # with no hard limit, activity extends the session indefinitely
        self.assertEqual(
            now + datetime.timedelta(seconds=CONF.vnc.token_timeout),
            vnc_utils.token_valid_until(self.node))

    def test_token_valid_until_missing_created(self):
        self.assertRaises(exception.NotAuthorized,
                          vnc_utils.token_valid_until, self.node)

    def test_token_valid_until_malformed_created(self):
        self.node.set_driver_internal_info('novnc_secret_token_created',
                                           'not-a-timestamp')

        self.assertRaises(exception.NotAuthorized,
                          vnc_utils.token_valid_until, self.node)

    def test_get_console(self):
        self.node.set_driver_internal_info('novnc_secret_token', 'asdf')
        uuid = self.node.uuid
        CONF.set_override('public_url', 'http://192.0.2.1:6090/vnc_auto.html',
                          group='vnc')

        # assert expected console URL
        self.assertEqual({
            'type': 'vnc',
            'url': 'http://192.0.2.1:6090/vnc_auto.html?'
                   f'path=websockify%3Fnode%3D{uuid}%26token%3Dasdf'
        }, vnc_utils.get_console(self.node))
