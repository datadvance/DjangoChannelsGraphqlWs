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

"""Tests GraphQL over WebSockets with subscriptions.

Here we test `Subscription` and `GraphqlWsConsumer` classes.
"""

# NOTE: Tests use the GraphQL over WebSockets setup. All the necessary
#       items (schema, query, subscriptions, mutations, Channels
#       consumer & application) are defined at the end on this file.

import json
import textwrap
import uuid

import channels
import channels.testing as ch_testing
import django.urls
import graphene
import pytest

from .graphql_ws import GraphqlWsConsumer, Subscription


# Default timeout. Increased to avoid TimeoutErrors on slow machines.
TIMEOUT = 5


@pytest.mark.asyncio
async def test_main_usecase():
    """Test main use-case with the GraphQL over WebSocket."""

    # Channels communicator to test WebSocket consumers.
    comm = ch_testing.WebsocketCommunicator(
        application=my_app, path="graphql/", subprotocols=["graphql-ws"]
    )

    print("Establish WebSocket connection and check a subprotocol.")
    connected, subprotocol = await comm.connect(timeout=TIMEOUT)
    assert connected, "Could not connect to the GraphQL subscriptions WebSocket!"
    assert subprotocol == "graphql-ws", "Wrong subprotocol received!"

    print("Initialize GraphQL connection.")
    await comm.send_json_to({"type": "connection_init", "payload": ""})
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["type"] == "connection_ack"

    print("Make simple GraphQL query and check the response.")
    uniq_id = str(uuid.uuid4().hex)
    await comm.send_json_to(
        {
            "id": uniq_id,
            "type": "start",
            "payload": {
                "query": textwrap.dedent(
                    """
                query MyOperationName {
                    value
                }
            """
                ),
                "variables": {},
                "operationName": "MyOperationName",
            },
        }
    )
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == uniq_id, "Response id != request id!"
    assert resp["type"] == "data", "Type `data` expected!"
    assert "errors" not in resp["payload"]
    assert resp["payload"]["data"]["value"] == MyQuery.VALUE
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == uniq_id, "Response id != request id!"
    assert resp["type"] == "complete", "Type `complete` expected!"

    print("Subscribe to GraphQL subscription.")
    subscription_id = str(uuid.uuid4().hex)

    await comm.send_json_to(
        {
            "id": subscription_id,
            "type": "start",
            "payload": {
                "query": textwrap.dedent(
                    """
                    subscription MyOperationName {
                        on_chat_message_sent(userId: ALICE) {
                            event
                        }
                    }
                    """
                ),
                "variables": {},
                "operationName": "MyOperationName",
            },
        }
    )

    print("Trigger the subscription by mutation to receive notification.")
    uniq_id = str(uuid.uuid4().hex)
    message = "Hi!"
    await comm.send_json_to(
        {
            "id": uniq_id,
            "type": "start",
            "payload": {
                "query": textwrap.dedent(
                    """
                    mutation MyOperationName($message: String!) {
                        send_chat_message(message: $message) {
                            message
                        }
                    }
                    """
                ),
                "variables": {"message": message},
                "operationName": "MyOperationName",
            },
        }
    )

    # Mutation response.
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == uniq_id, "Response id != request id!"
    assert resp["type"] == "data", "Type `data` expected!"
    assert "errors" not in resp["payload"]
    assert resp["payload"]["data"] == {"send_chat_message": {"message": message}}
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == uniq_id, "Response id != request id!"
    assert resp["type"] == "complete", "Type `complete` expected!"

    # Subscription notification.
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == subscription_id, "Notification id != subscription id!"
    assert resp["type"] == "data", "Type `data` expected!"
    assert "errors" not in resp["payload"]
    event = resp["payload"]["data"]["on_chat_message_sent"]["event"]
    assert json.loads(event) == {
        "userId": UserId.ALICE,
        "payload": message,
    }, "Subscription notification contains wrong data!"

    print("Disconnect and wait the application to finish gracefully.")
    await comm.disconnect(timeout=TIMEOUT)
    await comm.wait(timeout=TIMEOUT)


