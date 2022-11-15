# Copyright (C) DATADVANCE, 2010-2022
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

"""Test that intermediate notifications skipped."""

import time

import graphene
import pytest

import channels_graphql_ws


@pytest.mark.asyncio
async def test_notification_queue_limit(gql):
    """Test it is possible to skip intermediate notifications.

    Here we start subscription which send 10 messages and server took
    1 second to process each message. All messages except the first and
    the last will be skipped by the server. Because subscription class
    sets notification_queue_limit to 1.
    """

    print("Prepare the test setup: GraphQL backend classes.")

    class SendMessages(graphene.Mutation):
        """Send message mutation."""

        is_ok = graphene.Boolean()

        @staticmethod
        def mutate(root, info):
            """Broadcast 10 messages."""
            del root, info
            for idx in range(10):
                OnNewMessage.broadcast(payload={"message": str(idx)})
            return SendMessages(is_ok=True)

    class OnNewMessage(channels_graphql_ws.Subscription):
        """Triggered by `SendMessage` on every new message."""

        # Leave only the last message in the server queue.
        notification_queue_limit = 1

        message = graphene.String()

        @staticmethod
        def publish(payload, info):
            """Notify all clients except the author of the message."""
            del info
            # Emulate server high load. It is bad to use sleep in the
            # tests but here it seems ok. If test is running within
            # high load builder it will behave the same and skip
            # notifications it is unable to process.
            time.sleep(1)
            return OnNewMessage(message=payload["message"])

    class Subscription(graphene.ObjectType):
        """Root subscription."""

        on_new_message = OnNewMessage.Field()

    class Mutation(graphene.ObjectType):
        """Root mutation."""

        send_messages = SendMessages.Field()

    print("Establish & initialize WebSocket GraphQL connections.")

    comm1 = gql(
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True},
    )
    await comm1.connect_and_init()

    comm2 = gql(
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True},
    )
    await comm2.connect_and_init()

    print("Subscribe to receive a new message notifications.")

    sub_op_id = await comm2.send(
        msg_type="start",
        payload={
            "query": "subscription op_name { on_new_message { message } }",
            "variables": {},
            "operationName": "op_name",
        },
    )
    await comm2.assert_no_messages("Subscribe responded with a message!")

    print("Start sending notifications.")

    mut_op_id = await comm1.send(
        msg_type="start",
        payload={
            "query": """mutation op_name { send_messages { is_ok } }""",
            "variables": {},
            "operationName": "op_name",
        },
    )
    await comm1.receive(assert_id=mut_op_id, assert_type="data")
    await comm1.receive(assert_id=mut_op_id, assert_type="complete")

    await comm1.assert_no_messages("Self-notification happened!")

    # Client will receive only the first and the last notifications.
    resp = await comm2.receive(assert_id=sub_op_id, assert_type="data")
    assert resp["data"]["on_new_message"]["message"] == "0"

    resp = await comm2.receive(assert_id=sub_op_id, assert_type="data")
    assert resp["data"]["on_new_message"]["message"] == "9"

    await comm1.assert_no_messages(
        "Unexpected message received at the end of the test!"
    )
    await comm2.assert_no_messages(
        "Unexpected message received at the end of the test!"
    )
    await comm1.finalize()
    await comm2.finalize()
