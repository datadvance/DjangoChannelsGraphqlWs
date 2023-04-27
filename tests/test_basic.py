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

"""Check different basic scenarios."""

# NOTE: The GraphQL schema is defined at the end of the file.
# NOTE: In this file we use `strict_ordering=True` to simplify testing.

import json
import textwrap
import uuid

import graphene
import pytest

import channels_graphql_ws


@pytest.mark.asyncio
async def test_main_usecase(gql):
    """Test main use-case with the GraphQL over WebSocket."""

    print("Establish & initialize WebSocket GraphQL connection.")
    client = gql(
        query=Query,
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True},
    )
    await client.connect_and_init()

    print("Make simple GraphQL query and check the response.")
    msg_id = await client.send(
        msg_type="start",
        payload={
            "query": "query op_name { value }",
            "variables": {},
            "operationName": "op_name",
        },
    )
    resp = await client.receive(assert_id=msg_id, assert_type="data")
    assert resp["data"]["value"] == Query.VALUE
    await client.receive(assert_id=msg_id, assert_type="complete")

    print("Subscribe to GraphQL subscription.")
    sub_id = await client.send(
        msg_type="start",
        payload={
            "query": textwrap.dedent(
                """
                subscription op_name {
                    on_chat_message_sent(user_id: ALICE) { event }
                }
                """
            ),
            "variables": {},
            "operationName": "op_name",
        },
    )

    await client.assert_no_messages()

    print("Trigger the subscription by mutation to receive notification.")
    message = f"Hi! {str(uuid.uuid4().hex)}"
    msg_id = await client.send(
        msg_type="start",
        payload={
            "query": textwrap.dedent(
                """
                mutation op_name($message: String!) {
                    send_chat_message(message: $message) {
                        message
                    }
                }
                """
            ),
            "variables": {"message": message},
            "operationName": "op_name",
        },
    )

    # Mutation response.
    resp = await client.receive(assert_id=msg_id, assert_type="data")
    assert resp["data"] == {"send_chat_message": {"message": message}}
    resp = await client.receive(assert_id=msg_id, assert_type="complete")

    print("Check that subscription notification were sent.")
    # Subscription notification.
    resp = await client.receive(assert_id=sub_id, assert_type="data")
    event = resp["data"]["on_chat_message_sent"]["event"]
    assert json.loads(event) == {
        # pylint: disable=no-member
        "user_id": UserId.ALICE.value,  # type: ignore[attr-defined]
        "payload": message,
    }, "Subscription notification contains wrong data!"

    print("Disconnect and wait the application to finish gracefully.")
    await client.finalize()


@pytest.mark.asyncio
async def test_subscribe_unsubscribe(gql):
    """Test subscribe-unsubscribe behavior with the GraphQL over WebSocket.

    0. Subscribe to GraphQL subscription: messages for Alice.
    1. Send STOP message and unsubscribe.
    2. Subscribe to GraphQL subscription: messages for Tom.
    3. Call unsubscribe method of the Subscription instance
    (via `kick_out_user` mutation).
    4. Execute some mutation.
    5. Check subscription notifications: there are no notifications.
    """

    print("Establish & initialize WebSocket GraphQL connection.")
    client = gql(
        query=Query,
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True},
    )
    await client.connect_and_init()

    print("Subscribe to GraphQL subscription.")
    sub_id = await client.send(
        msg_type="start",
        payload={
            "query": textwrap.dedent(
                """
                subscription op_name { on_chat_message_sent(user_id: ALICE) { event } }
                """
            ),
            "variables": {},
            "operationName": "op_name",
        },
    )

    print("Stop subscription by id.")
    await client.send(msg_id=sub_id, msg_type="stop")
    await client.receive(assert_id=sub_id, assert_type="complete")

    print("Subscribe to GraphQL subscription.")
    sub_id = await client.send(
        msg_type="start",
        payload={
            "query": textwrap.dedent(
                """
                subscription op_name {
                    on_chat_message_sent(user_id: TOM) { event }
                }
                """
            ),
            "variables": {},
            "operationName": "op_name",
        },
    )

    print("Stop all subscriptions for TOM.")
    msg_id = await client.send(
        msg_type="start",
        payload={
            "query": "mutation op_name { kick_out_user(user_id: TOM) { success } }",
            "variables": {},
            "operationName": "op_name",
        },
    )
    # Mutation & unsubscription responses.
    await client.receive(assert_id=msg_id, assert_type="data")
    await client.receive(assert_id=msg_id, assert_type="complete")
    await client.receive(assert_id=sub_id, assert_type="complete")

    print("Trigger the subscription by mutation to receive notification.")
    msg_id = await client.send(
        msg_type="start",
        payload={
            "query": textwrap.dedent(
                """
                mutation op_name {
                    send_chat_message(message: "Is there anybody here?") {
                        message
                    }
                }
                """
            ),
            "variables": {},
            "operationName": "op_name",
        },
    )
    # Mutation response.
    await client.receive(assert_id=msg_id, assert_type="data")
    await client.receive(assert_id=msg_id, assert_type="complete")

    # Check notifications: there are no notifications! Previously,
    # we have unsubscribed from all subscriptions.
    await client.assert_no_messages(
        "Notification received in spite of we have unsubscribed from all subscriptions!"
    )

    print("Disconnect and wait the application to finish gracefully.")
    await client.finalize()


