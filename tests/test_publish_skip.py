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

"""Test that it is possible to skip the broadcast from the `publish`."""

import http.cookies
import uuid

import graphene
import pytest

import channels_graphql_ws


@pytest.mark.asyncio
async def test_publish_skip(gql):
    """Test it is possible to skip the broadcast from the `publish`.

    Here we send the message to the fake chat server and make sure that
    the sender does not receive the notification about the message while
    another client receives it.

    Technically we test that returning `SKIP` from the `publish` method
    suppresses the notification.
    """

    print("Prepare the test setup: GraphQL backend classes.")

    def sessionid_from_headers(headers):
        """Extract sessionid from headers with known structure.

        In this test we know there is a single header - cookie with
        the sessionid inside. Simply extract it.
        """
        # Expected headers list looks like:
        # [(b"cookie", b"sessionid=acea05bbb40941a488d5e9a830e67354")]
        assert (
            len(headers) == 1 and headers[0][0] == b"cookie"
        ), f"Unexpected headers received: {headers}"
        cookie_header = headers[0][1].decode()
        cookie: http.cookies.SimpleCookie = http.cookies.SimpleCookie(cookie_header)
        sessionid = cookie["sessionid"].value
        return sessionid

    class SendMessage(graphene.Mutation):
        """Send message mutation."""

        is_ok = graphene.Boolean()

        class Arguments:
            """Mutation arguments."""

            message = graphene.String()

        @staticmethod
        def mutate(root, info, message):
            """Broadcast the message-author pair."""
            del root
            OnNewMessage.broadcast(
                payload={
                    "author_sessionid": sessionid_from_headers(
                        info.context.channels_scope["headers"]
                    ),
                    "message": message,
                }
            )
            return SendMessage(is_ok=True)

    class OnNewMessage(channels_graphql_ws.Subscription):
        """Triggered by `SendMessage` on every new message."""

        message = graphene.String()

        @staticmethod
        def publish(payload, info):
            """Notify all clients except the author of the message."""
            sessionid = sessionid_from_headers(info.context.channels_scope["headers"])
            if payload["author_sessionid"] == sessionid:
                return OnNewMessage.SKIP

            return OnNewMessage(message=payload["message"])

    class Subscription(graphene.ObjectType):
        """Root subscription."""

        on_new_message = OnNewMessage.Field()

    class Mutation(graphene.ObjectType):
        """Root mutation."""

        send_message = SendMessage.Field()

    print("Establish & initialize WebSocket GraphQL connections.")

    comm1 = gql(
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True},
        communicator_kwds={
            "headers": [(b"cookie", b"sessionid=%s" % uuid.uuid4().hex.encode())]
        },
    )
    await comm1.connect_and_init()

    comm2 = gql(
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True},
        communicator_kwds={
            "headers": [(b"cookie", b"sessionid=%s" % uuid.uuid4().hex.encode())]
        },
    )
    await comm2.connect_and_init()

    print("Subscribe to receive a new message notifications.")

    await comm1.send(
        msg_type="start",
        payload={
            "query": "subscription op_name { on_new_message { message } }",
            "variables": {},
            "operationName": "op_name",
        },
    )
    await comm1.assert_no_messages("Subscribe responded with a message!")

    sub_op_id = await comm2.send(
        msg_type="start",
        payload={
            "query": "subscription op_name { on_new_message { message } }",
            "variables": {},
            "operationName": "op_name",
        },
    )
    await comm2.assert_no_messages("Subscribe responded with a message!")

    print("Send a new message to check we have not received notification about it.")

    mut_op_id = await comm1.send(
        msg_type="start",
        payload={
            "query": """mutation op_name { send_message(message: "Hi!") { is_ok } }""",
            "variables": {},
            "operationName": "op_name",
        },
    )
    await comm1.receive(assert_id=mut_op_id, assert_type="data")
    await comm1.receive(assert_id=mut_op_id, assert_type="complete")

    await comm1.assert_no_messages("Self-notification happened!")

    resp = await comm2.receive(assert_id=sub_op_id, assert_type="data")
    assert resp["data"]["on_new_message"]["message"] == "Hi!"

    await comm1.assert_no_messages(
        "Unexpected message received at the end of the test!"
    )
    await comm2.assert_no_messages(
        "Unexpected message received at the end of the test!"
    )
    await comm1.finalize()
    await comm2.finalize()
