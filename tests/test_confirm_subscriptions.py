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

"""Test the `confirm_subscriptions` setting."""

# NOTE: In this file we use `strict_ordering=True` to simplify testing.

import uuid

import graphene
import pytest

import channels_graphql_ws


@pytest.mark.asyncio
@pytest.mark.parametrize("subprotocol", ["graphql-transport-ws", "graphql-ws"])
async def test_confirmation_enabled(gql, subprotocol):
    """Test subscription confirmation message received when enabled."""

    print("Establish WebSocket GraphQL connections with subscription confirmation.")

    client = gql(
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True, "confirm_subscriptions": True},
        subprotocol=subprotocol,
    )
    await client.connect_and_init()

    print("Subscribe & check there is a subscription confirmation message.")

    sub_op_id = await client.start(
        query="subscription op_name { on_trigger { is_ok } }", operation_name="op_name"
    )

    resp = await client.receive_next(sub_op_id)
    assert resp == {"data": None}

    print("Trigger the subscription.")

    mut_op_id = await client.start(
        query="mutation op_name { trigger { is_ok } }", operation_name="op_name"
    )
    await client.receive_next(mut_op_id)
    await client.receive_complete(mut_op_id)

    print("Check that subscription notification received.")

    resp = await client.receive_next(sub_op_id)
    assert resp["data"]["on_trigger"]["is_ok"] is True

    await client.assert_no_messages(
        "Unexpected message received at the end of the test!"
    )
    await client.finalize()


@pytest.mark.asyncio
@pytest.mark.parametrize("subprotocol", ["graphql-transport-ws", "graphql-ws"])
async def test_confirmation_disabled(gql, subprotocol):
    """Test subscription confirmation message absent when disabled."""

    print("Establish WebSocket GraphQL connections w/o a subscription confirmation.")

    client = gql(
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True, "confirm_subscriptions": False},
        subprotocol=subprotocol,
    )
    await client.connect_and_init()

    print("Subscribe & check there is no subscription confirmation message.")

    sub_op_id = await client.start(
        query="subscription op_name { on_trigger { is_ok } }",
        operation_name="op_name",
    )

    await client.assert_no_messages("Subscribe responded with a message!")

    print("Trigger the subscription.")

    mut_op_id = await client.start(
        query="mutation op_name { trigger { is_ok } }", operation_name="op_name"
    )
    await client.receive_next(mut_op_id)
    await client.receive_complete(mut_op_id)

    print("Check that subscription notification received.")

    resp = await client.receive_next(sub_op_id)
    assert resp == {"data": {"on_trigger": {"is_ok": True}}}

    await client.assert_no_messages(
        "Unexpected message received at the end of the test!"
    )
    await client.finalize()


@pytest.mark.asyncio
@pytest.mark.parametrize("subprotocol", ["graphql-transport-ws", "graphql-ws"])
async def test_custom_confirmation_message(gql, subprotocol):
    """Test custom confirmation message."""

    print("Establish WebSocket GraphQL connections with a custom confirmation message.")

    expected_data = uuid.uuid4().hex
    expected_error = RuntimeError(uuid.uuid4().hex)

    client = gql(
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={
            "strict_ordering": True,
            "confirm_subscriptions": True,
            "subscription_confirmation_message": {
                "data": expected_data,
                "errors": [expected_error],
            },
        },
        subprotocol=subprotocol,
    )
    await client.connect_and_init()

    print("Subscribe & check there is a subscription confirmation message.")

    sub_op_id = await client.start(
        query="subscription op_name { on_trigger { is_ok } }", operation_name="op_name"
    )

    with pytest.raises(channels_graphql_ws.GraphqlWsResponseError) as ex:
        await client.receive_next(sub_op_id)
    expected_errors = [
        {
            "message": f"{type(expected_error).__name__}: {expected_error}",
            "extensions": {"code": "RuntimeError"},
        }
    ]
    payload = ex.value.response["payload"]
    assert payload["errors"] == expected_errors, "Wrong confirmation errors received!"
    assert (
        payload["data"] == expected_data
    ), "Wrong subscription confirmation message received!"

    print("Trigger the subscription.")

    mut_op_id = await client.start(
        query="mutation op_name { trigger { is_ok } }", operation_name="op_name"
    )
    await client.receive_next(mut_op_id)
    await client.receive_complete(mut_op_id)

    print("Check that subscription notification received.")

    resp = await client.receive_next(sub_op_id)
    assert resp["data"]["on_trigger"]["is_ok"] is True

    await client.assert_no_messages(
        "Unexpected message received at the end of the test!"
    )
    await client.finalize()


# ------------------------------------------------------------------- TEST GRAPHQL SETUP


class Trigger(graphene.Mutation):
    """Trigger the subscription."""

    is_ok = graphene.Boolean()

    @staticmethod
    def mutate(root, info):
        """Trigger the subscription."""
        del root, info
        OnTrigger.broadcast()
        return Trigger(is_ok=True)


class OnTrigger(channels_graphql_ws.Subscription):
    """Simple subscription triggered by the `Trigger` mutation."""

    is_ok = graphene.Boolean()

    @staticmethod
    def publish(payload, info):
        """Send the subscription notification."""
        del payload, info
        return OnTrigger(is_ok=True)


class Subscription(graphene.ObjectType):
    """Root subscription."""

    on_trigger = OnTrigger.Field()


class Mutation(graphene.ObjectType):
    """Root mutation."""

    trigger = Trigger.Field()
