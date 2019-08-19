# Copyright (C) DATADVANCE, 2010-2020
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

"""Check different asynchronous workflows."""

# NOTE: The GraphQL schema is defined at the end of the file.

from datetime import datetime
import textwrap
import time
import uuid

import graphene
import pytest

import channels_graphql_ws


@pytest.mark.asyncio
async def test_broadcast(gql):
    """Test that the asynchronous 'broadcast()' call works correctly.

    Because we cannot use sync 'broadcasts()' method in the thread
    which has running event loop.

    Test simply checks that there is no problem in sending
    notification messages via the `OnMessageSent` subscription
    in the asynchronous `mutate()` method of the `SendMessage`
    mutation.
    """

    print("Establish & initialize WebSocket GraphQL connection.")

    # Test subscription notifications order, even with disabled ordering
    # notifications must be send in the order they were broadcasted.
    settings = {"strict_ordering": False}
    client = gql(mutation=Mutation, subscription=Subscription, consumer_attrs=settings)
    await client.connect_and_init()

    print("Subscribe to GraphQL subscription.")
    sub_id = await client.send(
        msg_type="start",
        payload={
            "query": "subscription on_message_sent { on_message_sent { message } }",
            "variables": {},
            "operationName": "on_message_sent",
        },
    )

    await client.assert_no_messages()

    print("Trigger the subscription by mutation to receive notification.")
    message = f"Hi! {str(uuid.uuid4().hex)}"
    msg_id = await client.send(
        msg_type="start",
        payload={
            "query": textwrap.dedent(
                """
                mutation send_message($message: String!) {
                    send_message(message: $message) {
                        success
                    }
                }
                """
            ),
            "variables": {"message": message},
            "operationName": "send_message",
        },
    )

    # Mutation response.
    resp = await client.receive(assert_id=msg_id, assert_type="data")
    assert resp["data"] == {"send_message": {"success": True}}
    await client.receive(assert_id=msg_id, assert_type="complete")

    # Subscription notification.
    resp = await client.receive(assert_id=sub_id, assert_type="data")
    data = resp["data"]["on_message_sent"]
    assert data["message"] == message, "Subscription notification contains wrong data!"

    print("Trigger sequence of timestamps with delayed publish.")
    count = 10
    msg_id = await client.send(
        msg_type="start",
        payload={
            "query": textwrap.dedent(
                """
                mutation send_timestamps($count: Int!) {
                    send_timestamps(count: $count) {
                        success
                    }
                }
                """
            ),
            "variables": {"count": count},
            "operationName": "send_timestamps",
        },
    )

    # Mutation response.
    resp = await client.receive(assert_id=msg_id, assert_type="data")
    assert resp["data"] == {"send_timestamps": {"success": True}}
    await client.receive(assert_id=msg_id, assert_type="complete")

    timestamps = []
    for _ in range(count):
        resp = await client.receive(assert_id=sub_id, assert_type="data")
        data = resp["data"]["on_message_sent"]
        timestamps.append(data["message"])
    assert timestamps == sorted(
        timestamps
    ), "Server does not preserve messages order for subscription!"

    print("Disconnect and wait the application to finish gracefully.")
    await client.assert_no_messages(
        "Unexpected message received at the end of the test!"
    )
    await client.finalize()


# ---------------------------------------------------------------- GRAPHQL BACKEND SETUP


class SendMessage(graphene.Mutation, name="SendMessagePayload"):
    """Test mutation to send message to `OnMessageSent` subscription."""

    class Arguments:
        """That is how mutation arguments are defined."""

        message = graphene.String(description="Some text notification.", required=True)

    success = graphene.Boolean()

    @staticmethod
    async def mutate(root, info, message):
        """Send notification and return `success` status."""
        del root, info

        await OnMessageSent.broadcast(payload={"message": message})

        return SendMessage(success=True)


class SendTimestamps(graphene.Mutation, name="SendTimestampsPayload"):
    """Send monotonic timestamps by `OnMessageSent` subscription.

    Broadcast messages contains timestamp and publish delay, while
    timestamps are increasing delays otherwise are decreasing by 0.1s
    from the first to the last timestamp. Delay is executed by the
    publish callback on server, and if server does not preserve messages
    order client will get timestamps in the wrong order.
    """

    class Arguments:
        """That is how mutation arguments are defined."""

        count = graphene.Int(description="Number of timestamps to send.", required=True)

    success = graphene.Boolean()

    @staticmethod
    async def mutate(root, info, count):
        """Send increasing timestamps with decreasing delays."""

        del root, info

        for idx in range(count):
            now = datetime.fromtimestamp(time.monotonic())
            payload = dict(message=now.isoformat(), delay=(count - idx) / 10)
            await OnMessageSent.broadcast(payload=payload)

        return SendTimestamps(success=True)


class OnMessageSent(channels_graphql_ws.Subscription):
    """Test GraphQL simple subscription.

    Subscribe to receive messages.
    """

    message = graphene.String(description="Some text notification.", required=True)

    @staticmethod
    async def subscribe(payload, info):
        """This method is needed to assure `async` variant works OK."""
        del payload, info

    @staticmethod
    async def publish(payload, info):
        """Publish query result to all subscribers may be with delay."""
        del info

        time.sleep(payload.get("delay") or 0)
        return OnMessageSent(message=payload["message"])


class Mutation(graphene.ObjectType):
    """GraphQL mutations."""

    send_message = SendMessage.Field()
    send_timestamps = SendTimestamps.Field()


class Subscription(graphene.ObjectType):
    """GraphQL subscriptions."""

    on_message_sent = OnMessageSent.Field()
