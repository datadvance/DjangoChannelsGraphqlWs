#
# coding: utf-8
# Copyright (c) 2020 DATADVANCE
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

import random
import textwrap
import uuid

import django.contrib.auth
import graphene
import pytest

import channels_graphql_ws


@pytest.mark.asyncio
async def test_serialization(gql, transactional_db):
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
        """Send models to the subscriptions `OnModelsReceived`."""

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

    comm = gql(
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True},
    )
    await comm.connect_and_init()

    print("Subscribe to receive subscription notifications.")

    sub_id = await comm.send(
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

    await comm.execute("mutation { send_model { is_ok } }")

    print("Receive subscription notification with models info and check it.")

    models_info = await comm.receive(assert_id=sub_id)
    models_info = models_info["data"]["on_models_received"]
    assert models_info["user1_id"] == user1_id
    assert models_info["user2_id"] == user2_id
    assert models_info["user1_name"] == user1_name
    assert models_info["user2_name"] == user2_name
    assert models_info["user1_typename"] == str(User)
    assert models_info["user2_typename"] == str(User)

    await comm.finalize()
