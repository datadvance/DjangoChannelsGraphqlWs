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

"""Check different error cases."""

# NOTE: In this file we use `strict_ordering=True` to simplify testing.

import uuid

import graphene
import pytest

import channels_graphql_ws


@pytest.mark.asyncio
@pytest.mark.parametrize("subprotocol", ["graphql-transport-ws", "graphql-ws"])
async def test_syntax_error(gql, subprotocol):
    """Test that server respond correctly when syntax error(s) happen.

    Check that server send errors in response, when there are a syntax
    error in the request.
    """

    print("Establish & initialize WebSocket GraphQL connection.")
    client = gql(query=Query, subprotocol=subprotocol)
    await client.connect_and_init()

    print(
        "Check that query syntax error leads to the `data` response "
        "with `errors` array."
    )

    msg_id = await client.start(query="This produces a syntax error!")
    errors, data = await client.receive_error(msg_id)

    assert data is None
    assert len(errors) == 1, "Single error expected!"
    assert (
        "message" in errors[0] and "locations" in errors[0]
    ), "Response missing mandatory fields!"
    assert errors[0]["locations"] == [{"line": 1, "column": 1}]
    if subprotocol == "graphql-ws":
        await client.receive_complete(msg_id)

    print("Check multiple errors in the `data` message.")
    msg_id = await client.start(
        query="""
                query { projects { path wrong_field } }
                query a { projects }
                { wrong_name }
                """
    )
    errors, data = await client.receive_error(msg_id)
    assert data is None
    assert len(errors) == 5, f"Five errors expected, but {len(errors)} errors received!"
    assert errors[0]["message"] == errors[3]["message"]
    assert "locations" in errors[2], "The `locations` field expected"
    assert "locations" in errors[4], "The `locations` field expected"
    if subprotocol == "graphql-ws":
        await client.receive_complete(msg_id)

    print("Disconnect and wait the application to finish gracefully.")
    await client.finalize()


@pytest.mark.asyncio
@pytest.mark.parametrize("subprotocol", ["graphql-transport-ws", "graphql-ws"])
async def test_resolver_error(gql, subprotocol):
    """Test that server responds correctly when error in resolver.

    Check that server responds with message of type `next`/`data` with
    `errors` array , when the exception in a resolver was raised.
    """

    print("Establish & initialize WebSocket GraphQL connection.")
    client = gql(query=Query, subprotocol=subprotocol)
    await client.connect_and_init()

    print(
        "Check that syntax error leads to the `next`/`data`"
        "response with `errors` array."
    )
    msg_id = await client.start(
        query="query op_name { value(issue_error: true) }", operation_name="op_name"
    )
    with pytest.raises(channels_graphql_ws.GraphqlWsResponseError) as exc_info:
        await client.receive_next(msg_id)
    payload = exc_info.value.response["payload"]
    assert payload["data"]["value"] is None
    assert len(payload["errors"]) == 1, "Single error expected!"
    assert payload["errors"][0]["message"] == Query.VALUE
    assert "locations" in payload["errors"][0]
    assert (
        "extensions" not in payload["errors"][0]
    ), "For syntax error there should be no 'extensions'."
    await client.receive_complete(msg_id)

    print("Disconnect and wait the application to finish gracefully.")
    await client.assert_no_messages()
    await client.finalize()


@pytest.mark.asyncio
async def test_connection_init_timeout_error_graphql_transport_ws(gql):
    """Test how server works with `connection_init_wait_timeout`.

    Server must close WebSocket connection with code 4408 if connection
    was not initialized (client don't send `connection_init` message)
    after `connection_init_wait_timeout` seconds.
    """

    print("Establish WebSocket GraphQL connection.")
    client = gql(
        consumer_attrs={"strict_ordering": True, "connection_init_wait_timeout": 3},
    )
    await client.connect_and_init(connect_only=True)

    print(
        "Wait until server close connection because client don't send"
        "`connection_init` message within 3 seconds."
    )
    await client.wait_disconnect(assert_code=4408)

    print("Disconnect and wait the application to finish gracefully.")
    await client.assert_no_messages()
    await client.finalize()


