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

"""Check different asynchronous workflows."""

# NOTE: The GraphQL schema is defined at the end of the file.

import asyncio
import textwrap
import threading
import uuid

import channels
import django
import graphene
import pytest

import channels_graphql_ws


@pytest.mark.asyncio
async def test_concurrent_queries(gql):
    """Check a single hanging operation does not block other ones."""

    print("Establish & initialize WebSocket GraphQL connection.")
    comm = gql(query=Query, mutation=Mutation)
    await comm.connect_and_init()

    print("Invoke a long operation which waits for the wakeup even.")
    long_op_id = await comm.send(
        type="start",
        payload={
            "query": "mutation op_name { long_op { is_ok } }",
            "variables": {},
            "operationName": "op_name",
        },
    )

    await comm.assert_no_messages()

    print("Make several fast operations to check they are not blocked by the long one.")
    for _ in range(3):
        fast_op_id = await comm.send(
            type="start",
            payload={
                "query": "query op_name { fast_op }",
                "variables": {},
                "operationName": "op_name",
            },
        )
        resp = await comm.receive(assert_id=fast_op_id, assert_type="data")
        assert resp["data"] == {"fast_op": True}
        await comm.receive(assert_id=fast_op_id, assert_type="complete")

    print("Trigger the wakeup event to let long operation finish.")
    wakeup.set()

    resp = await comm.receive(assert_id=long_op_id, assert_type="data")
    assert "errors" not in resp
    assert resp["data"] == {"long_op": {"is_ok": True}}
    await comm.receive(assert_id=long_op_id, assert_type="complete")

    print("Disconnect and wait the application to finish gracefully.")
    await comm.assert_no_messages("Unexpected message received at the end of the test!")
    await comm.finalize()


@pytest.mark.asyncio
async def test_heavy_load(gql):
    """Test that server correctly processes many simultaneous requests.

    Send many requests simultaneously and make sure all of them have
    been processed. This test reveals hanging worker threads.
    """

    print("Establish & initialize WebSocket GraphQL connection.")
    comm = gql(query=Query)
    await comm.connect_and_init()

    # NOTE: Larger numbers may lead to errors thrown from `select`.
    REQUESTS_NUMBER = 1000

    print(f"Send {REQUESTS_NUMBER} requests and check {REQUESTS_NUMBER*2} responses.")
    send_waitlist = []
    receive_waitlist = []
    expected_responses = set()
    for _ in range(REQUESTS_NUMBER):
        op_id = str(uuid.uuid4().hex)
        send_waitlist += [
            comm.send(
                id=op_id,
                type="start",
                payload={
                    "query": "query op_name { fast_op }",
                    "variables": {},
                    "operationName": "op_name",
                },
            )
        ]
        # Expect two messages for each one we have sent.
        expected_responses.add((op_id, "data"))
        expected_responses.add((op_id, "complete"))
        receive_waitlist += [comm.transport.receive(), comm.transport.receive()]

    await asyncio.wait(send_waitlist)
    responses, _ = await asyncio.wait(receive_waitlist)

    for response in (r.result() for r in responses):
        expected_responses.remove((response["id"], response["type"]))
        if response["type"] == "data":
            assert "errors" not in response["payload"]
    assert not expected_responses, "Not all expected responses received!"

    print("Disconnect and wait the application to finish gracefully.")
    await comm.assert_no_messages("Unexpected message received at the end of the test!")
    await comm.finalize()


@pytest.mark.asyncio
async def test_broadcast(gql):
    """Test that the `broadcast()` call works correctly from
    the thread which has running event loop.

    Test simply checks that there is no problem in sending
    notification messages via the `OnMessageSent` subscription
    in the asynchronous `mutate()` method of the `SendMessage`
    mutation.
    """

    print("Establish & initialize WebSocket GraphQL connection.")
    comm = gql(mutation=Mutation, subscription=Subscription)
    await comm.connect_and_init()

    print("Subscribe to GraphQL subscription.")
    sub_id = await comm.send(
        type="start",
        payload={
            "query": "subscription on_message_sent { on_message_sent { message } }",
            "variables": {},
            "operationName": "on_message_sent",
        },
    )

    await comm.assert_no_messages()

    print("Trigger the subscription by mutation to receive notification.")
    message = f"Hi! {str(uuid.uuid4().hex)}"
    msg_id = await comm.send(
        type="start",
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
    resp = await comm.receive(assert_id=msg_id, assert_type="data")
    assert resp["data"] == {"send_message": {"success": True}}
    await comm.receive(assert_id=msg_id, assert_type="complete")

    # Subscription notification.
    resp = await comm.receive(assert_id=sub_id, assert_type="data")
    data = resp["data"]["on_message_sent"]
    assert data["message"] == message, "Subscription notification contains wrong data!"

    print("Disconnect and wait the application to finish gracefully.")
    await comm.assert_no_messages("Unexpected message received at the end of the test!")
    await comm.finalize()


# ---------------------------------------------------------------- GRAPHQL BACKEND SETUP

wakeup = threading.Event()


class LongMutation(graphene.Mutation, name="LongMutationPayload"):
    """Test mutation which simply hangs until event `wakeup` is set."""

    is_ok = graphene.Boolean()

    @staticmethod
    async def mutate(root, info):
        """Sleep until `wakeup` event is set."""
        del root, info
        wakeup.wait()
        return LongMutation(True)


class SendMessage(graphene.Mutation, name="SendMessagePayload"):
    """Test mutation that simply sends message by `OnMessageSent`
    subscription."""

    class Arguments:
        """That is how mutation arguments are defined."""

        message = graphene.String(description="Some text notification.", required=True)

    success = graphene.Boolean()

    @staticmethod
    async def mutate(root, info, message):
        """Send notification and complete the mutation with
        `success` status."""
        del root, info

        await OnMessageSent.broadcast(payload={"message": message})

        return SendMessage(success=True)


class OnMessageSent(channels_graphql_ws.Subscription):
    """Test GraphQL simple subscription.

    Subscribe to receive messages.
    """

    message = graphene.String(description="Some text notification.", required=True)

    @staticmethod
    def publish(payload, info):
        """Publish query result to all subscribers."""
        del info

        return OnMessageSent(message=payload["message"])


class Mutation(graphene.ObjectType):
    """GraphQL mutations."""

    long_op = LongMutation.Field()
    send_message = SendMessage.Field()


class Query(graphene.ObjectType):
    """Root GraphQL query."""

    fast_op = graphene.Boolean()

    @staticmethod
    async def resolve_fast_op(root, info):
        """Simple instant resolver."""
        del root, info
        return True


class Subscription(graphene.ObjectType):
    """GraphQL subscriptions."""

    on_message_sent = OnMessageSent.Field()


class GraphqlWsConsumer(channels_graphql_ws.GraphqlWsConsumer):
    """Channels WebSocket consumer which provides GraphQL API."""

    schema = graphene.Schema(
        query=Query, mutation=Mutation, subscription=Subscription, auto_camelcase=False
    )


application = channels.routing.ProtocolTypeRouter(
    {
        "websocket": channels.routing.URLRouter(
            [django.urls.path("graphql/", GraphqlWsConsumer)]
        )
    }
)