@pytest.mark.asyncio
async def test_error_cases():
    """Test that server responds correctly when errors happen.

    Check that server responds with message of type `data` when there
    is a syntax error in the request or the exception in a resolver
    was raised. Check that server responds with message of type `error`
    when there was an exceptional situation, for example, field `query`
    of `payload` is missing or field `type` has a wrong value.
    """

    # Channels communicator to test WebSocket consumers.
    comm = ch_testing.WebsocketCommunicator(
        application=my_app, path="graphql/", subprotocols=["graphql-ws"]
    )

    print("Establish & initialize the connection.")
    await comm.connect(timeout=TIMEOUT)
    await comm.send_json_to({"type": "connection_init", "payload": ""})
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["type"] == "connection_ack"

    print("Check that query syntax error leads to the `error` response.")
    uniq_id = str(uuid.uuid4().hex)
    await comm.send_json_to(
        {
            "id": uniq_id,
            "type": "wrong_type__(ツ)_/¯",
            "payload": {"variables": {}, "operationName": "MyOperationName"},
        }
    )
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == uniq_id, "Response id != request id!"
    assert resp["type"] == "error", "Type `error` expected!"
    assert len(resp["payload"]) == 1, "Single error expected!"
    assert isinstance(
        resp["payload"]["errors"][0], str
    ), "Error must be of string type!"

    print(
        "Check that query syntax error leads to the `data` response "
        "with `errors` array."
    )
    uniq_id = str(uuid.uuid4().hex)
    await comm.send_json_to(
        {
            "id": uniq_id,
            "type": "start",
            "payload": {
                "query": textwrap.dedent(
                    """
                    This list produces a syntax error!
                    """
                ),
                "variables": {},
                "operationName": "MyOperationName",
            },
        }
    )
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == uniq_id, "Response id != request id!"
    assert resp["type"] == "data", "Type `data` expected!"
    payload = resp["payload"]
    assert payload["data"] is None
    errors = payload["errors"]
    assert len(errors) == 1, "Single error expected!"
    assert (
        "message" in errors[0] and "locations" in errors[0]
    ), "Response missing mandatory fields!"
    assert errors[0]["locations"] == [{"line": 1, "column": 1}]
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == uniq_id, "Response id != request id!"
    assert resp["type"] == "complete", "Type `complete` expected!"

    print(
        "Check that query syntax error leads to the `data` response "
        "with `errors` array."
    )
    uniq_id = str(uuid.uuid4().hex)
    await comm.send_json_to(
        {
            "id": uniq_id,
            "type": "start",
            "payload": {
                "query": textwrap.dedent(
                    """
                    query MyOperationName {
                        value(issue_error: true)
                    }
                    """
                ),
                "variables": {},
                "operationName": "MyOperationName",
            },
        }
    )
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == uniq_id, "Response id != request id!"
    assert resp["type"] == "data", "Type `data` expected!"
    payload = resp["payload"]
    assert payload["data"]["value"] is None
    errors = payload["errors"]
    assert len(errors) == 1, "Single error expected!"
    assert errors[0]["message"] == MyQuery.VALUE
    assert "locations" in errors[0]
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == uniq_id, "Response id != request id!"
    assert resp["type"] == "complete", "Type `complete` expected!"

    print("Check multiple errors in the `data` message.")
    uniq_id = str(uuid.uuid4().hex)
    await comm.send_json_to(
        {
            "id": uniq_id,
            "type": "start",
            "payload": {
                "query": textwrap.dedent(
                    """
                    query {
                        projects {
                            path
                            wrong_filed
                        }
                    }

                    query a {
                        projects
                    }

                    { wrong_name }
                    """
                ),
                "variables": {},
                "operationName": "MyOperationName",
            },
        }
    )
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == uniq_id, "Response id != request id!"
    assert resp["type"] == "data", "Type `data` expected!"
    payload = resp["payload"]
    assert payload["data"] is None
    errors = payload["errors"]
    assert len(errors) == 5, "Five errors expected!"
    # Message is here: `This anonymous operation must be
    # the only defined operation`.
    assert errors[0]["message"] == errors[3]["message"]
    assert "locations" in errors[2], "The `locations` field expected"
    assert "locations" in errors[4], "The `locations` field expected"
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == uniq_id, "Response id != request id!"
    assert resp["type"] == "complete", "Type `complete` expected!"

    print("Disconnect and wait the application to finish gracefully.")
    await comm.disconnect(timeout=TIMEOUT)
    await comm.wait(timeout=TIMEOUT)


