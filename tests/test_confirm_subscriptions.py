#
# coding: utf-8
# Copyright (c) 2018 DATADVANCE
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

import graphene
import pytest

import channels_graphql_ws


@pytest.mark.asyncio
async def test_confirm_subscriptions(gql):
    """Test subscription confirmation messages.

    Technically here we check that `confirm_subscriptions` setting
    enables/disables sending of a subscription activation confirmation
    message.
    """

    print("Prepare the test setup: GraphQL backend classes.")

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

    print("Establish WebSocket GraphQL connections w/o a subscription confirmation.")

    comm = gql(
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True, "confirm_subscriptions": False},
    )
    await comm.gql_connect_and_init()

    print("Subscribe & check there is no subscription confirmation message.")

    sub_op_id = await comm.gql_send(
        type="start",
        payload={
            "query": "subscription op_name { on_trigger { is_ok } }",
            "operationName": "op_name",
        },
    )

    await comm.gql_assert_no_messages("Subscribe responded with a message!")

    print("Trigger the subscription.")

    mut_op_id = await comm.gql_send(
        type="start",
        payload={
            "query": """mutation op_name { trigger { is_ok } }""",
            "operationName": "op_name",
        },
    )
    await comm.gql_receive(assert_id=mut_op_id, assert_type="data")
    await comm.gql_receive(assert_id=mut_op_id, assert_type="complete")

    print("Check that subscription notification received.")

    resp = await comm.gql_receive(assert_id=sub_op_id, assert_type="data")
    assert resp == {"data": {"on_trigger": {"is_ok": True}}}

    await comm.gql_assert_no_messages(
        "Unexpected message received at the end of the test!"
    )
    await comm.gql_finalize()

    print("Establish WebSocket GraphQL connections with subscription confirmation.")

    comm = gql(
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True, "confirm_subscriptions": True},
    )
    await comm.gql_connect_and_init()

    print("Subscribe & check there is a subscription confirmation message.")

    sub_op_id = await comm.gql_send(
        type="start",
        payload={
            "query": "subscription op_name { on_trigger { is_ok } }",
            "operationName": "op_name",
        },
    )

    resp = await comm.gql_receive(assert_id=sub_op_id, assert_type="data")
    assert resp == {"data": None}

    print("Trigger the subscription.")

    mut_op_id = await comm.gql_send(
        type="start",
        payload={
            "query": """mutation op_name { trigger { is_ok } }""",
            "operationName": "op_name",
        },
    )
    await comm.gql_receive(assert_id=mut_op_id, assert_type="data")
    await comm.gql_receive(assert_id=mut_op_id, assert_type="complete")

    print("Check that subscription notification received.")

    resp = await comm.gql_receive(assert_id=sub_op_id, assert_type="data")
    assert resp["data"]["on_trigger"]["is_ok"] is True

    await comm.gql_assert_no_messages(
        "Unexpected message received at the end of the test!"
    )
    await comm.gql_finalize()
