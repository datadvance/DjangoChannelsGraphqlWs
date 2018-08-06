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

"""Check different error cases."""

# NOTE: The GraphQL schema is defined at the end of the file.
# NOTE: In this file we use `strict_ordering=True` to simplify testing.

import textwrap
import uuid

import graphene
import pytest


@pytest.mark.asyncio
async def test_error_cases(gql):
    """Test that server responds correctly when errors happen.

    Check that server responds with message of type `data` when there
    is a syntax error in the request or the exception in a resolver
    was raised. Check that server responds with message of type `error`
    when there was an exceptional situation, for example, field `query`
    of `payload` is missing or field `type` has a wrong value.
    """

    print("Establish & initialize WebSocket GraphQL connection.")
    comm = gql(query=Query)
    await comm.gql_connect_and_init()

    print("Check that query syntax error leads to the `error` response.")
    msg_id = await comm.gql_send(
        type="wrong_type__(ツ)_/¯", payload={"variables": {}, "operationName": ""}
    )
    resp = await comm.gql_receive(
        assert_id=msg_id, assert_type="error", assert_no_errors=False
    )
    assert len(resp) == 1, "Multiple errors received while a single error expected!"
    assert isinstance(resp["errors"][0], str), "Error must be of string type!"

    print(
        "Check that query syntax error leads to the `data` response "
        "with `errors` array."
    )

    msg_id = await comm.gql_send(
        type="start",
        payload={
            "query": "This produces a syntax error!",
            "variables": {},
            "operationName": "",
        },
    )
    resp = await comm.gql_receive(
        assert_id=msg_id, assert_type="data", assert_no_errors=False
    )
    assert resp["data"] is None
    errors = resp["errors"]
    assert len(errors) == 1, "Single error expected!"
    assert (
        "message" in errors[0] and "locations" in errors[0]
    ), "Response missing mandatory fields!"
    assert errors[0]["locations"] == [{"line": 1, "column": 1}]
    await comm.gql_receive(assert_id=msg_id, assert_type="complete")

    print("Check that syntax error leads to the `data` response with `errors` array.")
    msg_id = await comm.gql_send(
        type="start",
        payload={
            "query": "query op_name { value(issue_error: true) }",
            "variables": {},
            "operationName": "op_name",
        },
    )
    resp = await comm.gql_receive(
        assert_id=msg_id, assert_type="data", assert_no_errors=False
    )
    assert resp["data"]["value"] is None
    errors = resp["errors"]
    assert len(errors) == 1, "Single error expected!"
    assert errors[0]["message"] == Query.VALUE
    assert "locations" in errors[0]
    await comm.gql_receive(assert_id=msg_id, assert_type="complete")

    print("Check multiple errors in the `data` message.")
    msg_id = await comm.gql_send(
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
    resp = await comm.gql_receive(
        assert_id=msg_id, assert_type="data", assert_no_errors=False
    )
    assert resp["data"] is None
    errors = resp["errors"]
    assert len(errors) == 5, f"Five errors expected, but {len(errors)} errors received!"
    assert errors[0]["message"] == errors[3]["message"]
    assert "locations" in errors[2], "The `locations` field expected"
    assert "locations" in errors[4], "The `locations` field expected"
    await comm.gql_receive(assert_id=msg_id, assert_type="complete")

    print("Disconnect and wait the application to finish gracefully.")
    await comm.gql_finalize()


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
    await comm.gql_connect_and_init(connect_only=True)

    print("Try to initialize the connection.")
    await comm.gql_send(type="connection_init", payload="")
    resp = await comm.gql_receive(assert_type="connection_error")
    assert resp["message"] == "RuntimeError: Connection rejected!"
    resp = await comm.receive_output()
    assert resp["type"] == "websocket.close"
    assert resp["code"] == 4000

    print("Disconnect and wait the application to finish gracefully.")
    await comm.gql_finalize()


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