@pytest.mark.asyncio
async def test_connection_error():
    """Test that server responds correctly when connection errors happen.

    Check that server responds with message of type `connection_error`
    when there was an exception in `on_connect` method.
    """

    print("Prepare application.")

    class MyGraphqlWsConsumerConnectionError(GraphqlWsConsumer):
        """Channels WebSocket consumer which provides GraphQL API."""

        schema = ""

        async def on_connect(self, payload):
            from graphql.error import GraphQLError

            # Always close the connection.
            raise GraphQLError("Reject connection")

    application = channels.routing.ProtocolTypeRouter(
        {
            "websocket": channels.routing.URLRouter(
                [
                    django.urls.path(
                        "graphql-connection-error/", MyGraphqlWsConsumerConnectionError
                    )
                ]
            )
        }
    )

    # Channels communicator to test WebSocket consumers.
    comm = ch_testing.WebsocketCommunicator(
        application=application,
        path="graphql-connection-error/",
        subprotocols=["graphql-ws"],
    )

    print("Try to initialize the connection.")
    await comm.connect(timeout=TIMEOUT)
    await comm.send_json_to({"type": "connection_init", "payload": ""})
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["type"] == "connection_error"
    assert resp["payload"]["message"] == "Reject connection"
    resp = await comm.receive_output(timeout=TIMEOUT)
    assert resp["type"] == "websocket.close"
    assert resp["code"] == 4000

    print("Disconnect and wait the application to finish gracefully.")
    await comm.disconnect(timeout=TIMEOUT)
    await comm.wait(timeout=TIMEOUT)


@pytest.mark.asyncio
async def test_subscribe_unsubscribe():
    """Test subscribe-unsubscribe behavior with the GraphQL over WebSocket.

    0. Subscribe to GraphQL subscription: messages for Alice.
    1. Send STOP message and unsubscribe.
    2. Subscribe to GraphQL subscription: messages for Tom.
    3. Call unsubscribe method of the Subscription instance
    (via `kick_out_user` mutation).
    4. Execute some mutation.
    5. Check subscription notifications: there are no notifications.
    """

    # Channels communicator to test WebSocket consumers.
    comm = ch_testing.WebsocketCommunicator(
        application=my_app, path="graphql/", subprotocols=["graphql-ws"]
    )

    print("Establish and initialize WebSocket GraphQL connection.")
    await comm.connect(timeout=TIMEOUT)
    await comm.send_json_to({"type": "connection_init", "payload": ""})
    await comm.receive_json_from(timeout=TIMEOUT)

    print("Subscribe to GraphQL subscription.")
    sub_id = str(uuid.uuid4().hex)
    await comm.send_json_to(
        {
            "id": sub_id,
            "type": "start",
            "payload": {
                "query": textwrap.dedent(
                    """
                    subscription MyOperationName {
                        on_chat_message_sent(userId: ALICE) { event }
                    }
                    """
                ),
                "variables": {},
                "operationName": "MyOperationName",
            },
        }
    )

    print("Stop subscription by id.")
    await comm.send_json_to({"id": sub_id, "type": "stop"})
    # Subscription notification with unsubscribe information.
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == sub_id, "Response id != request id!"
    assert resp["type"] == "complete", "Type `complete` expected!"

    print("Subscribe to GraphQL subscription.")
    sub_id = str(uuid.uuid4().hex)
    await comm.send_json_to(
        {
            "id": sub_id,
            "type": "start",
            "payload": {
                "query": textwrap.dedent(
                    """
                    subscription MyOperationName {
                        on_chat_message_sent(userId: TOM) { event }
                    }
                    """
                ),
                "variables": {},
                "operationName": "MyOperationName",
            },
        }
    )

    print("Stop all subscriptions for TOM.")
    uniq_id = str(uuid.uuid4().hex)
    await comm.send_json_to(
        {
            "id": uniq_id,
            "type": "start",
            "payload": {
                "query": textwrap.dedent(
                    """
                    mutation MyOperationName {
                        kick_out_user(userId: TOM) { success }
                    }
                    """
                ),
                "variables": {},
                "operationName": "MyOperationName",
            },
        }
    )
    # Mutation response.
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == uniq_id, "Response id != request id!"
    assert resp["type"] == "data", "Type `data` expected!"
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == uniq_id, "Response id != request id!"
    assert resp["type"] == "complete", "Type `complete` expected!"
    # Subscription notification with unsubscribe information.
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == sub_id, "Response id != request id!"
    assert resp["type"] == "complete", "Type `complete` expected!"

    print("Trigger the subscription by mutation to receive notification.")
    uniq_id = str(uuid.uuid4().hex)
    message = "Is anybody here?"
    await comm.send_json_to(
        {
            "id": uniq_id,
            "type": "start",
            "payload": {
                "query": textwrap.dedent(
                    """
                    mutation MyOperationName($message: String!) {
                        send_chat_message(message: $message) {
                            message
                        }
                    }
                    """
                ),
                "variables": {"message": message},
                "operationName": "MyOperationName",
            },
        }
    )
    # Mutation response.
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == uniq_id, "Response id != request id!"
    assert resp["type"] == "data", "Type `data` expected!"
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["id"] == uniq_id, "Response id != request id!"
    assert resp["type"] == "complete", "Type `complete` expected!"

    # Check notifications: there are no notifications! Previously,
    # there was an unsubscription from all subscriptions.
    assert await comm.receive_nothing() is True, "No notifications expected!"

    print("Disconnect and wait the application to finish gracefully.")
    await comm.disconnect(timeout=TIMEOUT)
    await comm.wait(timeout=TIMEOUT)


