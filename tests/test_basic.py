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
async def test_main_usecase(gql_communicator):
    """Test main use-case with the GraphQL over WebSocket."""

    print("Establish & initialize WebSocket GraphQL connection.")
    comm = gql_communicator(
        Query, Mutation, Subscription, consumer_attrs={"strict_ordering": True}
    )
    await comm.gql_connect()
    await comm.gql_init()

    print("Make simple GraphQL query and check the response.")
    msg_id = await comm.gql_send(
        type="start",
        payload={
            "query": "query op_name { value }",
            "variables": {},
            "operationName": "op_name",
        },
    )
    resp = await comm.gql_receive(assert_id=msg_id, assert_type="data")
    assert resp["payload"]["data"]["value"] == Query.VALUE
    await comm.gql_receive(assert_id=msg_id, assert_type="complete")

    print("Subscribe to GraphQL subscription.")
    sub_id = await comm.gql_send(
        type="start",
        payload={
            "query": textwrap.dedent(
                """
                subscription op_name {
                    on_chat_message_sent(userId: ALICE) { event }
                }
                """
            ),
            "variables": {},
            "operationName": "op_name",
        },
    )

    await comm.gql_assert_no_response()

    print("Trigger the subscription by mutation to receive notification.")
    message = f"Hi! {str(uuid.uuid4().hex)}"
    msg_id = await comm.gql_send(
        type="start",
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
    resp = await comm.gql_receive(assert_id=msg_id, assert_type="data")
    assert resp["payload"]["data"] == {"send_chat_message": {"message": message}}
    await comm.gql_receive(assert_id=msg_id, assert_type="complete")

    # Subscription notification.
    resp = await comm.gql_receive(assert_id=sub_id, assert_type="data")
    event = resp["payload"]["data"]["on_chat_message_sent"]["event"]
    assert json.loads(event) == {
        "userId": UserId.ALICE,
        "payload": message,
    }, "Subscription notification contains wrong data!"

    print("Disconnect and wait the application to finish gracefully.")
    await comm.gql_finalize()


@pytest.mark.asyncio
async def test_subscribe_unsubscribe(gql_communicator):
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
    comm = gql_communicator(
        Query, Mutation, Subscription, consumer_attrs={"strict_ordering": True}
    )
    await comm.gql_connect()
    await comm.gql_init()

    print("Subscribe to GraphQL subscription.")
    sub_id = await comm.gql_send(
        type="start",
        payload={
            "query": textwrap.dedent(
                """
                subscription op_name { on_chat_message_sent(userId: ALICE) { event } }
                """
            ),
            "variables": {},
            "operationName": "op_name",
        },
    )

    print("Stop subscription by id.")
    await comm.gql_send(id=sub_id, type="stop")
    await comm.gql_receive(assert_id=sub_id, assert_type="complete")

    print("Subscribe to GraphQL subscription.")
    sub_id = await comm.gql_send(
        type="start",
        payload={
            "query": textwrap.dedent(
                """
                subscription op_name {
                    on_chat_message_sent(userId: TOM) { event }
                }
                """
            ),
            "variables": {},
            "operationName": "op_name",
        },
    )

    print("Stop all subscriptions for TOM.")
    msg_id = await comm.gql_send(
        type="start",
        payload={
            "query": "mutation op_name { kick_out_user(userId: TOM) { success } }",
            "variables": {},
            "operationName": "op_name",
        },
    )
    # Mutation & unsubscription responses.
    await comm.gql_receive(assert_id=msg_id, assert_type="data")
    await comm.gql_receive(assert_id=msg_id, assert_type="complete")
    await comm.gql_receive(assert_id=sub_id, assert_type="complete")

    print("Trigger the subscription by mutation to receive notification.")
    msg_id = await comm.gql_send(
        type="start",
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
            "variables": "",
            "operationName": "op_name",
        },
    )
    # Mutation response.
    await comm.gql_receive(assert_id=msg_id, assert_type="data")
    await comm.gql_receive(assert_id=msg_id, assert_type="complete")

    # Check notifications: there are no notifications! Previously,
    # we have unsubscribed from all subscriptions.
    await comm.gql_assert_no_response(
        "Notification received in spite of we have unsubscribed from all subscriptions!"
    )

    print("Disconnect and wait the application to finish gracefully.")
    await comm.gql_finalize()


