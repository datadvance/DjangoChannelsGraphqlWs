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

"""Auxiliary fixtures to simplify testing."""

import uuid

import channels
import channels.testing
import django
import graphene
import pytest

import channels_graphql_ws


# Increase default timeout to avoid errors on slow machines.
TIMEOUT = 5


@pytest.fixture
def gql_communicator():
    """Configured WebSocket communicator to the GraphQL backend.

    Fixture represents a communicator constructor which accepts the
    following arguments:

    Args:
        query: Root GraphQL query.
        mutation: Root GraphQL mutation.
        subscription: Root GraphQL subscription.
        strict_ordering: The `GraphqlWsConsumer` subclass attribute.
            See `GraphqlWsConsumer` comments for details.
        on_connect: The `GraphqlWsConsumer` subclass attribute.
            See `GraphqlWsConsumer` comments for details.
        send_keepalive_every: The `GraphqlWsConsumer` subclass
            attribute. See `GraphqlWsConsumer` comments for details.

    Used like this:
    ```
    def test_something(gql_communicator):
        comm = gql_communicator(
            query=MyQuery,                # Root GraphQL query.
            mutation=MyMutation,          # Root GraphQL mutation.
            subscription=MySubscription,  # Root GraphQL subscription.
            strict_ordering=True,
        )
        ...
    ```

    Returned communicator has many useful GraphQL-related methods, see
    the `GraphqlWsCommunicator` class below for details.
    """

    def communicator_constructor(
        query=None,
        mutation=None,
        subscription=None,
        strict_ordering=False,
        on_connect=None,
        send_keepalive_every=None,
    ):
        strict_ordering_arg = strict_ordering
        on_connect_arg = on_connect
        send_keepalive_every_arg = send_keepalive_every

        class ChannelsConsumer(channels_graphql_ws.GraphqlWsConsumer):
            """Channels WebSocket consumer which provides GraphQL API."""

            schema = graphene.Schema(
                query=query,
                mutation=mutation,
                subscription=subscription,
                auto_camelcase=False,
            )
            strict_ordering = strict_ordering_arg
            if on_connect_arg is not None:
                on_connect = on_connect_arg
            if send_keepalive_every_arg is not None:
                send_keepalive_every = send_keepalive_every_arg

        application = channels.routing.ProtocolTypeRouter(
            {
                "websocket": channels.routing.URLRouter(
                    [django.urls.path("graphql/", ChannelsConsumer)]
                )
            }
        )

        graphql_ws_communicator = GraphqlWsCommunicator(
            application=application, path="graphql/", subprotocols=["graphql-ws"]
        )

        return graphql_ws_communicator

    return communicator_constructor


class GraphqlWsCommunicator(channels.testing.WebsocketCommunicator):
    """Auxiliary communicator with extra GraphQL related methods."""

    async def gql_connect(self):
        """Establish WebSocket connection and check subprotocol."""
        connected, subprotocol = await self.connect(timeout=TIMEOUT)
        assert connected, "Could not connect to the GraphQL subscriptions WebSocket!"
        assert subprotocol == "graphql-ws", "Wrong subprotocol received!"
        return connected, subprotocol

    async def gql_init(self):
        """Initialize GraphQL connection."""
        await self.send_json_to({"type": "connection_init", "payload": ""})
        resp = await self.receive_json_from(timeout=TIMEOUT)
        assert resp["type"] == "connection_ack"

    AUTO = object()

    async def gql_send(self, id=AUTO, type=None, payload=None):
        """Send GraphQL message.

        When any argument is `None` it is excluded from the message.
        Function returns value of `id` for convenience.
        """
        if id is self.AUTO:
            id = str(uuid.uuid4().hex)
        message = {}
        message.update({"id": id} if id is not None else {})
        message.update({"type": type} if type is not None else {})
        message.update({"payload": payload} if payload is not None else {})
        await self.send_json_to(message)
        return id

    async def gql_receive(self, assert_id=None, assert_type=None):
        """Receive GraphQL message checking `id` an `type` if given."""
        response = await self.receive_json_from(timeout=TIMEOUT)
        if assert_id is not None:
            assert response["id"] == assert_id, "Response id != expected id!"
        if assert_type is not None:
            assert response["type"] == assert_type, (
                f"Type `{assert_type}` expected, but `{response['type']}` received! "
                f"Response: {response}."
            )
        return response

    async def gql_finalize(self):
        """Disconnect and wait the application to finish gracefully."""
        await self.disconnect(timeout=TIMEOUT)
        await self.wait(timeout=TIMEOUT)

    async def gql_assert_no_response(self, message=None):
        """Assure no response received."""
        assert await self.receive_nothing(), (
            f"{message}"
            if message is not None
            else f"Message received when nothing expected!"
        )
