#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import datetime
from unittest import mock

from oslo_config import cfg
from oslo_utils import timeutils

from ironic.common import console_factory
from ironic.common import exception
from ironic.drivers.modules import fake
from ironic.tests.unit.db import base as db_base
from ironic.tests.unit.objects import utils as obj_utils

CONF = cfg.CONF


class TestGraphicalConsole(db_base.DbTestCase):

    def setUp(self):
        super(TestGraphicalConsole, self).setUp()
        self.node = obj_utils.create_test_node(
            self.context, driver='fake-hardware')
        self.task = mock.Mock(node=self.node)
        self.console = fake.FakeGraphicalConsole()

        # Use the fake provider and make its client count controllable.
        CONF.set_override('container_provider', 'fake', group='vnc')
        console_factory.ConsoleContainerFactory._provider = None
        self.provider = console_factory.ConsoleContainerFactory().provider
        self.provider.client_count = 0
        self.addCleanup(setattr, self.provider, 'client_count', 0)

    def _set_created(self, seconds_ago):
        created = timeutils.utcnow() - datetime.timedelta(
            seconds=seconds_ago)
        self.node.set_driver_internal_info(
            'novnc_secret_token_created', created.isoformat())

    def _set_last_active(self):
        self.node.set_driver_internal_info(
            'console_last_active', timeutils.utcnow().isoformat())

    def test_start_console(self):
        self.node.set_driver_internal_info(
            'console_last_active', timeutils.utcnow().isoformat())
        self.assertFalse(self.node.console_enabled)

        self.console.start_console(self.task)
        dii = self.node.driver_internal_info

        # assert start_console has set internal info
        self.assertEqual('192.0.2.1', dii['vnc_host'])
        self.assertEqual(5900, dii['vnc_port'])
        self.assertIn('novnc_secret_token', dii)
        self.assertIn('novnc_secret_token_created', dii)
        # a new session must not inherit activity from an old one
        self.assertNotIn('console_last_active', dii)
        self.assertTrue(self.node.console_enabled)

    def test_stop_console(self):
        self.console.start_console(self.task)
        self._set_last_active()
        dii = self.node.driver_internal_info
        self.assertIn('vnc_host', dii)
        self.assertIn('vnc_port', dii)
        self.assertIn('novnc_secret_token', dii)
        self.assertIn('novnc_secret_token_created', dii)
        self.assertIn('console_last_active', dii)
        self.assertTrue(self.node.console_enabled)

        # assert stop_console has cleared internal info
        self.console.stop_console(self.task)
        self.assertNotIn('vnc_host', dii)
        self.assertNotIn('vnc_port', dii)
        self.assertNotIn('novnc_secret_token', dii)
        self.assertNotIn('novnc_secret_token_created', dii)
        self.assertNotIn('console_last_active', dii)
        self.assertFalse(self.node.console_enabled)

    def test__expire_console_sessions(self):
        self.console.start_console(self.task)
        dii = self.node.driver_internal_info

        # assert active session
        self.assertFalse(self.console._expire_console_sessions(self.task))
        self.assertTrue(self.node.console_enabled)
        self.task.upgrade_lock.assert_not_called()

        self._set_created(CONF.vnc.token_timeout + 10)

        # assert expired, console is closed
        self.assertTrue(self.console._expire_console_sessions(self.task))
        self.assertNotIn('novnc_secret_token', dii)
        self.assertFalse(self.node.console_enabled)
        self.task.upgrade_lock.assert_called_once_with(
            purpose='expiring graphical console session')

    def test__expire_console_sessions_extend(self):
        self.console.start_console(self.task)
        self._set_created(CONF.vnc.token_timeout - 100)
        self.provider.client_count = 2

        # a connected client near the idle deadline extends the session
        self.assertFalse(self.console._expire_console_sessions(self.task))

        self.assertTrue(self.node.console_enabled)
        self.assertIn('console_last_active', self.node.driver_internal_info)
        self.task.upgrade_lock.assert_called_once_with(
            purpose='extending graphical console session')

    def test__expire_console_sessions_unknown_count(self):
        self.console.start_console(self.task)
        self._set_created(CONF.vnc.token_timeout - 100)
        self.provider.client_count = None

        # an unknown count does not extend an otherwise active session
        self.assertFalse(self.console._expire_console_sessions(self.task))

        self.assertTrue(self.node.console_enabled)
        self.assertNotIn('console_last_active',
                         self.node.driver_internal_info)
        self.task.upgrade_lock.assert_not_called()

    def test__expire_console_sessions_zero_count(self):
        self.console.start_console(self.task)
        self._set_created(CONF.vnc.token_timeout - 100)
        self.provider.client_count = 0

        # no clients, but the idle deadline has not passed yet
        self.assertFalse(self.console._expire_console_sessions(self.task))

        self.assertTrue(self.node.console_enabled)
        self.task.upgrade_lock.assert_not_called()

    def test__expire_console_sessions_skips_probe(self):
        self.console.start_console(self.task)

        with mock.patch.object(self.provider, 'is_stopped', autospec=True,
                               return_value=False) as mock_stopped:
            with mock.patch.object(self.provider, 'get_client_count',
                                   autospec=True) as mock_count:
                self.assertFalse(
                    self.console._expire_console_sessions(self.task))

        # the container state is still reconciled on every run
        mock_stopped.assert_called_once_with(self.task)
        # but activity is only checked near the idle deadline
        mock_count.assert_not_called()

    def test__expire_console_sessions_hard_limit(self):
        self.config(max_session_timeout=300, group='vnc')
        self.console.start_console(self.task)
        self._set_created(310)
        self._set_last_active()

        with mock.patch.object(self.provider, 'is_stopped', autospec=True):
            with mock.patch.object(self.provider, 'get_client_count',
                                   autospec=True) as mock_count:
                self.assertTrue(
                    self.console._expire_console_sessions(self.task))

        # activity must not keep a session past the hard limit
        self.assertFalse(self.node.console_enabled)
        mock_count.assert_not_called()
        self.task.upgrade_lock.assert_called_once_with(
            purpose='expiring graphical console session')

    def test__expire_console_sessions_hard_limit_disabled(self):
        self.config(max_session_timeout=0, group='vnc')
        self.console.start_console(self.task)
        self._set_created(10000)
        self._set_last_active()

        # with no hard limit, activity keeps the session alive
        self.assertFalse(self.console._expire_console_sessions(self.task))

        self.assertTrue(self.node.console_enabled)

    def test__expire_console_sessions_invalid_metadata(self):
        self.console.start_console(self.task)
        self.node.del_driver_internal_info('novnc_secret_token_created')

        self.assertTrue(self.console._expire_console_sessions(self.task))

        self.assertFalse(self.node.console_enabled)
        self.task.upgrade_lock.assert_called_once_with(
            purpose='expiring graphical console session')

    @mock.patch.object(console_factory, 'ConsoleContainerFactory',
                       autospec=True)
    def test__expire_console_sessions_container_stopped(
            self, mock_console_factory):
        provider = mock_console_factory.return_value.provider
        provider.start_container.return_value = ('192.0.2.1', 5900)
        provider.is_stopped.return_value = True

        self.console.start_console(self.task)
        dii = self.node.driver_internal_info

        # container stopped out-of-band: the session is stopped
        self.assertTrue(self.console._expire_console_sessions(self.task))
        self.assertFalse(self.node.console_enabled)
        self.assertNotIn('vnc_host', dii)
        self.assertNotIn('novnc_secret_token', dii)
        provider.is_stopped.assert_called_once_with(self.task)
        provider.stop_container.assert_called_once_with(self.task)
        self.task.upgrade_lock.assert_called_once_with(
            purpose='expiring graphical console session')

    @mock.patch.object(console_factory, 'ConsoleContainerFactory',
                       autospec=True)
    def test__expire_console_sessions_container_not_stopped(
            self, mock_console_factory):
        provider = mock_console_factory.return_value.provider
        provider.start_container.return_value = ('192.0.2.1', 5900)
        provider.is_stopped.return_value = False

        self.console.start_console(self.task)

        # container still running: the session is left alone
        self.assertFalse(self.console._expire_console_sessions(self.task))
        self.assertTrue(self.node.console_enabled)
        self.assertIn('novnc_secret_token',
                      self.node.driver_internal_info)
        provider.is_stopped.assert_called_once_with(self.task)
        provider.get_client_count.assert_not_called()
        provider.stop_container.assert_not_called()

    @mock.patch.object(console_factory, 'ConsoleContainerFactory',
                       autospec=True)
    def test__expire_console_sessions_provider_error(
            self, mock_console_factory):
        provider = mock_console_factory.return_value.provider
        provider.start_container.return_value = ('192.0.2.1', 5900)
        provider.is_stopped.side_effect = exception.ConsoleContainerError(
            provider='fake', reason='ouch')

        self.console.start_console(self.task)

        # container state undetermined: the session is left alone and
        # the error does not propagate to the periodic task
        self.assertFalse(self.console._expire_console_sessions(self.task))
        self.assertTrue(self.node.console_enabled)
        self.assertIn('novnc_secret_token',
                      self.node.driver_internal_info)
        provider.is_stopped.assert_called_once_with(self.task)
        provider.stop_container.assert_not_called()

    def test__expire_console_sessions_client_count_error(self):
        self.console.start_console(self.task)
        self._set_created(CONF.vnc.token_timeout - 100)

        with mock.patch.object(
                self.provider, 'get_client_count', autospec=True,
                side_effect=exception.ConsoleContainerError(
                    provider='fake', reason='ouch')):
            self.assertFalse(
                self.console._expire_console_sessions(self.task))

        # the probe failure is treated as no activity
        self.assertTrue(self.node.console_enabled)
        self.assertNotIn('console_last_active',
                         self.node.driver_internal_info)
        self.task.upgrade_lock.assert_not_called()

    def test__expire_console_sessions_session_replaced(self):
        self.console.start_console(self.task)
        self._set_created(CONF.vnc.token_timeout - 100)
        self.provider.client_count = 2

        def replace_session(*args, **kwargs):
            self.node.set_driver_internal_info('novnc_secret_token', 'new')

        # simulate a concurrent disable and re-enable while upgrading
        self.task.upgrade_lock.side_effect = replace_session

        self.assertFalse(self.console._expire_console_sessions(self.task))

        # the new session must not be marked active by the old probe
        self.assertTrue(self.node.console_enabled)
        self.assertNotIn('console_last_active',
                         self.node.driver_internal_info)

    def test__expire_console_sessions_stop_replaced_session(self):
        self.console.start_console(self.task)
        self._set_created(CONF.vnc.token_timeout + 10)
        self.provider.client_count = 0

        def replace_session(*args, **kwargs):
            self.node.set_driver_internal_info('novnc_secret_token', 'new')

        # simulate a concurrent disable and re-enable while upgrading
        self.task.upgrade_lock.side_effect = replace_session

        self.assertFalse(self.console._expire_console_sessions(self.task))

        # the new session must not be stopped by the old expiry decision
        self.assertTrue(self.node.console_enabled)