@pytest.mark.asyncio
async def test_groups():
    """Test notifications and subscriptions behavior depending on the
    different subscription groups.

    0. Subscribe to the group1: messages for Alice.
    1. Subscribe to the group2: messages for Tom.
    2. Trigger group1 (send message to Alice) and check subscribed
    recipients: Alice.
    3. Trigger group2 (send message to Tom) and check subscribed
    recipients: Tom.
    4. Trigger all groups (send messages for all users) and check
    subscribed recipients: Alice, Tom.
    """

    async def create_and_subscribe(userId):
        """Establish and initialize WebSocket GraphQL connection.

        Subscribe to GraphQL subscription by userId.

        Args:
            userId: User ID for `on_chat_message_sent` subscription.

        Returns:
            sub_id: Subscription uid.
            comm: Client, instance of the `WebsocketCommunicator`.
        """
        comm = ch_testing.WebsocketCommunicator(
            application=my_app, path="graphql/", subprotocols=["graphql-ws"]
        )

        await comm.connect(timeout=TIMEOUT)
        await comm.send_json_to({"type": "connection_init", "payload": ""})
        await comm.receive_json_from(timeout=TIMEOUT)

        sub_id = str(uuid.uuid4().hex)
        await comm.send_json_to(
            {
                "id": sub_id,
                "type": "start",
                "payload": {
                    "query": textwrap.dedent(
                        """
                        subscription MyOperationName($userId: UserId) {
                            on_chat_message_sent(userId: $userId) { event }
                        }
                        """
                    ),
                    "variables": {"userId": userId},
                    "operationName": "MyOperationName",
                },
            }
        )
        return sub_id, comm

    async def trigger_subscription(comm, userId, message):
        """Send a message to user using `send_chat_message` mutation.

        Args:
            comm: Client, instance of WebsocketCommunicator.
            userId: User ID for `send_chat_message` mutation.
            message: Any string message.
        """
        uniq_id = str(uuid.uuid4().hex)
        await comm.send_json_to(
            {
                "id": uniq_id,
                "type": "start",
                "payload": {
                    "query": textwrap.dedent(
                        """
                        mutation MyOperationName($message: String!,
                                                 $userId: UserId) {
                            send_chat_message(message: $message,
                                              userId: $userId) { message }
                        }
                        """
                    ),
                    "variables": {"message": message, "userId": userId},
                    "operationName": "MyOperationName",
                },
            }
        )
        # Mutation response.
        resp = await comm.receive_json_from(timeout=TIMEOUT)
        assert resp["id"] == uniq_id, "Response id != request id!"
        resp = await comm.receive_json_from(timeout=TIMEOUT)
        assert resp["id"] == uniq_id, "Response id != request id!"

    def check_resp(resp, uid, user_id, message):
        """Check the responce from `on_chat_message_sent` subscription.

        Args:
            uid: Expected value of field `id` of responce.
            userId: Expected user ID.
            message: Expected message string.
        """
        assert resp["id"] == uid, "Notification id != subscription id!"
        assert resp["type"] == "data", "Type `data` expected!"
        assert "errors" not in resp["payload"]
        event = resp["payload"]["data"]["on_chat_message_sent"]["event"]
        assert json.loads(event) == {
            "userId": user_id,
            "payload": message,
        }, "Subscription notification contains wrong data!"

    async def disconnect(comm):
        """Disconnect and wait the application to finish gracefully."""
        await comm.disconnect(timeout=TIMEOUT)
        await comm.wait(timeout=TIMEOUT)

    print("Initialize the connection, create subscriptions.")
    alice_id = "ALICE"
    tom_id = "TOM"
    # Subscribe to messages for TOM.
    uid_tom, comm_tom = await create_and_subscribe(tom_id)
    # Subscribe to messages for Alice.
    uid_alice, comm_alice = await create_and_subscribe(alice_id)

    print("Trigger subscription: send message to Tom.")
    message = "Hi, Tom!"
    # Note: Strictly speaking, we do not have confidence that `comm_tom`
    # had enough time to subscribe. So Tom may not be able to receive
    # a message from Alice. But in this simple test, we performed the
    # Tom's subscription before the Alice's subscription and
    # that should be enough.
    await trigger_subscription(comm_alice, tom_id, message)
    # Check Tom's notifications.
    resp = await comm_tom.receive_json_from(timeout=TIMEOUT)
    check_resp(resp, uid_tom, UserId[tom_id].value, message)
    # Any other did not receive any notifications.
    assert await comm_alice.receive_nothing() is True, "No notifications expected!"

    print("Trigger subscription: send message to Alice.")
    message = "Hi, Alice!"
    await trigger_subscription(comm_tom, alice_id, message)
    # Check Tom's notifications.
    resp = await comm_alice.receive_json_from(timeout=TIMEOUT)
    check_resp(resp, uid_alice, UserId[alice_id].value, message)
    # Any other did not receive any notifications.
    assert await comm_tom.receive_nothing() is True, "No notifications expected!"

    print("Trigger subscription: send message to all groups.")
    message = "test... ping..."
    await trigger_subscription(comm_tom, None, message)
    # Check Tom's and Alice's notifications.
    resp = await comm_tom.receive_json_from(timeout=TIMEOUT)
    check_resp(resp, uid_tom, UserId[tom_id].value, message)
    resp = await comm_alice.receive_json_from(timeout=TIMEOUT)
    check_resp(resp, uid_alice, UserId[alice_id].value, message)

    # Disconnect.
    await disconnect(comm_tom)
    await disconnect(comm_alice)


