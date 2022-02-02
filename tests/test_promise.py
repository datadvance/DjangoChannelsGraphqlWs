"""Check different basic scenarios."""

# NOTE: The GraphQL schema is defined at the end of the file.
# NOTE: In this file we use `strict_ordering=True` to simplify testing.

import json
import textwrap
from typing import List
import uuid

import graphene
from promise import Promise
from promise.dataloader import DataLoader
import pytest

import channels_graphql_ws


@pytest.mark.asyncio
async def test_main_usecase(gql):
    """
    Test main use-case with the GraphQL over WebSocket.

    Basically this test is same to test_basic.py, but subscription has one extra field.
    """

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
                    on_chat_message_sent(user_id: ALICE) { event value }
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
    await client.receive(assert_id=msg_id, assert_type="complete")

    # Subscription notification.
    resp = await client.receive(assert_id=sub_id, assert_type="data")
    event = resp["data"]["on_chat_message_sent"]["event"]
    assert json.loads(event) == {
        "user_id": UserId.ALICE,
        "payload": message,
    }, "Subscription notification contains wrong data!"
    value = resp["data"]["on_chat_message_sent"]["value"]
    assert value == ValueDataLoader.VALUE

    print("Disconnect and wait the application to finish gracefully.")
    await client.finalize()


# ---------------------------------------------------------------------- GRAPHQL BACKEND


class UserId(graphene.Enum):
    """User IDs for sending messages."""

    TOM = 0
    ALICE = 1


class ValueDataLoader(DataLoader):
    """Simple dataloader."""

    VALUE = str(uuid.uuid4().hex)

    def batch_load_fn(self, keys):
        # type: (List[str]) -> Promise
        """Returns keys as-is."""
        return Promise.resolve(keys)


class OnChatMessageSent(channels_graphql_ws.Subscription):
    """Test GraphQL subscription.

    Subscribe to receive messages by user ID.
    """

    # pylint: disable=arguments-differ

    event = graphene.JSONString()
    value = graphene.String()

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
        event = {"user_id": user_id, "payload": self}

        return OnChatMessageSent(event=event)

    @classmethod
    def resolve_value(cls, root, info):
        """Resolver to return predefined value which can be tested using dataloader."""
        return ValueDataLoader().load(ValueDataLoader.VALUE)

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
        return ValueDataLoader().load(Query.VALUE)
