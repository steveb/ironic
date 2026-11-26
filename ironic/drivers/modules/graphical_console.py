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

import abc
import datetime

from oslo_log import log as logging
from oslo_utils import timeutils

from ironic.common import console_factory
from ironic.common import exception
from ironic.common import metrics_utils
from ironic.common import vnc as vnc_utils
from ironic.conf import CONF
from ironic.drivers import base

LOG = logging.getLogger(__name__)

METRICS = metrics_utils.get_metrics_logger(__name__)


class GraphicalConsole(base.ConsoleInterface):

    @abc.abstractmethod
    def get_app_name(self):
        """Get the name of the app passed to the console container.

        A single console container image is expected to support all known
        graphical consoles and each implementation is referred to as an app.
        Each graphical console driver will specify what app to load in the
        container.

        :returns: String representing the app name to load in the console
                  container
        """

    def get_app_info(self, task):
        """Information required by the app to connect to the console

        :returns: dict containing app-specific values
        """
        return {}

    @METRICS.timer('GraphicalConsole.start_console')
    def start_console(self, task):
        node = task.node

        provider = console_factory.ConsoleContainerFactory().provider
        host, port = provider.start_container(
            task, self.get_app_name(), self.get_app_info(task))

        node.set_driver_internal_info('vnc_host', host)
        node.set_driver_internal_info('vnc_port', port)
        node.console_enabled = True
        vnc_utils.novnc_authorize(node)

        node.save()

    @METRICS.timer('GraphicalConsole.stop_console')
    def stop_console(self, task):
        node = task.node

        provider = console_factory.ConsoleContainerFactory().provider
        provider.stop_container(task)

        node.del_driver_internal_info('vnc_port')
        node.del_driver_internal_info('vnc_host')
        node.console_enabled = False
        vnc_utils.novnc_unauthorize(node)

    def get_console(self, task):
        """Get the type and connection information about the console."""
        return vnc_utils.get_console(task.node)

    def _session_is_current(self, task, token):
        """Check whether the inspected console session is still current.

        The periodic runs with a shared lock, and upgrade_lock() reloads
        the node, so the console may have been disabled, or disabled and
        re-enabled, in the meantime.

        :param task: A TaskManager instance.
        :param token: the novnc_secret_token observed before the lock
            upgrade
        :returns: True if the node still has the same enabled session
        """
        node = task.node
        return bool(
            node.console_enabled
            and node.driver_internal_info.get('novnc_secret_token') == token)

    def _stop_session(self, task):
        """Stop the console session under an exclusive lock.

        :param task: A TaskManager instance.
        :returns: True if the console was stopped, False if it was already
            stopped or replaced by a new session.
        """
        token = task.node.driver_internal_info.get('novnc_secret_token')
        task.upgrade_lock(purpose='expiring graphical console session')
        if not self._session_is_current(task, token):
            return False
        self.stop_console(task)
        return True

    def _expire_console_sessions(self, task):
        """Expire or extend the console session.

        This is called by a periodic task.
        :returns: True if the console was stopped
        """
        node = task.node
        provider = console_factory.ConsoleContainerFactory().provider
        LOG.debug('Checking graphical console session for node: %s with '
                  'provider %s', node.uuid, provider.provider_name)

        try:
            idle_deadline = vnc_utils.token_idle_deadline(node)
            hard_deadline = vnc_utils.token_hard_deadline(node)
        except exception.NotAuthorized:
            LOG.warning('Graphical console session for %s has invalid token '
                        'metadata, closing.', node.uuid)
            return self._stop_session(task)

        now = timeutils.utcnow()

        if hard_deadline is not None and hard_deadline <= now:
            LOG.info('Graphical console session for %s has reached its '
                     'maximum lifetime, closing.', node.uuid)
            return self._stop_session(task)

        try:
            stopped = provider.is_stopped(task)
        except Exception:
            LOG.exception('Could not determine the console container state '
                          'for node %s', node.uuid)
            return False
        if stopped:
            LOG.warning('Console container for node %s is no longer running, '
                        'stopping the console session.', node.uuid)
            return self._stop_session(task)

        window = datetime.timedelta(
            seconds=2 * CONF.vnc.expire_console_session_interval)
        if idle_deadline > now + window:
            # The session is not close to expiring, so avoid querying the
            # container on every periodic run.
            return False

        token = node.driver_internal_info.get('novnc_secret_token')
        try:
            count = provider.get_client_count(task)
        except Exception:
            LOG.exception('Could not get the console client count for node '
                          '%s', node.uuid)
            count = None

        if count is not None and count > 0:
            task.upgrade_lock(
                purpose='extending graphical console session')
            if not self._session_is_current(task, token):
                return False
            vnc_utils.set_console_last_active(task.node)
            task.node.save()
            LOG.debug('Extended graphical console session for node %s: '
                      '%s client(s) connected.', node.uuid, count)
            return False

        if idle_deadline <= now:
            LOG.info('Graphical console session for %s has been idle for '
                     'too long, closing.', node.uuid)
            return self._stop_session(task)

        return False