@pytest.mark.asyncio
async def test_subscription_groups(gql):
    """Test notifications behavior with different subscription group.

    Test notifications and subscriptions behavior depending on the
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

    async def create_and_subscribe(user_id):
        """Establish and initialize WebSocket GraphQL connection.

        Subscribe to GraphQL subscription by user_id.

        Args:
            user_id: User ID for `on_chat_message_sent` subscription.

        Returns:
            sub_id: Subscription uid.
            client: Client, instance of the `WebsocketCommunicator`.

        """
        client = gql(
            query=Query,
            mutation=Mutation,
            subscription=Subscription,
            consumer_attrs={"strict_ordering": True, "confirm_subscriptions": True},
        )
        await client.connect_and_init()

        sub_id = await client.send(
            msg_type="start",
            payload={
                "query": textwrap.dedent(
                    """
                    subscription op_name($user_id: UserId) {
                        on_chat_message_sent(user_id: $user_id) { event }
                    }
                    """
                ),
                "variables": {"user_id": user_id},
                "operationName": "op_name",
            },
        )

        # Receive the subscription confirmation message.
        resp = await client.receive(assert_id=sub_id, assert_type="data")
        assert resp == {"data": None}

        return sub_id, client

    async def trigger_subscription(client, user_id, message):
        """Send a message to user using `send_chat_message` mutation.

        Args:
            client: Client, instance of WebsocketCommunicator.
            user_id: User ID for `send_chat_message` mutation.
            message: Any string message.

        """
        msg_id = await client.send(
            msg_type="start",
            payload={
                "query": textwrap.dedent(
                    """
                    mutation op_name($message: String!, $user_id: UserId) {
                        send_chat_message(message: $message, user_id: $user_id) {
                            message
                        }
                    }
                    """
                ),
                "variables": {"message": message, "user_id": user_id},
                "operationName": "op_name",
            },
        )
        # Mutation response.
        await client.receive(assert_id=msg_id, assert_type="data")
        await client.receive(assert_id=msg_id, assert_type="complete")

    def check_resp(resp, user_id, message):
        """Check the response from `on_chat_message_sent` subscription.

        Args:
            user_id: Expected user ID.
            message: Expected message string.

        """
        event = resp["data"]["on_chat_message_sent"]["event"]
        assert json.loads(event) == {
            "user_id": user_id,
            "payload": message,
        }, "Subscription notification contains wrong data!"

    print("Initialize the connection, create subscriptions.")
    alice_id = "ALICE"
    tom_id = "TOM"
    # Subscribe to messages for Alice.
    uid_alice, comm_alice = await create_and_subscribe(alice_id)
    # Subscribe to messages for TOM.
    uid_tom, comm_tom = await create_and_subscribe(tom_id)

    print("Trigger subscription: send message to Tom.")
    message = "Hi, Tom!"
    await trigger_subscription(comm_alice, tom_id, message)
    # Check Tom's notifications.
    resp = await comm_tom.receive(assert_id=uid_tom, assert_type="data")
    check_resp(resp, UserId[tom_id].value, message)  # type: ignore[misc]
    # Any other did not receive any notifications.
    await comm_alice.assert_no_messages()
    print("Trigger subscription: send message to Alice.")
    message = "Hi, Alice!"
    await trigger_subscription(comm_tom, alice_id, message)
    # Check Alice's notifications.
    resp = await comm_alice.receive(assert_id=uid_alice, assert_type="data")
    check_resp(resp, UserId[alice_id].value, message)  # type: ignore[misc]
    # Any other did not receive any notifications.
    await comm_tom.assert_no_messages()

    print("Trigger subscription: send message to all groups.")
    message = "test... ping..."
    await trigger_subscription(comm_tom, None, message)

    print("Check Tom's and Alice's notifications.")
    resp = await comm_tom.receive(assert_id=uid_tom, assert_type="data")
    check_resp(resp, UserId[tom_id].value, message)  # type: ignore[misc]
    resp = await comm_alice.receive(assert_id=uid_alice, assert_type="data")
    check_resp(resp, UserId[alice_id].value, message)  # type: ignore[misc]

    print("Disconnect and wait the application to finish gracefully.")
    await comm_tom.finalize()
    await comm_alice.finalize()


@pytest.mark.asyncio
async def test_keepalive(gql):
    """Test that server sends keepalive messages."""

    print("Establish & initialize WebSocket GraphQL connection.")
    client = gql(
        query=Query,
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True, "send_keepalive_every": 0.05},
    )
    await client.connect_and_init()

    async def receive_keep_alive():
        response = await client.transport.receive()
        assert response["type"] == "ka", "Non keep alive response received!"

    await receive_keep_alive()
    print("Receive several keepalive messages.")
    for _ in range(3):
        await receive_keep_alive()

    print("Send connection termination message.")
    await client.send(msg_id=None, msg_type="connection_terminate")

    print("Disconnect and wait the application to finish gracefully.")
    await client.finalize()


# ---------------------------------------------------------------------- GRAPHQL BACKEND


class UserId(graphene.Enum):
    """User IDs for sending messages."""

    TOM = 0
    ALICE = 1


class OnChatMessageSent(channels_graphql_ws.Subscription):
    """Test GraphQL subscription.

    Subscribe to receive messages by user ID.
    """

    # pylint: disable=arguments-differ

    event = graphene.JSONString()

    class Arguments:
        """That is how subscription arguments are defined."""

        user_id = UserId()

    def subscribe(self, info, user_id=None):
        """Specify subscription groups when client subscribes."""
        del info
        assert self is None, "Root `self` expected to be `None`!"
        # Subscribe to the group corresponding to the user.
        if not user_id is None:
            return [f"user_{user_id}"]
        # Subscribe to default group.
        return []

    def publish(self, info, user_id):
        """Publish query result to the subscribers."""
        del info
        event = {"user_id": user_id.value, "payload": self}

        return OnChatMessageSent(event=event)

    @classmethod
    def notify(cls, user_id, message):
        """Example of the `notify` classmethod usage."""
        # Find the subscription group for user.
        group = None if user_id is None else f"user_{user_id}"
        cls.broadcast(group=group, payload=message)


class SendChatMessage(graphene.Mutation):
    """Test GraphQL mutation.

    Send message to the user or all users.
    """

    class Output(graphene.ObjectType):
        """Mutation result."""

        message = graphene.String()
        user_id = UserId()

    class Arguments:
        """That is how mutation arguments are defined."""

        message = graphene.String(required=True)
        user_id = graphene.Argument(UserId, required=False)

    def mutate(self, info, message, user_id=None):
        """Send message to the user or all users."""
        del info
        assert self is None, "Root `self` expected to be `None`!"

        # Notify subscribers.
        OnChatMessageSent.notify(message=message, user_id=user_id)

        return SendChatMessage.Output(message=message, user_id=user_id)


class KickOutUser(graphene.Mutation):
    """Test GraphQL mutation.

    Stop all subscriptions associated with the user.
    """

    class Arguments:
        """That is how mutation arguments are defined."""

        user_id = UserId()

    success = graphene.Boolean()

    def mutate(self, info, user_id):
        """Unsubscribe everyone associated with the user_id."""
        del info
        assert self is None, "Root `self` expected to be `None`!"

        OnChatMessageSent.unsubscribe(group=f"user_{user_id}")

        return KickOutUser(success=True)


class Subscription(graphene.ObjectType):
    """GraphQL subscriptions."""

    on_chat_message_sent = OnChatMessageSent.Field()


class Mutation(graphene.ObjectType):
    """GraphQL mutations."""

    send_chat_message = SendChatMessage.Field()
    kick_out_user = KickOutUser.Field()


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
