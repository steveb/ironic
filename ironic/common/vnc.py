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

"""Functions for VNC graphical console drivers and novncproxy"""

import datetime
import secrets
from urllib import parse

from oslo_log import log as logging
from oslo_utils import timeutils

from ironic.common import exception
from ironic.conf import CONF

LOG = logging.getLogger(__name__)

CONSOLE_LAST_ACTIVE = 'console_last_active'


def _parse_timestamp(value):
    # Values are written by Node.timestamp_driver_internal_info() using
    # timeutils.utcnow().isoformat(). parse_isotime() also accepts values
    # without fractional seconds, and normalize_time() returns the naive
    # UTC value used by timeutils.utcnow().
    return timeutils.normalize_time(timeutils.parse_isotime(value))


def _token_created(node):
    """Return the time the console token was created.

    :param node: the node object
    :returns: a datetime object
    :raises: NotAuthorized if the timestamp is missing or malformed
    """
    token_created = node.driver_internal_info.get(
        'novnc_secret_token_created')
    if not token_created:
        # missing token created timestamp for node
        raise exception.NotAuthorized()
    try:
        return _parse_timestamp(token_created)
    except (TypeError, ValueError):
        LOG.warning('Malformed novnc_secret_token_created for node %s: %r',
                    node.uuid, token_created)
        raise exception.NotAuthorized()


def _console_last_active(node):
    """Return the last console activity timestamp, or None.

    A malformed timestamp is ignored: it must not shorten a session which
    is otherwise valid.

    :param node: the node object
    :returns: a datetime object, or None
    """
    last_active = node.driver_internal_info.get(CONSOLE_LAST_ACTIVE)
    if not last_active:
        return None
    try:
        return _parse_timestamp(last_active)
    except (TypeError, ValueError):
        LOG.warning('Ignoring malformed %s for node %s: %r',
                    CONSOLE_LAST_ACTIVE, node.uuid, last_active)
        return None


def novnc_authorize(node):
    """Create and save a console token.

    A random token is created and is stored in the node
    ``driver_internal_info`` along with creation time.

    This is called by graphical console drivers when ``start_console`` is
    called.

    :param node: the node object
    :returns: an authorized token
    """
    token = secrets.token_urlsafe()
    node.set_driver_internal_info('novnc_secret_token', token)
    node.del_driver_internal_info(CONSOLE_LAST_ACTIVE)
    node.timestamp_driver_internal_info('novnc_secret_token_created')
    node.save()
    return token


def novnc_unauthorize(node):
    """Clear any existing console token

    :param node: the node object
    """
    node.del_driver_internal_info('novnc_secret_token')
    node.del_driver_internal_info('novnc_secret_token_created')
    node.del_driver_internal_info(CONSOLE_LAST_ACTIVE)
    node.save()


def set_console_last_active(node):
    """Record that at least one client is connected to the console.

    The caller is responsible for saving the node, typically while holding
    an exclusive lock.

    :param node: the node object
    """
    node.timestamp_driver_internal_info(CONSOLE_LAST_ACTIVE)


def novnc_validate(node, token):
    """Validate the token.

    :param node: the node object
    :param token: the token for the authorization
    :returns: The ConsoleAuthToken object if valid

    The token is valid if the token is in the database and the expires
    time has not passed.
    """
    if not token:
        raise exception.NotAuthorized()
    node_token = node.driver_internal_info.get('novnc_secret_token')
    if not node_token:
        # missing token info for node
        raise exception.NotAuthorized()

    if token != node_token:
        # token doesn't match
        raise exception.NotAuthorized()

    if token_valid_until(node) < timeutils.utcnow():
        # token has expired
        raise exception.NotAuthorized()


def token_idle_deadline(node):
    """Calculate when an active console session becomes idle.

    The deadline slides with console activity, but is not bounded by the
    hard session limit here; use token_valid_until() for that.

    :param node: the node object
    :returns: a datetime object representing the idle deadline
    :raises: NotAuthorized if no valid creation timestamp is stored
    """
    created_dt = _token_created(node)
    last_active = _console_last_active(node)
    active = max(created_dt, last_active) if last_active else created_dt
    return active + datetime.timedelta(seconds=CONF.vnc.token_timeout)


def token_hard_deadline(node):
    """Calculate the absolute deadline of a console session.

    :param node: the node object
    :returns: a datetime object, or None when the hard limit is disabled
    :raises: NotAuthorized if no valid creation timestamp is stored
    """
    if CONF.vnc.max_session_timeout <= 0:
        return None
    created_dt = _token_created(node)
    return created_dt + datetime.timedelta(
        seconds=CONF.vnc.max_session_timeout)


def token_valid_until(node):
    """Calculate when the token will expire

    :param node: the node object
    :returns: a datetime object representing expiration time
    :raises: NotAuthorized if no valid creation timestamp is stored
    """
    deadline = token_idle_deadline(node)
    hard_deadline = token_hard_deadline(node)
    if hard_deadline is not None:
        deadline = min(deadline, hard_deadline)
    return deadline


def get_console(node):
    """Get the type and connection information about the console

    :param node: the node object
    :returns: A dict containing keys 'type', 'url'
    """
    uuid = node.uuid
    base_url = CONF.vnc.public_url
    token = node.driver_internal_info.get('novnc_secret_token')
    path = parse.quote(f"websockify?node={uuid}&token={token}")
    url = f"{base_url}?path={path}"
    return {'type': 'vnc', 'url': url}
