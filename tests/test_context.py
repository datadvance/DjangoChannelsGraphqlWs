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

"""Test `info.context` and `ScopeAsContext`."""

from typing import List

import graphene
import pytest

import channels_graphql_ws.scope_as_context


def test_scope_as_context():
    """Make sure `ScopeAsContext` behaves as a correct dict wrapper."""
    print("Construct a context as a wrapper of dict scope.")
    scope = {}
    context = channels_graphql_ws.scope_as_context.ScopeAsContext(scope)

    print("Add records and check they propagate in both directions.")
    context.marker1 = 1
    assert "marker1" in context
    assert scope["marker1"] == context["marker1"] == context.marker1 == 1
    scope["marker2"] = 2
    assert "marker2" in context
    assert scope["marker2"] == context["marker2"] == context.marker2 == 2

    print("Check string context representation equals to dict one.")
    assert str(context) == str(scope)

    print("Make sure `_asdict` returns underlying scope.")
    assert id(context._asdict()) == id(scope)

    print("Remove records and check they propagate in both directions.")
    del scope["marker1"]
    assert "marker1" not in context
    assert "marker1" not in scope
    with pytest.raises(KeyError):
        _ = context["marker1"]
    with pytest.raises(AttributeError):
        _ = context.marker1
    del context["marker2"]
    assert "marker2" not in context
    assert "marker2" not in scope
    with pytest.raises(KeyError):
        _ = context["marker2"]
    with pytest.raises(AttributeError):
        _ = context.marker2


@pytest.mark.asyncio
async def test_context_lifetime(gql):
    """Check that `info.context` holds data during the connection."""

    # Store ids of `info.context.scope` objects to check them later.
    run_log: List[bool] = []

    print("Setup GraphQL backend and initialize GraphQL client.")

    class Query(graphene.ObjectType):
        """Root GraphQL query."""

        ok = graphene.Boolean()

        def resolve_ok(self, info):
            """Store `info.context.scope` id."""

            run_log.append("fortytwo" in info.context)
            if "fortytwo" in info.context:
                assert info.context.fortytwo == 42, "Context has delivered wrong data!"
            info.context.fortytwo = 42

            return True

    for _ in range(2):
        print("Make connection and perform query and close connection.")
        client = gql(query=Query, consumer_attrs={"strict_ordering": True})
        await client.connect_and_init()
        for _ in range(2):
            await client.send(msg_type="subscribe", payload={"query": "{ ok }"})
            await client.receive(assert_type="next")
            await client.receive(assert_type="complete")
        await client.finalize()

    # Expected run log: [False, True, False, True].
    assert run_log[2] is False, "Context preserved between connections!"
    assert run_log[0:2] == [False, True], "Context is not preserved in a connection!"
    assert run_log[2:4] == [False, True], "Context is not preserved in a connection!"
