# Copyright 2010 New Relic, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
from uuid import uuid4

import pytest
from testing_support.db_settings import valkey_settings
from testing_support.fixture.event_loop import event_loop as loop
from testing_support.util import instance_hostname
from testing_support.validators.validate_transaction_metrics import validate_transaction_metrics

from newrelic.api.background_task import background_task

# Settings
DB_SETTINGS = valkey_settings()[0]

# Metrics for publish test
datastore_all_metric_count = 5

_base_scoped_metrics = [("Datastore/operation/Valkey/publish", 3)]


_base_scoped_metrics.append(("Datastore/operation/Valkey/client_setinfo", 2))

_base_rollup_metrics = [
    ("Datastore/all", datastore_all_metric_count),
    ("Datastore/allOther", datastore_all_metric_count),
    ("Datastore/Valkey/all", datastore_all_metric_count),
    ("Datastore/Valkey/allOther", datastore_all_metric_count),
    ("Datastore/operation/Valkey/publish", 3),
    (
        f"Datastore/instance/Valkey/{instance_hostname(DB_SETTINGS['host'])}/{DB_SETTINGS['port']}",
        datastore_all_metric_count,
    ),
]
_base_rollup_metrics.append(("Datastore/operation/Valkey/client_setinfo", 2))


# Metrics for connection pool test

_base_pool_scoped_metrics = [
    ("Datastore/operation/Valkey/get", 1),
    ("Datastore/operation/Valkey/set", 1),
    ("Datastore/operation/Valkey/client_list", 1),
]

_base_pool_rollup_metrics = [
    ("Datastore/all", 3),
    ("Datastore/allOther", 3),
    ("Datastore/Valkey/all", 3),
    ("Datastore/Valkey/allOther", 3),
    ("Datastore/operation/Valkey/get", 1),
    ("Datastore/operation/Valkey/set", 1),
    ("Datastore/operation/Valkey/client_list", 1),
    (f"Datastore/instance/Valkey/{instance_hostname(DB_SETTINGS['host'])}/{DB_SETTINGS['port']}", 3),
]


# Tests


@pytest.fixture
def client(loop):
    import valkey.asyncio

    return loop.run_until_complete(valkey.asyncio.Valkey(host=DB_SETTINGS["host"], port=DB_SETTINGS["port"], db=0))


@pytest.fixture
def client_pool(loop):
    import valkey.asyncio

    connection_pool = valkey.asyncio.ConnectionPool(host=DB_SETTINGS["host"], port=DB_SETTINGS["port"], db=0)
    return loop.run_until_complete(valkey.asyncio.Valkey(connection_pool=connection_pool))


@validate_transaction_metrics(
    "test_asyncio:test_async_connection_pool",
    scoped_metrics=_base_pool_scoped_metrics,
    rollup_metrics=_base_pool_rollup_metrics,
    background_task=True,
)
@background_task()
def test_async_connection_pool(client_pool, loop):
    async def _test_async_pool(client_pool):
        await client_pool.set("key1", "value1")
        await client_pool.get("key1")
        await client_pool.execute_command("CLIENT", "LIST")

    loop.run_until_complete(_test_async_pool(client_pool))


@validate_transaction_metrics("test_asyncio:test_async_pipeline", background_task=True)
@background_task()
def test_async_pipeline(client, loop):
    async def _test_pipeline(client):
        async with client.pipeline(transaction=True) as pipe:
            await pipe.set("key1", "value1")
            await pipe.execute()

    loop.run_until_complete(_test_pipeline(client))


@validate_transaction_metrics(
    "test_asyncio:test_async_pubsub",
    scoped_metrics=_base_scoped_metrics,
    rollup_metrics=_base_rollup_metrics,
    background_task=True,
)
@background_task()
def test_async_pubsub(client, loop):
    messages_received = []
    message_received = asyncio.Event()

    channel_1 = f"channel:{uuid4()}"
    channel_2 = f"channel:{uuid4()}"

    async def reader(pubsub):
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True)
            if message:
                message_received.set()
                messages_received.append(message["data"].decode())
                if message["data"].decode() == "NOPE":
                    break

    async def _publish(client, channel, message):
        """Publish a message and wait for the reader to receive it."""
        await client.publish(channel, message)
        await asyncio.wait_for(message_received.wait(), timeout=10)
        message_received.clear()

    async def _test_pubsub():
        async with client.pubsub() as pubsub:
            await pubsub.psubscribe(channel_1, channel_2)

            future = asyncio.create_task(reader(pubsub))

            await _publish(client, channel_1, "Hello")
            await _publish(client, channel_2, "World")
            await _publish(client, channel_1, "NOPE")

            await future

    loop.run_until_complete(_test_pubsub())
    assert messages_received == ["Hello", "World", "NOPE"]
