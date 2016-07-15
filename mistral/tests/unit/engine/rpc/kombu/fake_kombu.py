# Copyright (c) 2016 Intel Corporation
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
import mock


producer = mock.MagicMock()

producers = mock.MagicMock()
producers.__getitem__ = lambda *args, **kwargs: producer

connection = mock.MagicMock()

connections = mock.MagicMock()
connections.__getitem__ = lambda *args, **kwargs: connection


def BrokerConnection(*args, **kwargs):
    return mock.MagicMock()


def Exchange(*args, **kwargs):
    return mock.MagicMock()


def Queue(*args, **kwargs):
    return mock.MagicMock()


def Consumer(*args, **kwargs):
    return mock.MagicMock()
