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

"""Test that intermediate notifications skipped."""

import asyncio
from typing import List

import graphene
import pytest

import channels_graphql_ws


@pytest.mark.asyncio
@pytest.mark.parametrize("subprotocol", ["graphql-transport-ws", "graphql-ws"])
async def test_notification_queue_limit(gql, subprotocol):
    """Test it is possible to skip intermediate notifications.

    Here we start subscription which send 10 messages and server took
    1 second to process each message. All messages except the first and
    the last will be skipped by the server. Because subscription class
    sets notification_queue_limit to 1.
    """

    # Number of test notifications to send.
    msgs_count = 100
    msg_proc_delay = 0.001

    print("Prepare the test setup: GraphQL backend classes.")

    class SendMessages(graphene.Mutation):
        """Send message mutation."""

        is_ok = graphene.Boolean()

        @staticmethod
        def mutate(root, info):
            """Broadcast many messages."""
            del root, info
            for idx in range(msgs_count):
                OnNewMessage.broadcast(payload={"message": idx})
            return SendMessages(is_ok=True)

    class OnNewMessage(channels_graphql_ws.Subscription):
        """Triggered by `SendMessage` on every new message."""

        # Leave only the last message in the server queue.
        notification_queue_limit = 1

        message = graphene.Int()

        @staticmethod
        async def publish(payload, info):
            """Notify all clients except the author of the message."""
            del info
            # Emulate server high load. It is bad to use sleep in the
            # tests but here it seems ok. If test is running within
            # high load builder it will behave the same and skip
            # notifications it is unable to process.
            await asyncio.sleep(msg_proc_delay)
            return OnNewMessage(message=payload["message"])

    class Subscription(graphene.ObjectType):
        """Root subscription."""

        on_new_message = OnNewMessage.Field()

    class Mutation(graphene.ObjectType):
        """Root mutation."""

        send_messages = SendMessages.Field()

    print("Establish & initialize WebSocket GraphQL connections.")

    comm = gql(
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True},
        subprotocol=subprotocol,
    )
    await comm.connect_and_init()

    print("Trigger notifications.")

    await comm.send(
        msg_type="subscribe" if subprotocol == "graphql-transport-ws" else "start",
        payload={
            "query": "subscription op_name { on_new_message { message } }",
            "variables": {},
            "operationName": "op_name",
        },
    )

    mut_op_id = await comm.send(
        msg_type="subscribe" if subprotocol == "graphql-transport-ws" else "start",
        payload={
            "query": """mutation op_name { send_messages { is_ok } }""",
            "variables": {},
            "operationName": "op_name",
        },
    )
    await comm.receive(
        assert_id=mut_op_id,
        assert_type="next" if subprotocol == "graphql-transport-ws" else "data",
    )
    await comm.receive(assert_id=mut_op_id, assert_type="complete")

    # Here we store ids of processed notifications.
    received_ids: List[int] = []

    while True:
        msg = await comm.receive(raw_response=False)
        print("Received message:", msg)
        received_ids.append(msg["data"]["on_new_message"]["message"])
        if msg["data"]["on_new_message"]["message"] == msgs_count - 1:
            break

    await comm.finalize()
    print("Received messages", received_ids)

    # Check that there is always the first and the last message.
    # Make sure that we received not all of them
    assert received_ids[0] == 0, "First message ID != 0!"
    assert received_ids[-1] == msgs_count - 1, f"Last message ID != {msgs_count - 1}!"
    assert len(received_ids) < msgs_count, "No messages skipped!"