@pytest.mark.asyncio
async def test_connection_unauthorized_error_graphql_transport_ws(gql):
    """Test how server handles `subscribe` before `connection_ack`.

    Server must close WebSocket connection with code 4401 if client
    trying to subscribe before connection acknowledgment (before server
    send `connection_ack` message).
    """

    print("Establish WebSocket GraphQL connection.")
    client = gql(
        query=Query,
        consumer_attrs={"strict_ordering": True},
    )
    await client.connect_and_init(connect_only=True)

    print("Send `subscribe` message.")
    await client.start(query="query op_name { value }", operation_name="op_name")
    await client.wait_disconnect(assert_code=4401)

    print("Disconnect and wait the application to finish gracefully.")
    await client.assert_no_messages()
    await client.finalize()


@pytest.mark.asyncio
@pytest.mark.parametrize("subprotocol", ["graphql-transport-ws", "graphql-ws"])
async def test_wrong_message_type_error(gql, subprotocol):
    """Test how server handles request with wrong message type.

    If GraphqlWsConsumer working on `graphql-transport-ws` subprotocol,
    server must close WebSocket connection with code 4400 when client
    send request with wrong message type.
    If GraphqlWsConsumer working on `graphql-ws` subprotocol server must
    send `error` message.

    """

    print("Establish WebSocket GraphQL connection.")
    client = gql(consumer_attrs={"strict_ordering": True}, subprotocol=subprotocol)
    await client.connect_and_init(connect_only=True)

    print("Send message with wrong type.")
    msg_id = await client.send_raw_message(
        {"type": "wrong_type__(ツ)_/¯", "variables": {}, "operationName": ""}
    )
    if subprotocol == "graphql-transport-ws":
        await client.wait_disconnect(assert_code=4400)
    else:
        with pytest.raises(channels_graphql_ws.GraphqlWsResponseError) as exc_info:
            await client.receive(assert_id=msg_id, assert_type="error")
        payload = exc_info.value.response["payload"]
        assert len(payload["errors"]) == 1, "Multiple errors received instead of one!"
        assert isinstance(payload["errors"][0], dict), "Error must be of dict type!"
        assert isinstance(
            payload["errors"][0]["message"], str
        ), "Error's message  must be of str type!"
        assert (
            payload["errors"][0]["extensions"]["code"] == "Exception"
        ), "Error must have 'code' field."

    print("Disconnect and wait the application to finish gracefully.")
    await client.assert_no_messages()
    await client.finalize()


@pytest.mark.asyncio
async def test_many_init_requests_error_graphql_transport_ws(gql):
    """Test how server handles more than 1 `connection_init` messages.

    Server must close WebSocket connection with code 4429 if client send
    more than 1 `connection_init` messages.
    """

    print("Establish WebSocket GraphQL connection.")
    client = gql(
        consumer_attrs={"strict_ordering": True},
    )
    await client.connect_and_init()

    print("Send second `connection_init` message.")
    await client.send_raw_message({"type": "connection_init", "payload": ""})
    await client.wait_disconnect(assert_code=4429)

    print("Disconnect and wait the application to finish gracefully.")
    await client.assert_no_messages()
    await client.finalize()


@pytest.mark.asyncio
async def test_subscriber_already_exists_error_graphql_transport_ws(gql):
    """Test how server handles subscription with same id.

    Server must close WebSocket connection with code 4409 if client sent
    `subscribe` message with id of subscription that already exists.
    """

    print("Establish WebSocket GraphQL connection.")
    client = gql(
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True},
    )
    await client.connect_and_init()

    print("Send two `subscribe` messages with same id.")
    for _ in range(2):
        await client.start(
            query="""
                    subscription { test_subscription (switch: "NONE\") { ok } }
                    """,
            msg_id="NOT_UNIQUE_ID",
        )
    await client.wait_disconnect(assert_code=4409)

    print("Disconnect and wait the application to finish gracefully.")
    await client.assert_no_messages()
    await client.finalize()