@pytest.mark.asyncio
async def test_keepalive():
    """Test that server sends keepalive messages."""

    print("Prepare application.")

    class MyGraphqlWsConsumerKeepalive(GraphqlWsConsumer):
        """Channels WebSocket consumer which provides GraphQL API."""

        schema = ""
        # Period to send keepalive mesages. Just some reasonable number.
        send_keepalive_every = 0.05

    application = channels.routing.ProtocolTypeRouter(
        {
            "websocket": channels.routing.URLRouter(
                [django.urls.path("graphql-keepalive/", MyGraphqlWsConsumerKeepalive)]
            )
        }
    )

    # Channels communicator to test WebSocket consumers.
    comm = ch_testing.WebsocketCommunicator(
        application=application, path="graphql-keepalive/", subprotocols=["graphql-ws"]
    )

    print("Establish & initialize the connection.")
    await comm.connect(timeout=TIMEOUT)
    await comm.send_json_to({"type": "connection_init", "payload": ""})
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp["type"] == "connection_ack"
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert (
        resp["type"] == "ka"
    ), "Keepalive message expected right after `connection_ack`!"

    print("Receive several keepalive messages.")
    pings = []
    for _ in range(3):
        pings.append(await comm.receive_json_from(timeout=TIMEOUT))
    assert all([ping["type"] == "ka" for ping in pings])

    print("Send connection termination message.")
    await comm.send_json_to({"type": "connection_terminate"})

    print("Disconnect and wait the application to finish gracefully.")
    await comm.disconnect(timeout=TIMEOUT)
    await comm.wait(timeout=TIMEOUT)


