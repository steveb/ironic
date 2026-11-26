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

"""
Fake console container provider for disabling .
"""

from ironic.console.container import base


class FakeConsoleContainer(base.BaseConsoleContainer):

    provider_name = 'fake'

    # Number of clients reported by get_client_count. Tests may override
    # this to exercise the conductor's activity handling.
    client_count = 0

    def start_container(self, task, app_name, app_info):
        # return a test-net-1 address
        return '192.0.2.1', 5900

    def stop_container(self, task):
        pass

    def stop_all_containers(self):
        pass

    def is_stopped(self, task):
        return False

    def get_client_count(self, task):
        return self.client_count
