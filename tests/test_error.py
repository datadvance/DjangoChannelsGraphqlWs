#
# coding: utf-8
# Copyright (c) 2019 DATADVANCE
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
    comm = gql(query=Query)
    await comm.connect_and_init()

    print("Check that query syntax error leads to the `error` response.")
    msg_id = await comm.send(
        type="wrong_type__(ツ)_/¯", payload={"variables": {}, "operationName": ""}
    )
    with pytest.raises(channels_graphql_ws.GraphqlWsResponseError) as error:
        await comm.receive(assert_id=msg_id, assert_type="error")
        assert len(error.errors) == 1, "Multiple errors received instead of one!"
        assert isinstance(error.errors[0], str), "Error must be of string type!"

    print(
        "Check that query syntax error leads to the `data` response "
        "with `errors` array."
    )

    msg_id = await comm.send(
        type="start",
        payload={
            "query": "This produces a syntax error!",
            "variables": {},
            "operationName": "",
        },
    )
    with pytest.raises(channels_graphql_ws.GraphqlWsResponseError) as error:
        await comm.receive(assert_id=msg_id, assert_type="data")
        assert error.response["data"] is None
        assert len(error.errors) == 1, "Single error expected!"
        assert (
            "message" in error.errors[0] and "locations" in error.errors[0]
        ), "Response missing mandatory fields!"
        assert error.errors[0]["locations"] == [{"line": 1, "column": 1}]
    await comm.receive(assert_id=msg_id, assert_type="complete")

    print("Check that syntax error leads to the `data` response with `errors` array.")
    msg_id = await comm.send(
        type="start",
        payload={
            "query": "query op_name { value(issue_error: true) }",
            "variables": {},
            "operationName": "op_name",
        },
    )
    with pytest.raises(channels_graphql_ws.GraphqlWsResponseError) as error:
        await comm.receive(assert_id=msg_id, assert_type="data")
        assert error.response["data"]["value"] is None
        assert len(error.errors) == 1, "Single error expected!"
        assert error.errors[0]["message"] == Query.VALUE
        assert "locations" in error.errors[0]
    await comm.receive(assert_id=msg_id, assert_type="complete")

    print("Check multiple errors in the `data` message.")
    msg_id = await comm.send(
        type="start",
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
    with pytest.raises(channels_graphql_ws.GraphqlWsResponseError) as error:
        await comm.receive(assert_id=msg_id, assert_type="data")
        assert error.response["data"] is None
        assert (
            len(error.errors) == 5
        ), f"Five errors expected, but {len(error.errors)} errors received!"
        assert error.errors[0]["message"] == error.errors[3]["message"]
        assert "locations" in error.errors[2], "The `locations` field expected"
        assert "locations" in error.errors[4], "The `locations` field expected"
    await comm.receive(assert_id=msg_id, assert_type="complete")

    print("Disconnect and wait the application to finish gracefully.")
    await comm.finalize()


@pytest.mark.asyncio
async def test_connection_error(gql):
    """Test that server responds correctly when connection errors happen.

    Check that server responds with message of type `connection_error`
    when there was an exception in `on_connect` method.
    """

    print("Establish WebSocket GraphQL connection.")

    def on_connect(self, payload):
        del self, payload
        raise RuntimeError("Connection rejected!")

    comm = gql(consumer_attrs={"strict_ordering": True, "on_connect": on_connect})
    await comm.connect_and_init(connect_only=True)

    print("Try to initialize the connection.")
    await comm.send(type="connection_init", payload="")
    resp = await comm.receive(assert_type="connection_error")
    assert resp["message"] == "RuntimeError: Connection rejected!"
    resp = await comm.transport.receive_output()
    assert resp["type"] == "websocket.close"
    assert resp["code"] == 4000

    print("Disconnect and wait the application to finish gracefully.")
    await comm.finalize()


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
        # method is the main idea of this test. Make PyLint ignore this.
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
            assert False

    class Subscription(graphene.ObjectType):
        """Root subscription."""

        test_subscription = TestSubscription.Field()

    print("Check there is no error when `subscribe` returns nothing, list, or tuple.")

    for result_type in ["NONE", "LIST", "TUPLE"]:

        comm = gql(subscription=Subscription)
        await comm.connect_and_init()
        await comm.send(
            type="start",
            payload={
                "query": """subscription { test_subscription (switch: "%s") { ok } }"""
                % result_type
            },
        )
        await comm.assert_no_messages("Subscribe responded with a message!")
        await comm.finalize()

    print("Check there is a error when `subscribe` returns string or dict.")

    for result_type in ["STR", "DICT", "EMPTYSTR"]:
        comm = gql(subscription=Subscription)
        await comm.connect_and_init()
        msg_id = await comm.send(
            type="start",
            payload={
                "query": """subscription { test_subscription (switch: "%s") { ok } }"""
                % result_type
            },
        )
        with pytest.raises(channels_graphql_ws.GraphqlWsResponseError) as error:
            await comm.receive(assert_id=msg_id, assert_type="data")
            assert "AssertionError" in error.errors[0]["message"], (
                "There is no error in response"
                " to the wrong type of the `subscribe` result!"
            )
        await comm.finalize()