# --------------------------------------------------------- GRAPHQL OVER WEBSOCKET SETUP


class UserId(graphene.Enum):
    """User IDs for sending messages."""

    TOM = 0
    ALICE = 1


class OnChatMessageSent(Subscription):
    """Test GraphQL subscription.

    Subscribe to receive messages by user ID.
    """

    event = graphene.JSONString()

    class Arguments:
        """That is how subscription arguments are defined."""

        userId = UserId()

    def subscribe(
        self, info, userId
    ):  # pylint: disable=unused-argument,arguments-differ
        """Specify subscription groups when client subscribes."""
        assert self is None, "Root `self` expected to be `None`!"
        # Subscribe to the group corresponding to the user.
        if not userId is None:
            return [f"user_{userId}"]
        # Subscribe to default group.
        return []

    def publish(self, info, userId):  # pylint: disable=unused-argument,arguments-differ
        """Publish query result to the subscribers."""
        event = {"userId": userId, "payload": self}

        return OnChatMessageSent(event=event)

    @classmethod
    def notify(cls, userId, message):  # pylint: disable=arguments-differ
        """Example of the `notify` classmethod usage."""
        # Find the subscription group for user.
        group = None if userId is None else f"user_{userId}"
        super().broadcast(group=group, payload=message)


class SendChatMessage(graphene.Mutation):
    """Test GraphQL mutation.

    Send message to the user or all users.
    """

    class Output(graphene.ObjectType):
        """Mutation result."""

        message = graphene.String()
        userId = UserId()

    class Arguments:
        """That is how mutation arguments are defined."""

        message = graphene.String(required=True)
        userId = graphene.Argument(UserId, required=False)

    def mutate(self, info, message, userId=None):  # pylint: disable=unused-argument
        """Send message to the user or all users."""
        assert self is None, "Root `self` expected to be `None`!"

        # Notify subscribers.
        OnChatMessageSent.notify(message=message, userId=userId)

        return SendChatMessage.Output(message=message, userId=userId)


class KickOutUser(graphene.Mutation):
    """Test GraphQL mutation.

    Stop all subscriptions associated with the user.
    """

    class Arguments:
        """That is how mutation arguments are defined."""

        userId = UserId()

    success = graphene.Boolean()

    def mutate(self, info, userId):  # pylint: disable=unused-argument
        """Unsubscribe everyone associated with the userId."""
        assert self is None, "Root `self` expected to be `None`!"

        OnChatMessageSent.unsubscribe(group=f"user_{userId}")

        return KickOutUser(success=True)


class MySubscription(graphene.ObjectType):
    """GraphQL subscriptions."""

    on_chat_message_sent = OnChatMessageSent.Field()


class MyMutation(graphene.ObjectType):
    """GraphQL mutations."""

    send_chat_message = SendChatMessage.Field()
    kick_out_user = KickOutUser.Field()


class MyQuery(graphene.ObjectType):
    """Root GraphQL query."""

    VALUE = str(uuid.uuid4().hex)
    value = graphene.String(args={"issue_error": graphene.Boolean(default_value=False)})

    def resolve_value(self, info, issue_error):  # pylint: disable=unused-argument
        """Resolver to return predefined value which can be tested."""
        assert self is None, "Root `self` expected to be `None`!"
        if issue_error:
            raise RuntimeError(MyQuery.VALUE)
        return MyQuery.VALUE


my_schema = graphene.Schema(
    query=MyQuery,
    subscription=MySubscription,
    mutation=MyMutation,
    auto_camelcase=False,
)


class MyGraphqlWsConsumer(GraphqlWsConsumer):
    """Channels WebSocket consumer which provides GraphQL API."""

    schema = my_schema


my_app = channels.routing.ProtocolTypeRouter(
    {
        "websocket": channels.routing.URLRouter(
            [django.urls.path("graphql/", MyGraphqlWsConsumer)]
        )
    }
)
