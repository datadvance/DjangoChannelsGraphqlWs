# Copyright (C) DATADVANCE, 2011-2022
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

"""Channels consumer which implements GraphQL WebSocket protocol.

The `GraphqlWsConsumer` is a Channels WebSocket consumer which maintains
WebSocket connection with the client.

Implementation assumes that client uses the protocol implemented by the
library `subscription-transport-ws` (which is used by Apollo).

NOTE: Links based on which this functionality is implemented:
- Protocol description:
  https://github.com/apollographql/subscriptions-transport-ws/blob/master/PROTOCOL.md
  https://github.com/apollographql/subscriptions-transport-ws/blob/master/src/message-types.ts
- ASGI specification for WebSockets:
  https://github.com/django/asgiref/blob/master/specs/www.rst#websocket
- GitHubGist with the root of inspiration:
  https://gist.github.com/tricoder42/af3d0337c1b33d82c1b32d12bd0265ec
"""

import asyncio
import dataclasses
import functools
import inspect
import logging
import time
import traceback
import weakref
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Union,
    cast,
)

import asgiref.sync
import channels.db
import channels.generic.websocket as ch_websocket
import graphene.types.resolver
import graphql
import graphql.error
import graphql.execution
import graphql.pyutils
import graphql.utilities

from .scope_as_context import ScopeAsContext
from .serializer import Serializer

# Module logger.
LOG = logging.getLogger(__name__)

# WebSocket subprotocol used for the GraphQL.
GRAPHQL_WS_SUBPROTOCOL = "graphql-ws"

dict_or_attr_resolver = graphene.types.resolver.dict_or_attr_resolver


async def _subscribe(
    schema: graphql.GraphQLSchema,
    document: graphql.DocumentNode,
    middleware_manager: graphql.MiddlewareManager,
    root_value: Any = None,
    context_value: Any = None,
    variable_values: Optional[Dict[str, Any]] = None,
    operation_name: Optional[str] = None,
    field_resolver: Optional[graphql.GraphQLFieldResolver] = None,
    subscribe_field_resolver: Optional[graphql.GraphQLFieldResolver] = None,
) -> Union[AsyncIterator[graphql.ExecutionResult], graphql.ExecutionResult]:
    """Create a GraphQL subscription.

    This is a copy of function from graphql-core library. It is extended
    with middleware_manager.
    """
    result_or_stream = await graphql.create_source_event_stream(
        schema,
        document,
        root_value,
        context_value,
        variable_values,
        operation_name,
        subscribe_field_resolver,
    )
    if isinstance(result_or_stream, graphql.ExecutionResult):
        return result_or_stream

    async def map_source_to_response(payload: Any) -> graphql.ExecutionResult:
        """Map source to response.

        For each payload yielded from a subscription, map it over the
        normal GraphQL :func:`~graphql.execute` function, with
        `payload` as the `root_value`. This implements the
        "MapSourceToResponseEvent" algorithm described in the GraphQL
        specification. The :func:`~graphql.execute` function provides
        the "ExecuteSubscriptionEvent" algorithm, as it is nearly
        identical to the "ExecuteQuery" algorithm, for which
        :func:`~graphql.execute` is also used.
        """
        result = await graphql.execute(
            schema,
            document,
            payload,
            context_value,
            variable_values,
            operation_name,
            field_resolver,
            middleware=middleware_manager,
        )  # type: ignore
        if inspect.isawaitable(result):
            return cast(graphql.ExecutionResult, await result)
        return cast(graphql.ExecutionResult, result)

    # Map every source value to a ExecutionResult value as described above.
    return graphql.MapAsyncIterator(result_or_stream, map_source_to_response)