@pytest.mark.asyncio
async def test_groups(gql_communicator):
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

        comm = gql_communicator(
            Query, Mutation, Subscription, consumer_attrs={"strict_ordering": True}
        )
        await comm.gql_connect()
        await comm.gql_init()

        sub_id = await comm.gql_send(
            type="start",
            payload={
                "query": textwrap.dedent(
                    """
                    subscription op_name($userId: UserId) {
                        on_chat_message_sent(userId: $userId) { event }
                    }
                    """
                ),
                "variables": {"userId": userId},
                "operationName": "op_name",
            },
        )
        return sub_id, comm

    async def trigger_subscription(comm, userId, message):
        """Send a message to user using `send_chat_message` mutation.

        Args:
            comm: Client, instance of WebsocketCommunicator.
            userId: User ID for `send_chat_message` mutation.
            message: Any string message.
        """
        msg_id = await comm.gql_send(
            type="start",
            payload={
                "query": textwrap.dedent(
                    """
                    mutation op_name($message: String!, $userId: UserId) {
                        send_chat_message(message: $message, userId: $userId) {
                            message
                        }
                    }
                    """
                ),
                "variables": {"message": message, "userId": userId},
                "operationName": "op_name",
            },
        )
        # Mutation response.
        await comm.gql_receive(assert_id=msg_id, assert_type="data")
        await comm.gql_receive(assert_id=msg_id, assert_type="complete")

    def check_resp(resp, user_id, message):
        """Check the response from `on_chat_message_sent` subscription.

        Args:
            userId: Expected user ID.
            message: Expected message string.
        """
        event = resp["payload"]["data"]["on_chat_message_sent"]["event"]
        assert json.loads(event) == {
            "userId": user_id,
            "payload": message,
        }, "Subscription notification contains wrong data!"

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
    resp = await comm_tom.gql_receive(assert_id=uid_tom, assert_type="data")
    check_resp(resp, UserId[tom_id].value, message)
    # Any other did not receive any notifications.
    await comm_alice.gql_assert_no_response()
    print("Trigger subscription: send message to Alice.")
    message = "Hi, Alice!"
    await trigger_subscription(comm_tom, alice_id, message)
    # Check Alise's notifications.
    resp = await comm_alice.gql_receive(assert_id=uid_alice, assert_type="data")
    check_resp(resp, UserId[alice_id].value, message)
    # Any other did not receive any notifications.
    await comm_tom.gql_assert_no_response()

    print("Trigger subscription: send message to all groups.")
    message = "test... ping..."
    await trigger_subscription(comm_tom, None, message)

    print("Check Tom's and Alice's notifications.")
    resp = await comm_tom.gql_receive(assert_id=uid_tom, assert_type="data")
    check_resp(resp, UserId[tom_id].value, message)
    resp = await comm_alice.gql_receive(assert_id=uid_alice, assert_type="data")
    check_resp(resp, UserId[alice_id].value, message)

    print("Disconnect and wait the application to finish gracefully.")
    await comm_tom.gql_finalize()
    await comm_alice.gql_finalize()


@pytest.mark.asyncio
async def test_keepalive(gql_communicator):
    """Test that server sends keepalive messages."""

    print("Establish & initialize WebSocket GraphQL connection.")
    comm = gql_communicator(
        Query,
        Mutation,
        Subscription,
        consumer_attrs={"strict_ordering": True, "send_keepalive_every": 0.05},
    )
    await comm.gql_connect()
    await comm.gql_init()

    await comm.gql_receive(assert_type="ka")

    print("Receive several keepalive messages.")
    for _ in range(3):
        await comm.gql_receive(assert_type="ka")

    print("Send connection termination message.")
    await comm.gql_send(id=None, type="connection_terminate")

    print("Disconnect and wait the application to finish gracefully.")
    await comm.gql_finalize()


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

        userId = UserId()

    def subscribe(self, info, userId):
        """Specify subscription groups when client subscribes."""
        del info
        assert self is None, "Root `self` expected to be `None`!"
        # Subscribe to the group corresponding to the user.
        if not userId is None:
            return [f"user_{userId}"]
        # Subscribe to default group.
        return []

    def publish(self, info, userId):
        """Publish query result to the subscribers."""
        del info
        event = {"userId": userId, "payload": self}

        return OnChatMessageSent(event=event)

    @classmethod
    def notify(cls, userId, message):
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

    def mutate(self, info, message, userId=None):
        """Send message to the user or all users."""
        del info
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

    def mutate(self, info, userId):
        """Unsubscribe everyone associated with the userId."""
        del info
        assert self is None, "Root `self` expected to be `None`!"

        OnChatMessageSent.unsubscribe(group=f"user_{userId}")

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
