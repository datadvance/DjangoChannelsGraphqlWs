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


class Mutation(graphene.ObjectType):
    """GraphQL mutations."""

    long_op = LongMutation.Field()


class Query(graphene.ObjectType):
    """Root GraphQL query."""

    fast_op = graphene.Boolean()

    @staticmethod
    async def resolve_fast_op(root, info):
        """Simple instant resolver."""
        del root, info
        return True


class GraphqlWsConsumer(channels_graphql_ws.GraphqlWsConsumer):
    """Channels WebSocket consumer which provides GraphQL API."""

    schema = graphene.Schema(query=Query, mutation=Mutation, auto_camelcase=False)


application = channels.routing.ProtocolTypeRouter(
    {
        "websocket": channels.routing.URLRouter(
            [django.urls.path("graphql/", GraphqlWsConsumer)]
        )
    }
)