class GraphqlWsConsumer(ch_websocket.AsyncJsonWebsocketConsumer):
    """Channels consumer for the WebSocket GraphQL backend.

    NOTE: Each instance of this class maintains one WebSocket
    connection to a single client.

    This class implements the WebSocket-based GraphQL protocol used by
    `subscriptions-transport-ws` library (used by Apollo):
    https://github.com/apollographql/subscriptions-transport-ws/blob/master/PROTOCOL.md
    """

    # ----------------------------------------------------------------- PUBLIC INTERFACE

    # Overwrite this in the subclass to specify the GraphQL schema which
    # processes GraphQL queries.
    schema = None

    # The interval to send keepalive messages to the clients (seconds).
    send_keepalive_every: Optional[int] = None

    # Set to `True` to process requests (i.e. GraphQL documents) from
    # a client in order of arrival, which is the same as sending order,
    # as guaranteed by the WebSocket protocol. This means that request
    # processing for this particular client becomes serial - in other
    # words, the server will not start processing another request
    # before it finishes the current one. Note that requests from
    # different clients (within different WebSocket connections)
    # are still processed asynchronously. Useful for tests.
    strict_ordering: bool = False

    # When set to `True` the server will send an empty data message in
    # response to the subscription. This is needed to let client know
    # when the subscription activates, so he can be sure he doesn't miss
    # any notifications. Disabled by default, cause this is an extension
    # to the original protocol and the client must be tuned accordingly.
    confirm_subscriptions: bool = False

    # The message sent to the client when subscription activation
    # confirmation is enabled.
    subscription_confirmation_message: Dict[str, Any] = {"data": None, "errors": None}

    # The interval given to every operation / resolver to execute in
    # seconds. If an operation or an resolver took longer than that a
    # warning message will be printed in the LOG.
    # `None` value (as well as zero) means "do not print warnings".
    warn_if_operation_took_longer_then: Optional[int] = 1
    warn_if_resolver_took_longer_then: Optional[int] = 1

    # The size of the subscription notification queue. If there are more
    # notifications (for a single subscription) than the given number,
    # then an oldest notification is dropped and a warning is logged.
    subscription_notification_queue_limit: int = 1024

    # A prefix of the Channel group names used for the subscription
    # notifications. You may change this to avoid name clashes in the
    # ASGI backend, e.g. in the Redis.
    group_name_prefix: str = "GRAPHQL_WS_SUBSCRIPTION"

    # GraphQL middleware.
    # Typically a list of functions (callables) like:
    # ```python
    # async def my_middleware(next_middleware, root, info, *args, **kwds):
    #     return await next_middleware(root, info, *args, **kwds)
    # ```
    # For more information read:
    # https://docs.graphene-python.org/en/latest/execution/middleware/#middleware
    middleware: Sequence = []

    # A function to execute synchronous code. By default we rely on
    # the `channels.db.database_sync_to_async` function and the thread
    # pool it uses internally.
    db_sync_to_async: asgiref.sync.SyncToAsync = channels.db.database_sync_to_async

    async def on_connect(self, user, payload):
        """Client connection handler.

        Called after CONNECTION_INIT message from client. Overwrite and
        raise an Exception to tell the server to reject the connection
        when it's necessary.

        Args:
            user: A Django user returned by `get_user`.
            payload: Payload from CONNECTION_INIT message.
        """

    async def get_user(self) -> Optional[Any]:
        """Get Django user instance associated with current connection.

        Returns:
            A Django user or None
        """
        return None

    async def on_operation_start(
        self, scope: Dict[str, Any], operation_id, content
    ) -> bool:
        """Process business logic before operation processing will start.

        For example this hook can check that user session is active (not
        yet expired).

        If you return `False` from this method it is your responsibility
        to call `self.send_json` here.

        Args:
            scope: Dict with connection information.
                    Example: {
                        'type': 'websocket',
                        'path': 'graphql/',
                        'query_string': b'',
                        'headers': [],
                        'subprotocols': ['graphql-ws'],
                        'path_remaining': '',
                        'url_route': {'args': (), 'kwargs': {}},
                        '_scope': {...},
                        'user': {...},
                        'operation_id': '726294ea34344c58b1a298bc16cc9fbe',
                    }
            operation_id: Operation id
            content: Dict with json received through websocket.
        Returns:
            True - if operation is allowed to start.
            False - if operation is forbidden.
        """
        del scope, operation_id, content
        return True

    # ------------------------------------------------------------------- IMPLEMENTATION

    # Subscription implementation may shall return this to tell consumer
    # to suppress subscription notification.
    SKIP = object()

    # Structure that holds subscription information.
    @dataclasses.dataclass
    class _SubInf:
        """Subscription information structure."""

        # Subscription identifier - protocol operation identifier.
        sid: int
        # Subscription groups the subscription belongs to.
        groups: List[str]
        # The subscription notification queue. Required to preserve the
        # order of notifications within a single subscription.
        notification_queue: asyncio.Queue
        # The callback to invoke when client unsubscribes.
        unsubscribed_callback: Callable[[], None]

    def __init__(self, *args, **kwargs):
        """Consumer constructor."""

        assert self.schema is not None, (
            "An attribute 'schema' is not set! Subclasses must specify "
            "the schema which processes GraphQL subscription queries."
        )

        # Registry of active (subscribed) subscriptions.
        self._subscriptions: Dict[
            int, GraphqlWsConsumer._SubInf
        ] = {}  # {'<sid>': '<SubInf>', ...}
        # A task which processes incoming messages and sends them to
        # clients.
        self._subscription_consumers: Dict[int, asyncio.Task] = {}
        self._sids_by_group = {}  # {'<grp>': ['<sid0>', '<sid1>', ...], ...}

        # Task that sends keepalive messages periodically.
        self._keepalive_task = None

        # Background tasks to clean it up when a client disconnects.
        # We use weak collection so finished task will be autoremoved.
        self._background_tasks = weakref.WeakSet()

        # Remember current eventloop so we can check it further in
        # `_assert_thread` method.
        self._eventloop = asyncio.get_event_loop()

        # Crafty weak collection with per-operation locks. It holds a
        # mapping from the operaion id (protocol message id) to the
        # `asyncio.Lock` used to serialize processing of start & stop
        # requests. Since the collection is weak, it automatically
        # throws away items when locks are garbage collected.
        self._operation_locks = weakref.WeakValueDictionary()

        # Offload the GraphQL processing to the worker thread cause
        # according to our experiments even GraphQL document parsing
        # (as well as validation) may take a while (and depends
        # approx. linearly on the size of the selection set).
        self._async_deserialize = self.db_sync_to_async(Serializer.deserialize)
        self._async_graphql_parse = self.db_sync_to_async(graphql.parse)
        self._async_graphql_validate = self.db_sync_to_async(graphql.validate)

        super().__init__(*args, **kwargs)

    # ---------------------------------------------------------- CONSUMER EVENT HANDLERS

    async def connect(self):
        """Handle new WebSocket connection."""
        # Assert we run in a proper thread.
        self._assert_thread()

        # Check the subprotocol told by the client.
        #
        # NOTE: In Python 3.6 `scope["subprotocols"]` was a string, but
        # starting with Python 3.7 it is a bytes. This can be a proper
        # change or just a bug in the Channels to be fixed. So let's
        # accept both variants until it becomes clear.
        assert GRAPHQL_WS_SUBPROTOCOL in (
            (sp.decode() if isinstance(sp, bytes) else sp)
            for sp in self.scope["subprotocols"]
        ), (
            f"WebSocket client does not request for the subprotocol "
            f"{GRAPHQL_WS_SUBPROTOCOL}!"
        )

        # Accept connection with the GraphQL-specific subprotocol.
        await self.accept(subprotocol=GRAPHQL_WS_SUBPROTOCOL)

    async def disconnect(self, code):
        """Handle WebSocket disconnect.

        Remove itself from the Channels groups, clear triggers and stop
        sending keepalive messages.
        """
        # Assert we run in a proper thread.
        self._assert_thread()

        # Print debug or warning message depending on the value of the
        # connection close code. We consider all reserved codes (<999),
        # 1000 "Normal Closure", and 1001 "Going Away" as OK.
        # See: https://developer.mozilla.org/en-US/docs/Web/API/CloseEvent
        if not code:
            LOG.warning("WebSocket connection closed without a code")
        elif code <= 1001:
            LOG.debug("WebSocket connection closed with code: %s.", code)
        else:
            LOG.warning("WebSocket connection closed with code: %s!", code)

        # The list of awaitables to simultaneously wait at the end.
        waitlist = []

        # Unsubscribe from the Channels groups.
        waitlist += [
            self._channel_layer.group_discard(group, self.channel_name)
            for group in self._sids_by_group
        ]

        # Cancel all currently running background tasks.
        for bg_task in self._background_tasks:
            bg_task.cancel()
        waitlist += list(self._background_tasks)

        # Stop sending keepalive messages (if enabled).
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            waitlist += [self._keepalive_task]

        # Stop subscription event processing.
        for consumer_task in self._subscription_consumers.values():
            consumer_task.cancel()
            waitlist.append(consumer_task)

        # Wait for tasks to stop.
        if waitlist:
            waitlist = [asyncio.ensure_future(task) for task in waitlist]
            await asyncio.wait(waitlist)

        self._subscriptions.clear()
        self._sids_by_group.clear()
        self._background_tasks.clear()
        self._subscription_consumers.clear()
        self._keepalive_task = None
        self._operation_locks = weakref.WeakValueDictionary()

    async def receive_json(self, content):  # pylint: disable=arguments-differ
        """Process WebSocket message received from the client.

        NOTE: We force 'STOP' message processing to wait until 'START'
        with the same operation id finishes (if it is running). This
        protects us from a raise conditions which may happen when
        a client stops operation immediately after starting it. An
        illustrative example is a subscribe-unsubscribe pair. If we
        spawn processing of both messages concurrently we can deliver
        subscription confirmation after unsubscription confirmation.
        """
        # Disable this check cause the current version of Pylint
        # improperly complains when we assign a coroutine object to
        # a local variable `task` below.
        # pylint: disable=assignment-from-no-return

        # Assert we run in a proper thread.
        self._assert_thread()

        # Extract message type based on which we select how to proceed.
        msg_type = content["type"].upper()

        if msg_type == "CONNECTION_INIT":
            task = self._on_gql_connection_init(payload=content["payload"])

        elif msg_type == "CONNECTION_TERMINATE":
            task = self._on_gql_connection_terminate()

        elif msg_type == "START":
            op_id = content["id"]

            allowed_to_start = await self.on_operation_start(self.scope, op_id, content)
            if not allowed_to_start:
                return

            # Create and lock a mutex for this particular operation id,
            # so STOP processing for the same operation id will wait
            # until START processing finishes. Locks are stored in a
            # weak collection so we do not have to manually clean it up.
            if op_id in self._operation_locks:
                raise graphql.error.GraphQLError(
                    f"Operation with msg_id={op_id} is already running!"
                )
            op_lock = asyncio.Lock()
            self._operation_locks[op_id] = op_lock
            await op_lock.acquire()

            async def on_start():
                try:
                    await self._on_gql_start(
                        operation_id=op_id, payload=content["payload"]
                    )
                finally:
                    op_lock.release()

            task = on_start()

        elif msg_type == "STOP":
            op_id = content["id"]

            async def on_stop():
                # Will until START message processing finishes, if any.
                async with self._operation_locks.setdefault(op_id, asyncio.Lock()):
                    await self._on_gql_stop(operation_id=op_id)

            task = on_stop()

        else:
            error_msg = f"Message of unknown type '{msg_type}' received!"
            task = self._send_gql_error(
                content["id"] if "id" in content else -1,
                error_msg,
            )
            LOG.warning("GraphQL WS Client error: %s", error_msg)

        # If strict ordering is required then simply wait until the
        # message processing is finished. Otherwise spawn a task so
        # Channels may continue calling `receive_json` while requests
        # (i.e. GraphQL documents) are being processed.
        if self.strict_ordering:
            await task
        else:
            self._spawn_background_task(task)

    async def broadcast(self, message):
        """The broadcast message handler.

        Method is called when new `broadcast` message received from the
        Channels group. The message is sent by `Subscription.broadcast`.

        We search for target group. Then we take a list of subscriptions
        registered for that group. And then we pushing the message to
        subscriptions notification queues. The task that read
        notifications from those queues will receive the message and
        will send it to the client.

        NOTE: There is an issue in the `channels_redis` implementation
        which lead to the possibility to receive broadcast messages in
        wrong order: https://github.com/django/channels_redis/issues/151
        Currently we recommend to monkey-patch the `channels_redis` to
        avoid this.
        """
        # Assert we run in a proper thread.
        self._assert_thread()

        # If strict ordering is required then simply wait until all the
        # broadcast messages are sent. Otherwise spawn a task so this
        # consumer will continue receiving messages.
        if self.strict_ordering:
            await self._process_broadcast(message)
        else:
            self._spawn_background_task(self._process_broadcast(message))

    async def _process_broadcast(self, message):
        """Process the broadcast message.

        NOTE: Depending on the value of the `strict_ordering` setting
        this method is either awaited directly or offloaded to an async
        task by the `broadcast` method (message handler).
        """
        # Assert we run in a proper thread. In particular, we can access
        # the `_subscriptions` and `_sids_by_group` without any locks.
        self._assert_thread()

        group = message["group"]

        # Do nothing if group does not exist. It is quite possible for
        # a client and a backend to concurrently unsubscribe and send
        # notification. And these events do not need to be synchronized.
        if group not in self._sids_by_group:
            return

        payload = message["payload"]

        # Put the payload to the notification queues of subscriptions
        # belonging to the subscription group. Drop the oldest payloads
        # if the `notification_queue` is full.
        for sid in self._sids_by_group[group]:
            subinf = self._subscriptions[sid]
            while True:
                try:
                    subinf.notification_queue.put_nowait(payload)
                    break
                except asyncio.QueueFull:
                    # The queue is full - issue a warning and throw away
                    # the oldest item from the queue.
                    # NOTE: Queue with the size 1 means that it is safe
                    # to drop intermediate notifications.
                    if subinf.notification_queue.maxsize != 1:
                        LOG.warning(
                            "Subscription notification dropped!"
                            " Subscription operation id: %s.",
                            sid,
                        )
                    subinf.notification_queue.get_nowait()

    async def unsubscribe(self, message):
        """The unsubscribe message handler.

        Method is called when new `unsubscribe` message received from
        the Channels group. The message is typically sent by the method
        `Subscription.unsubscribe`. Here we figure out the group message
        received from and stop all the subscriptions in this group.
        """
        # Assert we run in a proper thread.
        self._assert_thread()

        group = message["group"]

        # Do nothing if group does not exist. It is quite possible for
        # a client and a backend to unsubscribe from a subscription
        # concurrently. And these events do not need to be synchronized.
        if group not in self._sids_by_group:
            return

        # Send messages which look like user unsubscribes from all
        # subscriptions in the subscription group. This saves us from
        # thinking about raise condition between subscription and
        # unsubscription.
        await asyncio.wait(
            [
                asyncio.ensure_future(self.receive_json({"type": "stop", "id": sid}))
                for sid in self._sids_by_group[group]
            ]
        )

    # ---------------------------------------------------------- GRAPHQL PROTOCOL EVENTS

    async def _on_gql_connection_init(self, payload):
        """Process the CONNECTION_INIT message.

        Start sending keepalive messages if `send_keepalive_every` set.
        Respond with either CONNECTION_ACK or CONNECTION_ERROR message.

        NOTE: Depending on the value of the `strict_ordering` setting
        this method is either awaited directly or offloaded to an async
        task. See the `receive_json` handler.
        """
        # Assert we run in a proper thread.
        self._assert_thread()

        try:
            # Notify subclass a new client is connected.
            user = await self.get_user()
            await self.on_connect(user, payload)
        except Exception as ex:  # pylint: disable=broad-except
            LOG.exception(str(ex))
            await self._send_gql_connection_error(ex)
            # Close the connection.
            # NOTE: We use the 4000 code because there are two reasons:
            # A) We can not use codes greater than 1000 and less than
            # 3000 because daphne and autobahn do not allow this
            # (see `sendClose` from `autobahn/websocket/protocol.py`
            # and `daphne/ws_protocol.py`).
            # B) https://developer.mozilla.org/en-US/docs/Web/API/CloseEvent
            # So mozilla offers us the following codes:
            # 4000–4999 - Available for use by applications.
            await self.close(code=4000)
        else:
            # Send CONNECTION_ACK message.
            await self._send_gql_connection_ack()
            # If keepalive enabled then send one message immediately and
            # schedule periodic messages.
            if self.send_keepalive_every is not None:

                async def keepalive_sender():
                    """Send keepalive messages periodically."""
                    while True:
                        await asyncio.sleep(self.send_keepalive_every)
                        await self._send_gql_connection_keep_alive()

                self._keepalive_task = asyncio.ensure_future(keepalive_sender())
                # Immediately send keepalive message cause it is
                # required by the protocol description.
                await self._send_gql_connection_keep_alive()

    async def _on_gql_connection_terminate(self):
        """Process the CONNECTION_TERMINATE message.

        NOTE: Depending on the value of the `strict_ordering` setting
        this method is either awaited directly or offloaded to an async
        task. See the `receive_json` handler.
        """
        # Assert we run in a proper thread.
        self._assert_thread()

        # Close the connection.
        await self.close(code=1000)

    async def _on_gql_start(self, operation_id, payload):
        """Process the START message.

        Handle the message which holds query, mutation or subscription
        request.

        NOTE: Depending on the value of the `strict_ordering` setting
        this method is either awaited directly or offloaded to an async
        task. See the `receive_json` handler.
        """
        # Assert we run in a proper thread. In particular, we can access
        # the `_subscriptions` and `_sids_by_group` without any locks.
        self._assert_thread()

        try:
            if operation_id in self._subscriptions:
                raise graphql.error.GraphQLError(
                    f"Subscription with msg_id={operation_id} already exists!"
                )

            # Get the message data.
            query = payload["query"]
            op_name = payload.get("operationName")
            variables = payload.get("variables", {})

            # Create object-like context (like in `Query` or `Mutation`)
            # from the dict-like one provided by the Channels.
            context = ScopeAsContext(self.scope)

            # Adding channel name to the context because it seems to be
            # useful for some use cases, take a loot at the issue from
            # more details:
            # https://github.com/datadvance/DjangoChannelsGraphqlWs/issues/41#issuecomment-663951595
            context.channel_name = self.channel_name
            context.db_sync_to_async = self.db_sync_to_async
            context.group_name_prefix = self.group_name_prefix

            _marked_as_type_query = False

            async def async_resolver_middleware(
                next_middleware, root, info: graphql.GraphQLResolveInfo, *args, **kwds
            ):
                """Wraps resolver calls with `db_sync_to_async`.

                It is highly probable that resolvers will invoke
                blocking DB operations and might be slow.

                By wrapping resolvers with `db_sync_to_async` this
                middleware ensures us that thouse slow operations will
                be executed in separate worker thread.

                Also this middleware able to track execution time of
                resolvers and print warnings about slow resolvers.
                """
                nonlocal _marked_as_type_query
                # The "introspectionQuery" as well as requests for type
                # information might have a lot of nested resolver calls.
                # Since type information requests have no need to access
                # Django database, we will execute them directly. That
                # is faster than ThreadPool execution.
                if (
                    info.field_name == "__schema"
                    and info.return_type
                    and info.return_type.of_type
                    and info.return_type.of_type.name == "__Schema"
                ):
                    _marked_as_type_query = True

                type_request = info.field_name == "__typename"

                # The standard resolver "dict_or_attr_resolver" is known
                # to do just minor inmemory lookups. No need to execute
                # it in separate thread.
                std_resolver = (
                    hasattr(next_middleware, "func")
                    and next_middleware.func
                    == dict_or_attr_resolver  # pylint: disable=comparison-with-callable
                )

                # Execute known "system" resolvers directly. There is no
                # need to measure time of them.
                if _marked_as_type_query or std_resolver or type_request:
                    return next_middleware(root, info, *args, **kwds)

                if asyncio.iscoroutinefunction(next_middleware):
                    next_func = next_middleware
                else:
                    next_func = self.db_sync_to_async(next_middleware)

                if self.warn_if_resolver_took_longer_then:
                    start_time = time.perf_counter()

                result = next_func(root, info, *args, **kwds)
                if inspect.isawaitable(result):
                    result = await result

                if self.warn_if_resolver_took_longer_then:
                    duration = time.perf_counter() - start_time
                    if duration >= self.warn_if_resolver_took_longer_then:
                        # Trying to do some magic to show nice looking
                        # function name in the log. Unfortunately this
                        # is not always possible.
                        try:
                            if isinstance(next_middleware, functools.partial):
                                next_middleware_nice_name = (
                                    f"{next_middleware.func.__qualname__}"
                                )
                            elif hasattr(next_middleware, "__self__"):
                                # it is a bound method with self variable?
                                next_middleware_nice_name = (
                                    f"{next_middleware.__self__.__qualname__}."
                                    f"{next_middleware.__qualname__}"
                                )
                            else:
                                next_middleware_nice_name = (
                                    f"{next_middleware.__qualname__}"
                                )
                        except Exception:  # pylint: disable=broad-except
                            LOG.exception(
                                "Failed to form nice name for a function %s",
                                next_middleware,
                            )
                            next_middleware_nice_name = str(next_middleware)

                        parent_type = info.parent_type.name
                        field_name = info.field_name

                        LOG.warning(
                            "Execution time of resolver %s "
                            "for %s.%s is %.6f "
                            "(operation_id=%s, op_name=%r)",
                            next_middleware_nice_name,
                            parent_type,
                            field_name,
                            duration,
                            operation_id,
                            op_name,
                        )
                return result

            # Process the request with Graphene/GraphQL.

            # Do parsing
            try:
                document_ast = await self._async_graphql_parse(query)
            except graphql.GraphQLError as error:
                LOG.exception("Graphql parse failed")
                raise error

            # Do validation
            validation_errors: List[
                graphql.GraphQLError
            ] = await self._async_graphql_validate(
                self.schema.graphql_schema, document_ast
            )
            if validation_errors:
                LOG.warning(
                    "Graphql validation failed with error: %s", validation_errors
                )
                await self._send_gql_data(operation_id, None, validation_errors)
                await self._send_gql_complete(operation_id)
                return

            op_ast = graphql.utilities.get_operation_ast(
                document_ast, operation_name=op_name
            )
            # If the operation is subscription.
            if op_ast.operation == graphql.language.ast.OperationType.SUBSCRIPTION:
                context.operation_id = operation_id
                # The `register_subscription` is called when a client
                # subscribes.
                context.register_subscription = functools.partial(
                    self._register_subscription, operation_id
                )
                context.middleware_manager_for_subscriptions = (
                    graphql.MiddlewareManager(
                        async_resolver_middleware, *self.middleware
                    )
                )
                LOG.debug(
                    "Starting new subscription (operation_id=%s, op_name=%s)",
                    operation_id,
                    op_name,
                )

                # Normally the `_subscribe` call returns an
                # AsyncGenerator (stream) witch yields a ExecutionResult
                # for every notification. But in case of error it might
                # return just an ExecutionResult.
                result = await _subscribe(
                    self.schema.graphql_schema,
                    document_ast,
                    operation_name=op_name,
                    root_value=None,
                    variable_values=variables,
                    context_value=context,
                    middleware_manager=graphql.MiddlewareManager(
                        async_resolver_middleware, *self.middleware
                    ),
                )

                # Respond with the subscription activation message if
                # enabled in the consumer configuration.
                # NOTE: We intentionally do it before subscribing to the
                # `result` stream. This guarantees that subscription
                # confirmation message is sent before any subscription
                # notifications.
                if self.confirm_subscriptions:
                    await self._send_gql_data(
                        operation_id,
                        data=self.subscription_confirmation_message["data"],
                        errors=self.subscription_confirmation_message["errors"],
                    )

                # If the result is an AsyncGenerator (stream), then
                # listen for incoming events (notifications).
                if hasattr(result, "__aiter__"):
                    consumer_init_lock = asyncio.Lock()
                    await consumer_init_lock.acquire()

                    async def _consume_stream():
                        consumer_init_lock.release()
                        try:
                            async for item in result:
                                try:
                                    await self._send_gql_data(
                                        operation_id, item.data, item.errors
                                    )
                                except asyncio.CancelledError:
                                    break
                        except Exception:  # pylint: disable=broad-except
                            LOG.exception(
                                "Subscription events consumer got exception "
                                "(operation_id=%s).",
                                operation_id,
                            )
                            await self._send_gql_error(
                                operation_id, traceback.format_exc()
                            )

                    # We need to end this task when client drops
                    # connection or unsubscribes, so lets store it.
                    self._subscription_consumers[operation_id] = asyncio.ensure_future(
                        _consume_stream()
                    )

                    # We must be sure here that the subscription
                    # initialization is finished and the stream consumer
                    # is active before we exit this function. Because in
                    # the outer scope with have locking mechanism of
                    # start and stop operations. And we want to say
                    # "subscription operation is started" only when it
                    # actually is.
                    # This allows us to avoid the race condition between
                    # simultaneous subscribe and unsubscribe calls.
                    await consumer_init_lock.acquire()
                    consumer_init_lock.release()
            # If the operation is query or mutation.
            else:
                LOG.debug(
                    "Starting new operation (operation_id=%s, op_name=%s)",
                    operation_id,
                    op_name,
                )

                if self.warn_if_operation_took_longer_then:
                    start_time = time.perf_counter()

                # Standard name for "IntrospectionQuery". We might also
                # check that
                # `document_ast.definitions[0].selection_set.selections[0].name.value`
                # equals to `__schema`. This is a more robust way. But
                # it will eat up more CPU pre each query. For now lets
                # check only query name.
                if op_name == "IntrospectionQuery":
                    # No need to call middlewares for the
                    # IntrospectionQuery. There no real resolvers. Only
                    # the type information.
                    middleware_manager = None
                else:
                    middleware_manager = graphql.execution.MiddlewareManager(
                        async_resolver_middleware, *self.middleware
                    )
                result = graphql.execution.execute(
                    self.schema.graphql_schema,
                    document=document_ast,
                    root_value=None,
                    operation_name=op_name,
                    variable_values=variables,
                    context_value=context,
                    middleware=middleware_manager,
                )
                if inspect.isawaitable(result):
                    result = await result

                if self.warn_if_operation_took_longer_then:
                    duration = time.perf_counter() - start_time
                    if duration >= self.warn_if_operation_took_longer_then:
                        LOG.warning(
                            "Execution time of operation is %.6f "
                            "(operation_id=%r, op_name=%r). "
                            "Debug log contains a full query and variables.",
                            duration,
                            operation_id,
                            op_name,
                        )
                        LOG.debug(
                            "Execution time of operation is %.6f "
                            "(operation_id=%r, op_name=%r, query=%r, vars=%r)",
                            duration,
                            operation_id,
                            op_name,
                            query,
                            variables,
                        )
        except Exception as ex:  # pylint: disable=broad-except
            # Something is wrong - send error message.
            LOG.exception("Graphql operation error (operation_id=%s)", operation_id)

            # In the version of the django-channels-graphql-ws 0.9.1 we
            # returned syntax error in a specific way - as
            # a `'type': 'data'`. Not a `'type': 'error'`. To provide
            # backward compatability lets continue to return error in a
            # same way.
            #
            # If Graphene complains that the request is invalid
            # (e.g. query contains syntax error), then the `data`
            # argument is `None` and the 'errors' argument contains
            # all errors that occurred before or during execution.
            if isinstance(ex, graphql.error.GraphQLSyntaxError):
                # JSON for SyntaxError should look like this: {
                #     'type': 'data',
                #     'id': operation_id,
                #     'payload': {
                #          'data': None,
                #          'errors': [{
                #              'message': 'error message',
                #              'locations': [{'line': 1, 'column': 1}]
                #           }]
                #     }
                # }
                await self._send_gql_data(operation_id, None, [ex])
                await self._send_gql_complete(operation_id)
            else:
                await self._send_gql_error(operation_id, traceback.format_exc())
        else:
            # This is the "else" block of the "try-catch". Here we
            # finalizing processing of the `result` value. And, if
            # required, sending response to the client.

            # Receiving an AsyncIterator (stream) means the subscription
            # has been processed. We should not send anything to the
            # client.
            if hasattr(result, "__aiter__"):
                return

            # Respond to a query or mutation immediately.

            # The `result` is an instance of the `ExecutionResult`.
            await self._send_gql_data(operation_id, result.data, result.errors)

            # Tell the client that the request processing is over.
            await self._send_gql_complete(operation_id)

    async def _register_subscription(
        self,
        operation_id,
        groups,
        publish_callback,
        unsubscribed_callback,
        notification_queue_limit=None,
    ):
        """Register a new subscription when client subscribes.

        This function is called (by the subscription implementation -
        the "resolver" function `_subscribe`) when a client subscribes
        to register necessary callbacks.

        Args:
            operation_id: If of the protocol operation.
            groups: A list of subscription group names to put the
                subscription into.
            publish_callback: Called to process the notification (and to
                invoke GraphQL resolvers). The callback will be
                associated with a Channels groups used to deliver
                messages to the subscription groups current subscription
                belongs to.
            unsubscribed_callback: Called to notify when a client
                unsubscribes.
            notification_queue_limit: Limit for the subscription
                notification queue. Default is used if not set.
        """
        # NOTE: It is important to invoke `group_add` from an
        # eventloop which serves the current consumer.  Besides this is
        # a "proper" way of doing async things, it is also crucial for
        # at least one Channels layer implementation - for the
        # `channels_rabbitmq`. See:
        # https://github.com/CJWorkbench/channels_rabbitmq/issues/3
        # Moreover this allows us to access the `_subscriptions` and
        # `_sids_by_group` without any locks.
        self._assert_thread()

        # The subscription notification queue.
        queue_size = notification_queue_limit
        if not queue_size or queue_size < 0:
            queue_size = self.subscription_notification_queue_limit

        # Note: asyncio.Queue is not thread-safe!
        notification_queue = asyncio.Queue(maxsize=queue_size)

        # Start listening for broadcasts (subscribe to the Channels
        # groups), spawn the notification processing task and put
        # subscription information into the registry.
        # NOTE: Update of `_sids_by_group` & `_subscriptions` must be
        # atomic i.e. without `awaits` in between.
        waitlist = []
        for group in groups:
            self._sids_by_group.setdefault(group, []).append(operation_id)
            waitlist.append(
                asyncio.create_task(
                    self._channel_layer.group_add(group, self.channel_name)
                )
            )
        self._subscriptions[operation_id] = self._SubInf(
            groups=groups,
            sid=operation_id,
            unsubscribed_callback=unsubscribed_callback,
            notification_queue=notification_queue,
        )

        await asyncio.wait(waitlist)

        # For each notification (event) yielded from this function
        # the "_subscribe" function will call normal `graphql.execute`.
        # Middlewares will be executed for that call.
        while True:
            payload = await notification_queue.get()
            data = await self._async_deserialize(payload)
            ret = await publish_callback(data)
            if ret != self.SKIP:
                yield ret
            notification_queue.task_done()

    async def _on_gql_stop(self, operation_id):
        """Process the STOP message.

        Handle an unsubscribe request.

        NOTE: Depending on the value of the `strict_ordering` setting
        this method is either awaited directly or offloaded to an async
        task. See the `receive_json` handler.
        """
        # Assert we run in a proper thread. In particular, we can access
        # the `_subscriptions` and `_sids_by_group` without any locks.
        self._assert_thread()
        LOG.debug("Stop handling operation %r (unsubscribe)", operation_id)

        # Currently only subscriptions can be stopped. But we see but
        # some clients (e.g. GraphiQL) send the stop message even for
        # queries and mutations. We also see that the Apollo server
        # ignores such messages, so we ignore them as well.
        if operation_id not in self._subscriptions:
            return

        waitlist = []

        # Remove the subscription from the registry.
        subinf = self._subscriptions.pop(operation_id)

        # Cancel the task which watches the notification queue.
        consumer_task = self._subscription_consumers.pop(operation_id, None)
        if consumer_task:
            consumer_task.cancel()
            waitlist.append(consumer_task)

        # Stop listening for corresponding groups.
        for group in subinf.groups:
            # Remove the subscription from groups it belongs to. Remove
            # the group itself from the `_sids_by_group` if there are no
            # subscriptions left in it.
            assert self._sids_by_group[group].count(operation_id) == 1, (
                f"Registry is inconsistent: group '{group}' has "
                f"{self._sids_by_group[group].count(operation_id)} "
                "occurrences of operation_id={operation_id}!"
            )
            self._sids_by_group[group].remove(operation_id)
            if not self._sids_by_group[group]:
                del self._sids_by_group[group]
                waitlist.append(
                    self._channel_layer.group_discard(group, self.channel_name)
                )

        waitlist = [asyncio.ensure_future(task) for task in waitlist]
        await asyncio.wait(waitlist)

        await subinf.unsubscribed_callback()

        # Send the unsubscription confirmation message.
        await self._send_gql_complete(operation_id)

    # -------------------------------------------------------- GRAPHQL PROTOCOL MESSAGES

    async def _send_gql_connection_ack(self):
        """Sent in reply to the `connection_init` request."""
        self._assert_thread()
        await self.send_json({"type": "connection_ack"})

    async def _send_gql_connection_error(self, error: Exception):
        """Connection error sent in reply to the `connection_init`."""
        self._assert_thread()
        await self.send_json(
            {"type": "connection_error", "payload": self._format_error(error)}
        )

    async def _send_gql_data(
        self, operation_id, data: Optional[Dict], errors: Optional[Sequence[Exception]]
    ):
        """Send GraphQL `data` message to the client.

        Args:
            data: Dict with GraphQL query response.
            errors: List of exceptions occurred during processing the
                GraphQL query. (Errors happened in resolvers.)
        """
        self._assert_thread()
        # Log errors with tracebacks so we can understand what happened
        # in a failed resolver.
        for ex in errors or []:
            # Typical exception here is `GraphQLLocatedError` which has
            # reference to the original error raised from a resolver.
            tb = ex.__traceback__
            LOG.warning(
                "GraphQL resolver failed (operation_id=%s):\n%s",
                operation_id,
                "".join(traceback.format_exception(type(ex), ex, tb)).strip(),
            )

        await self.send_json(
            {
                "type": "data",
                "id": operation_id,
                "payload": {
                    "data": data,
                    **(
                        {
                            "errors": [  # type: ignore
                                self._format_error(e) for e in errors
                            ]
                        }
                        if errors
                        else {}
                    ),
                },
            }
        )

    async def _send_gql_error(self, operation_id, error: str):
        """Tell client there is a query processing error.

        Server sends this message upon a failing operation.
        It can be an unexpected or unexplained GraphQL execution error
        or a bug in the code. It is unlikely that this is GraphQL
        validation errors (such errors are part of data message and
        must be sent by the `_send_gql_data` method).

        Args:
            operation_id: Id of the operation that failed on the server.
            error: String with the information about the error.

        """
        self._assert_thread()
        LOG.warning("Send error (operation_id=%s): %s", operation_id, error)
        await self.send_json(
            {"type": "error", "id": operation_id, "payload": {"errors": [error]}}
        )

    async def _send_gql_complete(self, operation_id):
        """Send GraphQL `complete` message to the client.

        Args:
            operation_id: If of the corresponding operation.

        """
        self._assert_thread()
        await self.send_json({"type": "complete", "id": operation_id})

    async def _send_gql_connection_keep_alive(self):
        """Send the keepalive (ping) message."""
        self._assert_thread()
        await self.send_json({"type": "ka"})

    # ------------------------------------------------------------------------ AUXILIARY

    @staticmethod
    def _format_error(error: Exception) -> graphql.GraphQLFormattedError:
        """Format given exception `error` to send over a network."""
        if isinstance(error, graphql.error.GraphQLError):
            return error.formatted

        return {
            "message": f"{type(error).__name__}: {str(error)}",
            "extensions": {"code": type(error).__name__},
        }

    def _assert_thread(self):
        """Assert called from our thread with the eventloop."""

        assert asyncio.get_event_loop() == self._eventloop, (
            "Function is called from an inappropriate thread! This"
            " function must be called from the thread where the"
            " eventloop serving the consumer is running."
        )

    def _spawn_background_task(self, awaitable):
        """Spawn background task.

        Tasks are canceled and awaited when a client disconnects.
        Args:
            awaitable: An awaitable to run in a task.
        Returns:
            A started `asyncio.Task` instance.

        """
        background_task = asyncio.ensure_future(awaitable)
        self._background_tasks.add(background_task)

        return background_task

    @property
    def _channel_layer(self):
        """Channel layer."""
        # We cannot simply check existence of channel layer in the
        # consumer constructor, so we added this property.
        assert self.channel_layer is not None, "Channel layer is not configured!"
        return self.channel_layer
