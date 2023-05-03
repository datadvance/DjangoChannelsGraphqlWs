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

import textwrap
import uuid

import graphene
import pytest

import channels_graphql_ws


@pytest.mark.asyncio
async def test_error_cases(gql):
    """Test that server responds correctly when errors happen.

    Check that server responds with message of type `data` when there
    is a syntax error in the request or the exception in a resolver
    was raised. Check that server responds with message of type `error`
    when there was an exceptional situation, for example, field `query`
    of `payload` is missing or field `type` has a wrong value.
    """

    print("Setup test GraphQL backend.")

    class Query(graphene.ObjectType):
        """Root GraphQL query."""

        VALUE = str(uuid.uuid4().hex)
        value = graphene.String(
            args={"issue_error": graphene.Boolean(default_value=False)}
        )

        def resolve_value(self, info, issue_error):
            """Resolver to return predefined value which can be tested."""
            del info
            assert self is None, "Root `self` expected to be `None`!"
            if issue_error:
                raise RuntimeError(Query.VALUE)
            return Query.VALUE

    print("Establish & initialize WebSocket GraphQL connection.")
    client = gql(query=Query)
    await client.connect_and_init()

    print("Check that query syntax error leads to the `error` response.")
    msg_id = await client.send(
        msg_type="wrong_type__(ツ)_/¯", payload={"variables": {}, "operationName": ""}
    )
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

    print(
        "Check that query syntax error leads to the `data` response "
        "with `errors` array."
    )

    msg_id = await client.send(
        msg_type="subscribe",
        payload={
            "query": "This produces a syntax error!",
            "variables": {},
            "operationName": "",
        },
    )
    with pytest.raises(channels_graphql_ws.GraphqlWsResponseError) as exc_info:
        await client.receive(assert_id=msg_id, assert_type="next")

    payload = exc_info.value.response["payload"]
    assert "data" in payload
    assert payload["data"] is None
    assert len(payload["errors"]) == 1, "Single error expected!"
    assert (
        "message" in payload["errors"][0] and "locations" in payload["errors"][0]
    ), "Response missing mandatory fields!"
    assert payload["errors"][0]["locations"] == [{"line": 1, "column": 1}]
    await client.receive(assert_id=msg_id, assert_type="complete")

    print("Check that syntax error leads to the `data` response with `errors` array.")
    msg_id = await client.send(
        msg_type="subscribe",
        payload={
            "query": "query op_name { value(issue_error: true) }",
            "variables": {},
            "operationName": "op_name",
        },
    )
    with pytest.raises(channels_graphql_ws.GraphqlWsResponseError) as exc_info:
        await client.receive(assert_id=msg_id, assert_type="next")
    payload = exc_info.value.response["payload"]
    assert payload["data"]["value"] is None
    assert len(payload["errors"]) == 1, "Single error expected!"
    assert payload["errors"][0]["message"] == Query.VALUE
    assert "locations" in payload["errors"][0]
    assert (
        "extensions" not in payload["errors"][0]
    ), "For syntax error there should be no 'extensions'."
    await client.receive(assert_id=msg_id, assert_type="complete")

    print("Check multiple errors in the `data` message.")
    msg_id = await client.send(
        msg_type="subscribe",
        payload={
            "query": textwrap.dedent(
                """
                query { projects { path wrong_field } }
                query a { projects }
                { wrong_name }
                """
            ),
            "variables": {},
            "operationName": "",
        },
    )
    with pytest.raises(channels_graphql_ws.GraphqlWsResponseError) as exc_info:
        await client.receive(assert_id=msg_id, assert_type="next")
    payload = exc_info.value.response["payload"]
    assert payload["data"] is None
    assert (
        len(payload["errors"]) == 5
    ), f"Five errors expected, but {len(payload['errors'])} errors received!"
    assert payload["errors"][0]["message"] == payload["errors"][3]["message"]
    assert "locations" in payload["errors"][2], "The `locations` field expected"
    assert "locations" in payload["errors"][4], "The `locations` field expected"
    await client.receive(assert_id=msg_id, assert_type="complete")

    print("Disconnect and wait the application to finish gracefully.")
    await client.finalize()


@pytest.mark.asyncio
async def test_connection_error(gql):
    """Test that server disconnects user when `on_connect` raises error.

    When GraphqlWsConsumer method `on_connect` raises server must:
        1. Sends proper error message - with type `connection_error`.
        2. Disconnect user
    """

    print("Establish WebSocket GraphQL connection.")

    def on_connect(self, payload):
        del self, payload
        raise RuntimeError("Connection rejected!")

    client = gql(consumer_attrs={"strict_ordering": True, "on_connect": on_connect})
    await client.connect_and_init(connect_only=True)

    print("Try to initialize the connection.")
    await client.send(msg_type="connection_init", payload="")
    resp = await client.receive(assert_type="connection_error")
    assert resp["message"] == "RuntimeError: Connection rejected!"
    assert (
        resp["extensions"]["code"] == "RuntimeError"
    ), "Error should have 'extensions' with 'code'."
    await client.wait_disconnect()

    print("Disconnect and wait the application to finish gracefully.")
    await client.assert_no_messages()
    await client.finalize()


@pytest.mark.asyncio
async def test_subscribe_return_value(gql):
    """Assure the return value of the `subscribe` method is checked.

    - Check there is no error when `subscribe` returns nothing, a list
      or a tuple.
    - Check there is an `AssertionError` when `subscribe` returns
      a dict, a string or an empty string.
    """

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

    print("Check there is no error when `subscribe` returns nothing, list, or tuple.")

    for result_type in ["NONE", "LIST", "TUPLE"]:
        client = gql(subscription=Subscription)
        await client.connect_and_init()
        await client.send(
            msg_type="subscribe",
            payload={
                "query": f"""
                subscription {{ test_subscription (switch: "{result_type}\") {{ ok }} }}
                """
            },
        )
        await client.assert_no_messages("Subscribe responded with a message!")
        await client.finalize()

    print("Check there is a error when `subscribe` returns string or dict.")

    for result_type in ["STR", "DICT", "EMPTYSTR"]:
        client = gql(subscription=Subscription)
        await client.connect_and_init()
        msg_id = await client.send(
            msg_type="subscribe",
            payload={
                "query": f"""
                subscription {{ test_subscription (switch: "{result_type}") {{ ok }} }}
                """
            },
        )
        with pytest.raises(channels_graphql_ws.GraphqlWsResponseError) as ex:
            await client.receive(assert_id=msg_id, assert_type="next")

        payload = ex.value.response["payload"]
        assert "AssertionError" in payload["errors"][0]["message"], (
            "There is no error in response"
            " to the wrong type of the `subscribe` result!"
        )
        assert (
            payload["errors"][0]["extensions"]["code"] == "AssertionError"
        ), "Error should have 'extensions' with 'code'."

        await client.finalize()
