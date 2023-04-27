# Copyright (C) DATADVANCE, 2010-2023
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

"""Test the Django model automatic serialization."""

import datetime
import random
import sys
import textwrap
import uuid

import channels
import django.contrib.auth
import graphene
import graphene_django
import pytest

import channels_graphql_ws

# Use `backports-datetime-fromisoformat` to monkeypatch `datetime` cause
# in Python 3.6 it does not have `fromisoformat` we use below.
if sys.version_info < (3, 7):
    import backports.datetime_fromisoformat  # pylint: disable=import-error

    backports.datetime_fromisoformat.MonkeyPatch.patch_fromisoformat()  # pylint: disable=no-member


@pytest.mark.asyncio
async def test_models_serialization_with_nested_db_query(gql, transactional_db):
    """Test serialization with resolver that executes DB queries."""
    del transactional_db

    # Get Django user model class without referencing it directly:
    # https://docs.djangoproject.com/en/dev/topics/auth/customizing/#referencing-the-user-model
    User = django.contrib.auth.get_user_model()  # pylint: disable=invalid-name

    user_id = random.randint(0, 1023)
    username = uuid.uuid4().hex
    async_user_create = channels.db.database_sync_to_async(User.objects.create)
    user = await async_user_create(
        id=user_id,
        username=username,
    )

    class UserModel(
        graphene_django.types.DjangoObjectType,
        model=User,  # type: ignore[call-arg]
        only_fields=("username", "id"),  # type: ignore[call-arg]
    ):
        """User data."""

        id = graphene.Int(
            description="Id of a user",
            required=True,
        )
        username = graphene.String(
            description="Username of a user",
            required=True,
        )

    class SendModels(graphene.Mutation):
        """Send models to the subscriptions OnModelsReceived."""

        is_ok = graphene.Boolean()

        @staticmethod
        def mutate(root, info):
            """Send models in the `payload` to the `publish` method."""
            del root, info
            # Broadcast models inside a dictionary.
            OnModelsReceived.broadcast(payload={})
            return SendModels(is_ok=True)

    class OnModelsReceived(channels_graphql_ws.Subscription):
        """Receive the models and extract some info from it."""

        user = graphene.Field(UserModel, description="A username queried from database")

        @staticmethod
        def resolve_user(this, info):
            """Resolves user by reading it from db."""
            del this, info
            return User.objects.get(id=user_id)

        @staticmethod
        def publish(payload, info):
            """Publish models info so test can check it."""
            del payload, info
            return OnModelsReceived(
                user=user,
            )

    class Subscription(graphene.ObjectType):
        """Root subscription."""

        on_models_received = OnModelsReceived.Field()

    class Mutation(graphene.ObjectType):
        """Root mutation."""

        send_model = SendModels.Field()

    print("Establish & initialize WebSocket GraphQL connections.")

    client = gql(
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True},
    )
    await client.connect_and_init()

    print("Subscribe to receive subscription notifications.")

    sub_id = await client.send(
        msg_type="start",
        payload={
            "query": textwrap.dedent(
                """
                subscription {
                    on_models_received {
                        user { id, username }
                    }
                }
                """
            )
        },
    )

    print("Invoke mutation which sends Django model to the subscription.")

    await client.execute("mutation { send_model { is_ok } }")

    print("Receive subscription notification with models info and check it.")

    models_info = await client.receive(assert_id=sub_id)
    models_info = models_info["data"]["on_models_received"]
    assert models_info["user"]["id"] == user_id
    assert models_info["user"]["username"] == username

    await client.finalize()


