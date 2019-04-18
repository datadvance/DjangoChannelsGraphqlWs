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

"""Check different stress scenarios with subscriptions."""

# NOTE: The GraphQL schema is defined at the end of the file.
# NOTE: In this file we use `strict_ordering=True` to simplify testing.

import asyncio
import concurrent.futures
import itertools
import textwrap
import time
import uuid

import channels_graphql_ws
import graphene
import pytest


@pytest.mark.asyncio
async def test_subscribe_twice_unsubscribe(gql):
    """Test subscribe-unsubscribe behavior with the GraphQL over
    WebSocket.

    0. Subscribe to the same GraphQL subscription twice.
    1. Subscribe to the same GraphQL subscription from another
       communicator.
    2. Send STOP message for the first subscription and unsubscribe.
    3. Execute some mutation.
    4. Check subscription notifications: there are notifications from
       the second and the third subscription.
    """

    print("Establish & initialize two WebSocket GraphQL connections.")
    comm = gql(
        query=Query,
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True},
    )
    comm_new = gql(
        query=Query,
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={"strict_ordering": True},
    )
    await comm.connect_and_init()
    await comm_new.connect_and_init()

    print("Subscribe to GraphQL subscription with the same subscription group.")
    sub_id_1 = await comm.send(
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
    sub_id_2 = await comm.send(
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
    sub_id_new = await comm_new.send(
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

    print("Stop the first subscription by id.")
    await comm.send(id=sub_id_1, type="stop")
    await comm.receive(assert_id=sub_id_1, assert_type="complete")

    print("Trigger the subscription by mutation to receive notifications.")
    message = "HELLO WORLD"
    msg_id = await comm.send(
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
            "variables": {"message": message, "userId": "ALICE"},
            "operationName": "op_name",
        },
    )
    # Mutation response.
    await comm.receive(assert_id=msg_id, assert_type="data")
    await comm.receive(assert_id=msg_id, assert_type="complete")
    # Check responses from subscriptions.
    res = await comm.receive(assert_id=sub_id_2, assert_type="data")
    assert (
        message in res["data"]["on_chat_message_sent"]["event"]
    ), "Wrong response for second subscriber!"
    res = await comm_new.receive(assert_id=sub_id_new, assert_type="data")
    assert (
        message in res["data"]["on_chat_message_sent"]["event"]
    ), "Wrong response for third subscriber!"

    # Check notifications: there are no notifications. Previously,
    # we got all notifications.
    await comm.assert_no_messages()
    await comm_new.assert_no_messages()

    print("Disconnect and wait the application to finish gracefully.")
    await comm.finalize()
    await comm_new.finalize()


@pytest.mark.asyncio
@pytest.mark.parametrize("confirm_subscriptions", [False, True])
@pytest.mark.parametrize("strict_ordering", [False, True])
async def test_subscribe_unsubscribe(gql, confirm_subscriptions, strict_ordering):
    """Test subscribe-unsubscribe behavior with the GraphQL over
    WebSocket.

    During subscribe-unsubscribe messages possible situation when
    we need to change shared data (dict with operation identifier,
    dict with subscription groups, channel_layers data, etc.).
    We need to be sure that the unsubscribe does not destroy
    groups and operation identifiers which we add from another thread.

    So test:
    1) Send subscribe message and many unsubscribe messages
    concurrently.
    2) Check that all requests have been successfully processed.
    """

    print("Establish & initialize two WebSocket GraphQL connections.")
    comm = gql(
        query=Query,
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={
            "confirm_subscriptions": confirm_subscriptions,
            "strict_ordering": strict_ordering,
        },
    )
    await comm.connect_and_init()

    # Flag for communication between threads. If the flag is set, then
    # we have successfully unsubscribed from all subscriptions.
    flag = asyncio.Event()

    async def subscribe_unsubscribe(comm, user_id, op_id: str):
        """Subscribe to GraphQL subscription. And spam server with the
        'stop' messages while the flag is not set.
        """

        sub_id = await comm.send(
            type="start",
            payload={
                "query": textwrap.dedent(
                    """
                    subscription op_name($userId: UserId) {
                        on_chat_message_sent(userId: $userId) { event }
                    }
                    """
                ),
                "variables": {"userId": user_id},
                "operationName": "op_name",
            },
            id=op_id,
        )
        assert sub_id == op_id

        # Multiple stop messages.
        while True:
            await comm.send(id=op_id, type="stop")
            await asyncio.sleep(0.01)
            if flag.is_set():
                break

    async def receiver(op_ids):
        """Handler to receive successful messages about unsubscribing.
        We mark each received message with success and delete the id
        from the 'op_ids' set.
        """
        while True:
            try:
                resp = await comm.receive(raw_response=True)
                op_id = resp["id"]
                if resp["type"] == "complete":
                    op_ids.remove(op_id)
                else:
                    assert resp["type"] == "data" and resp["payload"]["data"] is None, (
                        "This should be a successful subscription message, not '%s'",
                        resp,
                    )
            except asyncio.TimeoutError:
                continue
            except Exception:  # pylint: disable=broad-except
                break
            if flag.is_set():
                break
            if not op_ids:
                # Let's say to other tasks in other threads -
                # that's enough, enough spam.
                print("Ok, all subscriptions are stopped!")
                flag.set()
                break

    print("Prepare tasks for the stress test.")
    number_of_tasks = 18
    # Wait timeout for tasks.
    wait_timeout = 60
    # Generate operations ids for subscriptions. In the future, we will
    # unsubscribe from all these subscriptions.
    op_ids = set()
    # List to collect tasks. We immediately add a handler to receive
    # successful messages.
    awaitables = [receiver(op_ids)]

    op_id = 0
    for user_id in itertools.cycle(["ALICE", "TOM", None]):
        op_id += 1
        op_ids.add(str(op_id))
        awaitables.append(subscribe_unsubscribe(comm, user_id, str(op_id)))
        if number_of_tasks == op_id:
            print("Tasks with the following ids prepared:", op_ids)
            break

    print("Let's run all the tasks concurrently.")
    _, pending = await asyncio.wait(awaitables, timeout=wait_timeout)

    # Check that the server withstood the flow of subscribe-unsubscribe
    # messages and successfully responded to all messages.
    if pending:
        for task in pending:
            task.cancel()
        await asyncio.wait(pending)
        assert False, (
            "Time limit has been reached!"
            " Subscribe-unsubscribe tasks can not be completed!"
        )

    assert not op_ids, "Not all subscriptions have been stopped!"

    # Check notifications: there are no notifications. We unsubscribed
    # from all subscriptions and received all messages.
    await comm.assert_no_messages()

    print("Trigger the subscription by mutation.")
    message = "HELLO WORLD"
    msg_id = await comm.send(
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
            "variables": {"message": message, "userId": "ALICE"},
            "operationName": "op_name",
        },
    )
    # Mutation response.
    await comm.receive(assert_id=msg_id, assert_type="data")
    await comm.receive(assert_id=msg_id, assert_type="complete")

    # Check notifications: there are no notifications. We unsubscribed
    # from all subscriptions and received all messages.
    await comm.assert_no_messages()

    print("Disconnect and wait the application to finish gracefully.")
    await comm.finalize()


@pytest.mark.asyncio
@pytest.mark.parametrize("confirm_subscriptions", [True])
@pytest.mark.parametrize("strict_ordering", [False, True])
async def test_subscribe_unsubscribe_order_messages(
    gql, confirm_subscriptions, strict_ordering
):
    """Test subscribe-unsubscribe behavior with the GraphQL over
    WebSocket.

    We are subscribing and must be sure that at any time after that,
    the subscription stop will be processed correctly.
    We must receive a notification of a successful subscription
    before the message about the successful unsubscribe.

    So test:
    1) Send subscribe message and many unsubscribe 'stop' messages.
    2) Check the order of the confirmation message and the
    'complete' message.
    """

    print("Establish & initialize two WebSocket GraphQL connections.")
    comm = gql(
        query=Query,
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={
            "confirm_subscriptions": confirm_subscriptions,
            "strict_ordering": strict_ordering,
        },
    )
    await comm.connect_and_init()

    async def subscribe_unsubscribe(user_id="TOM"):
        """Subscribe to GraphQL subscription. And spam server with the
        'stop' messages.
        """
        sub_id = await comm.send(
            type="start",
            payload={
                "query": textwrap.dedent(
                    """
                    subscription op_name($userId: UserId) {
                        on_chat_message_sent(userId: $userId) { event }
                    }
                    """
                ),
                "variables": {"userId": user_id},
                "operationName": "op_name",
            },
        )

        # Spam with stop messages.
        for _ in range(50):
            await comm.send(id=sub_id, type="stop")
            await asyncio.sleep(0.001)

        resp = await comm.receive(raw_response=True)
        assert sub_id == resp["id"]
        assert (
            resp["type"] == "data" and resp["payload"]["data"] is None
        ), "First we expect to get a confirmation message!"

        resp = await comm.receive(raw_response=True)
        assert sub_id == resp["id"]
        assert resp["type"] == "complete", (
            "Here we expect to receive a message about the completion"
            " of the unsubscribe!"
        )

    lock = asyncio.Lock()
    loop = asyncio.get_event_loop()
    time_border = 20
    start_time = loop.time()

    print("Start subscribe-unsubscribe iterations.")
    while True:
        # Stop the test if time is up.
        if loop.time() - start_time >= time_border:
            break
        # Start iteration with spam messages.
        async with lock:
            await subscribe_unsubscribe()

    # Check notifications: there are no notifications. We unsubscribed
    # from all subscriptions and received all messages.
    await comm.assert_no_messages()

    print("Disconnect and wait the application to finish gracefully.")
    await comm.finalize()


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [True, False])
@pytest.mark.parametrize("strict_ordering", [False, True])
async def test_subscribe_unsubscribe_all_order_messages(
    gql, strict_ordering, sync, confirm_subscriptions=True
):
    """Test subscribe-unsubscribe behavior with the GraphQL over
    WebSocket.

    We are subscribing and must be sure that at any time after that,
    the subscription stop will be processed correctly.
    We must receive a notification of a successful subscription
    before the message about the successful unsubscribe.

    So test:
    1) Send subscribe message and many unsubscribe messages with
    sync or async 'unsubscribe' method.
    2) Check the order of the confirmation message and the
    'complete' message.
    """

    print("Establish & initialize two WebSocket GraphQL connections.")
    comm = gql(
        query=Query,
        mutation=Mutation,
        subscription=Subscription,
        consumer_attrs={
            "confirm_subscriptions": confirm_subscriptions,
            "strict_ordering": strict_ordering,
        },
    )
    await comm.connect_and_init()

    loop = asyncio.get_event_loop()
    pool = concurrent.futures.ThreadPoolExecutor()

    async def subscribe_unsubscribe(user_id="TOM"):
        """Subscribe to GraphQL subscription. And spam server with the
        'stop' messages using sync 'unsubscribe' method.
        """

        # Just subscribe.
        sub_id = await comm.send(
            type="start",
            payload={
                "query": textwrap.dedent(
                    """
                    subscription op_name($userId: UserId) {
                        on_chat_message_sent(userId: $userId) { event }
                    }
                    """
                ),
                "variables": {"userId": user_id},
                "operationName": "op_name",
            },
        )

        # Spam with stop messages (unsubscribe all behavior).
        if sync:

            def unsubscribe_all():
                """Stop subscription by sync 'unsubscribe' classmethod."""
                for _ in range(50):
                    OnChatMessageSent.unsubscribe()
                    time.sleep(0.01)

            await loop.run_in_executor(pool, unsubscribe_all)
        else:
            for _ in range(50):
                await OnChatMessageSent.unsubscribe()
                await asyncio.sleep(0.01)

        resp = await comm.receive(raw_response=True)
        assert sub_id == resp["id"]
        print(resp)
        assert (
            resp["type"] == "data" and resp["payload"]["data"] is None
        ), "First we expect to get a confirmation message!"

        resp = await comm.receive(raw_response=True)
        assert sub_id == resp["id"]
        print(resp)
        assert resp["type"] == "complete", (
            "Here we expect to receive a message about the completion"
            " of the unsubscribe!"
        )

    lock = asyncio.Lock()
    time_border = 20
    start_time = loop.time()

    print("Start subscribe-unsubscribe iterations.")
    while True:
        # Stop the test if time is up.
        if loop.time() - start_time >= time_border:
            break
        # Start iteration with spam messages.
        async with lock:
            await subscribe_unsubscribe()

    # Check notifications: there are no notifications. We unsubscribed
    # from all subscriptions and received all messages.
    await comm.assert_no_messages()

    print("Disconnect and wait the application to finish gracefully.")
    await comm.finalize()


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

    def subscribe(self, info, userId=None):
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


class Subscription(graphene.ObjectType):
    """GraphQL subscriptions."""

    on_chat_message_sent = OnChatMessageSent.Field()


class Mutation(graphene.ObjectType):
    """GraphQL mutations."""

    send_chat_message = SendChatMessage.Field()


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
