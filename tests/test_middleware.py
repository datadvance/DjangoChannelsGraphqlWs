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

"""Test GraphQL middleware."""

import graphene
import graphql.pyutils
import pytest

import channels_graphql_ws


@pytest.mark.asyncio
@pytest.mark.parametrize("subprotocol", ["graphql-transport-ws", "graphql-ws"])
async def test_middleware_called_in_query(gql, subprotocol):
    """Check that middleware called during query request."""

    middleware_called = False

    def middleware(next_middleware, root, info, *args, **kwds):
        nonlocal middleware_called
        middleware_called = True
        return next_middleware(root, info, *args, **kwds)

    print("Initialize WebSocket GraphQL connection with middleware enabled.")
    client = gql(
        query=Query,
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True, "middleware": [middleware]},
        subprotocol=subprotocol,
    )
    await client.connect_and_init()

    print("Make simple query and assert that middleware function called.")
    msg_id = await client.start(query="query { ok }")
    await client.receive_next(msg_id)
    await client.receive_complete(msg_id)

    assert middleware_called, "Middleware is not called!"

    print("Disconnect and wait the application to finish gracefully.")
    await client.finalize()


@pytest.mark.asyncio
@pytest.mark.parametrize("subprotocol", ["graphql-transport-ws", "graphql-ws"])
async def test_middleware_called_in_mutation(gql, subprotocol):
    """Check that middleware called during mutation request."""

    middleware_called = False

    def middleware(next_middleware, root, info, *args, **kwds):
        nonlocal middleware_called
        middleware_called = True
        return next_middleware(root, info, *args, **kwds)

    print("Initialize WebSocket GraphQL connection with middleware enabled.")
    client = gql(
        query=Query,
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True, "middleware": [middleware]},
        subprotocol=subprotocol,
    )
    await client.connect_and_init()

    print("Make simple mutation and assert that middleware function called.")
    msg_id = await client.start(query="mutation { noop { ok } }")
    await client.receive_next(msg_id)
    await client.receive_complete(msg_id)

    assert middleware_called, "Middleware is not called!"

    print("Disconnect and wait the application to finish gracefully.")
    await client.finalize()


@pytest.mark.asyncio
@pytest.mark.parametrize("subprotocol", ["graphql-transport-ws", "graphql-ws"])
async def test_middleware_called_in_subscription(gql, subprotocol):
    """Check that middleware called during subscription processing."""

    middleware_call_counter = 0

    async def middleware(next_middleware, root, info, *args, **kwds):
        nonlocal middleware_call_counter
        middleware_call_counter += 1
        result = next_middleware(root, info, *args, **kwds)
        if graphql.pyutils.is_awaitable(result):
            result = await result
        return result

    print("Initialize WebSocket GraphQL connection with middleware enabled.")
    client = gql(
        query=Query,
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True, "middleware": [middleware]},
        subprotocol=subprotocol,
    )
    await client.connect_and_init()

    print("Subscribe to GraphQL subscription.")
    sub_id = await client.start(query="subscription { on_trigger{ ok } }")
    await client.assert_no_messages()

    # Middleware is not called during subscription initialization.
    assert (
        middleware_call_counter == 0
    ), "Middleware is not called during subscribing to the subscription!"

    print("Manually trigger the subscription.")
    await OnTrigger.broadcast()

    # Receive subscription notification to guarantee that the
    # subscription processing has finished.
    await client.receive_next(sub_id)

    # Middleware must be called two times:
    #  - to resolve "on_trigger";
    #  - to resolve "ok".
    assert (
        middleware_call_counter == 2
    ), "Middleware is not called three times for subscription!"

    print("Disconnect and wait the application to finish gracefully.")
    await client.finalize()


@pytest.mark.asyncio
@pytest.mark.parametrize("subprotocol", ["graphql-transport-ws", "graphql-ws"])
async def test_middleware_invocation_order(gql, subprotocol):
    """Check that several middleware called in a proper order."""

    middleware_invocation_log = []

    def middleware1(next_middleware, root, info, *args, **kwds):
        middleware_invocation_log.append(1)
        return next_middleware(root, info, *args, **kwds)

    def middleware2(next_middleware, root, info, *args, **kwds):
        middleware_invocation_log.append(2)
        return next_middleware(root, info, *args, **kwds)

    print("Initialize WebSocket GraphQL connection with middleware enabled.")
    client = gql(
        query=Query,
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={
            "strict_ordering": True,
            "middleware": [middleware2, middleware1],
        },
        subprotocol=subprotocol,
    )
    await client.connect_and_init()

    print("Make simple query and assert that middleware function called.")
    msg_id = await client.start(query="query { ok }")
    await client.receive_next(msg_id)
    await client.receive_complete(msg_id)

    assert middleware_invocation_log == [1, 2], "Middleware invocation order is wrong!"

    print("Disconnect and wait the application to finish gracefully.")
    await client.finalize()


# ---------------------------------------------------------------------- GRAPHQL BACKEND


# Mute Pytest for the Graphene DSL for the GraphQL setup.
# pylint: disable=arguments-differ


class OnTrigger(channels_graphql_ws.Subscription):
    """Test GraphQL subscription."""

    ok = graphene.Boolean()

    def publish(self, info):
        """Send test notification."""
        del info
        return OnTrigger(ok=True)


class Noop(graphene.Mutation):
    """Test GraphQL mutation."""

    ok = graphene.Boolean()

    def mutate(self, info):
        """Do nothing but responding with OK."""
        del info
        return Noop(ok=True)


class Subscription(graphene.ObjectType):
    """Root GraphQL subscriptions."""

    on_trigger = OnTrigger.Field()


class Mutation(graphene.ObjectType):
    """Root GraphQL mutations."""

    noop = Noop.Field()


class Query(graphene.ObjectType):
    """Root GraphQL query."""

    ok = graphene.Boolean()

    def resolve_ok(self, info):
        """Do nothing but return OK."""
        del info
        return True