@pytest.mark.asyncio
@pytest.mark.parametrize("subprotocol", ["graphql-transport-ws", "graphql-ws"])
async def test_connection_error(gql, subprotocol):
    """Test that server disconnects user when `on_connect` raises error.

    If GraphqlWsConsumer working on `graphql-transport-ws` subprotocol,
    when method `on_connect` raises `RuntimeError` server must close
    connection immediately with code 4403.
    If GraphqlWsConsumer working on `graphql-ws` subprotocol
    server must:
        1. Send proper error message - with type `connection_error`.
        2. Disconnect user.
    """

    print("Establish WebSocket GraphQL connection.")

    def on_connect(self, payload):
        del self, payload
        raise RuntimeError("Connection rejected!")

    client = gql(
        consumer_attrs={"strict_ordering": True, "on_connect": on_connect},
        subprotocol=subprotocol,
    )
    await client.connect_and_init(connect_only=True)

    print("Try to initialize the connection.")
    await client.send_raw_message({"type": "connection_init"})
    if subprotocol == "graphql-ws":
        resp = await client.receive(assert_type="connection_error")
        assert resp["message"] == "RuntimeError: Connection rejected!"
        assert (
            resp["extensions"]["code"] == "RuntimeError"
        ), "Error should have 'extensions' with 'code'."
        await client.wait_disconnect()
    else:
        await client.wait_disconnect(assert_code=4403)

    print("Disconnect and wait the application to finish gracefully.")
    await client.assert_no_messages()
    await client.finalize()


@pytest.mark.asyncio
@pytest.mark.parametrize("subprotocol", ["graphql-transport-ws", "graphql-ws"])
async def test_subscribe_return_value(gql, subprotocol):
    """Assure the return value of the `subscribe` method is checked.

    - Check there is no error when `subscribe` returns nothing, a list
      or a tuple.
    - Check there is an `AssertionError` when `subscribe` returns
      a dict, a string or an empty string.
    """

    print("Check there is no error when `subscribe` returns nothing, list, or tuple.")

    for result_type in ["NONE", "LIST", "TUPLE"]:
        client = gql(subscription=Subscription, subprotocol=subprotocol)
        await client.connect_and_init()
        await client.start(
            query=f"""
                subscription {{ test_subscription (switch: "{result_type}\") {{ ok }} }}
                """
        )
        await client.assert_no_messages("Subscribe responded with a message!")
        await client.finalize()

    print("Check there is an error when `subscribe` returns string or dict.")

    for result_type in ["STR", "DICT", "EMPTYSTR"]:
        client = gql(subscription=Subscription, subprotocol=subprotocol)
        await client.connect_and_init()
        msg_id = await client.start(
            query=f"""
                subscription {{ test_subscription (switch: "{result_type}") {{ ok }} }}
                """
        )
        errors, _ = await client.receive_error(msg_id)
        assert "AssertionError" in errors[0]["message"], (
            "There is no error in response"
            " to the wrong type of the `subscribe` result!"
        )
        assert (
            errors[0]["extensions"]["code"] == "AssertionError"
        ), "Error should have 'extensions' with 'code'."

        await client.finalize()


# ---------------------------------------------------------------------- GRAPHQL BACKEND


class Query(graphene.ObjectType):
    """Root GraphQL query."""

    VALUE = str(uuid.uuid4().hex)
    value = graphene.String(args={"issue_error": graphene.Boolean(default_value=False)})

    def resolve_value(self, info, issue_error):
        """Resolver to return predefined value which can be tested."""
        del info
        assert self is None, "Root `self` expected to be `None`!"
        if issue_error:
            raise RuntimeError(Query.VALUE)
        return Query.VALUE


class TestSubscription(channels_graphql_ws.Subscription):
    """Test subscription with a "special" `subscribe` method.

    The `subscribe` method returns values of different types
    depending on the subscription parameter `switch`.
    """

    # Returning different values (even `None`) from the `subscribe`
    # method is the main idea of this test. Make Pylint ignore this.
    # pylint: disable=inconsistent-return-statements

    ok = graphene.Boolean()

    class Arguments:
        """Argument which controls `subscribe` result value."""

        switch = graphene.String()

    @staticmethod
    def subscribe(root, info, switch):
        """This returns nothing which must be OK."""
        del root, info
        if switch == "NONE":
            return None
        if switch == "LIST":
            return ["group"]
        if switch == "TUPLE":
            return ("group",)
        if switch == "STR":
            return "group"
        if switch == "DICT":
            return "group"
        if switch == "EMPTYSTR":
            return ""

    @staticmethod
    def publish(payload, info):
        """We will never get here in this test."""
        del payload, info
        assert False  # raises AssertionError exception


class Subscription(graphene.ObjectType):
    """Root subscription."""

    test_subscription = TestSubscription.Field()