@pytest.mark.asyncio
async def test_models_serialization(gql, transactional_db):
    """Test serialization of the Django model inside the `payload`."""
    del transactional_db

    # Get Django user model class without referencing it directly:
    # https://docs.djangoproject.com/en/dev/topics/auth/customizing/#referencing-the-user-model
    User = django.contrib.auth.get_user_model()  # pylint: disable=invalid-name

    user1_id = random.randint(0, 1023)
    user2_id = random.randint(1024, 2047)
    user1_name = uuid.uuid4().hex
    user2_name = uuid.uuid4().hex

    print("Prepare the test setup: GraphQL backend classes.")

    class SendModels(graphene.Mutation):
        """Send models to the subscriptions OnModelsReceived."""

        is_ok = graphene.Boolean()

        @staticmethod
        def mutate(root, info):
            """Send models in the `payload` to the `publish` method."""
            del root, info
            # Broadcast models inside a dictionary.
            OnModelsReceived.broadcast(
                payload={
                    "user1": User(id=user1_id, username=user1_name),
                    "user2": User(id=user2_id, username=user2_name),
                }
            )
            return SendModels(is_ok=True)

    class OnModelsReceived(channels_graphql_ws.Subscription):
        """Receive the models and extract some info from it."""

        user1_id = graphene.Int()
        user2_id = graphene.Int()
        user1_name = graphene.String()
        user2_name = graphene.String()
        user1_typename = graphene.String()
        user2_typename = graphene.String()

        @staticmethod
        def publish(payload, info):
            """Publish models info so test can check it."""
            del info
            user1 = payload["user1"]
            user2 = payload["user2"]
            return OnModelsReceived(
                user1_id=user1.id,
                user2_id=user2.id,
                user1_name=user1.username,
                user2_name=user2.username,
                user1_typename=str(type(user1)),
                user2_typename=str(type(user2)),
            )

    class Subscription(graphene.ObjectType):
        """Root subscription."""

        on_models_received = OnModelsReceived.Field()

    class Mutation(graphene.ObjectType):
        """Root mutation."""

        send_model = SendModels.Field()

    print("Establish & initialize WebSocket GraphQL connections.")

    client = gql(
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True},
    )
    await client.connect_and_init()

    print("Subscribe to receive subscription notifications.")

    sub_id = await client.send(
        msg_type="start",
        payload={
            "query": textwrap.dedent(
                """
                subscription {
                    on_models_received {
                        user1_id
                        user2_id
                        user1_name
                        user2_name
                        user1_typename
                        user2_typename
                    }
                }
                """
            )
        },
    )

    print("Invoke mutation which sends Django model to the subscription.")

    await client.execute("mutation { send_model { is_ok } }")

    print("Receive subscription notification with models info and check it.")

    models_info = await client.receive(assert_id=sub_id)
    models_info = models_info["data"]["on_models_received"]
    assert models_info["user1_id"] == user1_id
    assert models_info["user2_id"] == user2_id
    assert models_info["user1_name"] == user1_name
    assert models_info["user2_name"] == user2_name
    assert models_info["user1_typename"] == str(User)
    assert models_info["user2_typename"] == str(User)

    await client.finalize()


@pytest.mark.asyncio
async def test_timestamps_serialization(gql, transactional_db):
    """Test serialization of timestamps inside the `payload`.

    Check that instances of `datetime.date`, `datetime.time`, and
    `datetime.datetime` serializes properly inside the notification
    payload.

    """
    del transactional_db

    # Used to verify serialization correctness in the end of the test.
    now = datetime.datetime.utcnow()

    print("Prepare the test setup: GraphQL backend classes.")

    class SendTimestamps(graphene.Mutation):
        """Send timestamps to the subscriptions OnTimestampsReceived."""

        is_ok = graphene.Boolean()

        @staticmethod
        def mutate(root, info):
            """Send timestamps the `payload` to the `publish` method."""
            del root, info
            # Broadcast timestamp objects inside a dictionary.
            OnTimestampsReceived.broadcast(
                payload={
                    "now_date": now.date(),
                    "now_datetime": now,
                    "now_time": now.time(),
                }
            )
            return SendTimestamps(is_ok=True)

    class OnTimestampsReceived(channels_graphql_ws.Subscription):
        """Receive the timestamps and extract info from it."""

        now_date = graphene.String()
        now_date_typename = graphene.String()
        now_datetime = graphene.String()
        now_datetime_typename = graphene.String()
        now_time = graphene.String()
        now_time_typename = graphene.String()

        @staticmethod
        def publish(payload, info):
            """Publish timestamps info so test can check it."""
            del info
            return OnTimestampsReceived(
                now_date_typename=type(payload["now_date"]).__name__,
                now_date=payload["now_date"],
                now_datetime_typename=type(payload["now_datetime"]).__name__,
                now_datetime=payload["now_datetime"],
                now_time_typename=type(payload["now_time"]).__name__,
                now_time=payload["now_time"],
            )

    class Subscription(graphene.ObjectType):
        """Root subscription."""

        on_timestamps_received = OnTimestampsReceived.Field()

    class Mutation(graphene.ObjectType):
        """Root mutation."""

        send_timestamps = SendTimestamps.Field()

    print("Establish & initialize WebSocket GraphQL connections.")

    client = gql(
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True},
    )
    await client.connect_and_init()

    print("Subscribe to receive subscription notifications.")

    sub_id = await client.send(
        msg_type="start",
        payload={
            "query": textwrap.dedent(
                """
                subscription {
                    on_timestamps_received {
                        now_date
                        now_date_typename
                        now_datetime
                        now_datetime_typename
                        now_time
                        now_time_typename
                    }
                }
                """
            )
        },
    )

    print("Invoke mutation which sends timestamps to the subscription.")

    await client.execute("mutation { send_timestamps { is_ok } }")

    print("Receive subscription notification with timestamps info and check it.")

    timestamps_info = await client.receive(assert_id=sub_id)
    await client.finalize()

    timestamps_info = timestamps_info["data"]["on_timestamps_received"]
    assert timestamps_info["now_date_typename"] == "date"
    assert timestamps_info["now_date"] == str(now.date())
    assert timestamps_info["now_datetime_typename"] == "datetime"
    assert timestamps_info["now_datetime"] == str(now)
    assert timestamps_info["now_time_typename"] == "time"
    assert timestamps_info["now_time"] == str(now.time())
