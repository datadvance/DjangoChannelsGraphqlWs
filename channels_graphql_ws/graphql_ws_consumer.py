# Copyright (C) DATADVANCE, 2011-2023
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
from collections.abc import Sequence
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Union, cast

import asgiref.sync
import channels.db
import channels.generic.websocket as ch_websocket
import django.db.models.query
import graphene
import graphql
import graphql.error
import graphql.execution
import graphql.pyutils
import graphql.utilities

from .dict_as_object import DictAsObject

# Module logger.
LOG = logging.getLogger(__name__)

# WebSocket subprotocol used for the GraphQL.
GRAPHQL_WS_SUBPROTOCOL = "graphql-ws"


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
    schema: graphene.Schema

    # The interval to send keepalive messages to the clients (seconds).
    send_keepalive_every: Optional[float] = None

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
    subscription_confirmation_message: dict[str, Any] = {"data": None, "errors": None}

    # Issue a warning to the log when operation/resolver takes longer
    # than specified number in seconds. None disables the warning.
    warn_operation_timeout: Optional[float] = 1
    warn_resolver_timeout: Optional[float] = 1

    # The size of the subscription notification queue. If there are more
    # notifications (for a single subscription) than the given number,
    # then an oldest notification is dropped and a warning is logged.
    subscription_notification_queue_limit: int = 1024

    # GraphQL middleware.
    # List of functions (callables) like the following:
    # ```python
    # async def my_middleware(next_middleware, root, info, *args, **kwds):
    #     result = next_middleware(root, info, *args, **kwds)
    #     if graphql.pyutils.is_awaitable(result):
    #        result = await result
    #     return result
    # ```
    # For more information read docs:
    # - https://docs.graphene-python.org/en/latest/execution/middleware/#middleware
    # - https://graphql-core-3.readthedocs.io/en/latest/diffs.html#custom-middleware
    # Docs about async middlewares are still missing - read the
    # GraphQL-core sources to know more.
    middleware: Sequence = []

    # A function to execute synchronous resolvers, middlewares, request
    # parsing functions, etc. from asynchronous context. The default is
    # a ASGI thread pool in `channels.db.database_sync_to_async` which
    # cleans up the database connections so resolvers can safely work
    # with a database.
    # https://channels.readthedocs.io/en/latest/topics/databases.html#database-sync-to-async
    # You can redefine this to use designated thread pool or to use
    # `asgiref.sync.sync_to_async` if you are sure your resolvers does
    # not work with the database.
    sync_to_async: asgiref.sync.SyncToAsync = channels.db.database_sync_to_async

    # Subscription implementation shall return this to tell consumer
    # to suppress subscription notification.
    SKIP = object()

    async def on_connect(self, payload):
        """Client connection handler.

        Called after CONNECTION_INIT message from client. Overwrite and
        raise an Exception to tell the server to reject the connection
        when it's necessary.

        Args:
            payload: Payload from CONNECTION_INIT message.
        """
        del payload

    async def on_operation(self, op_id, payload):
        """Process business logic before operation processing starts.

        Useful e.g. to check that user session is not yet expired.

        Throw `graphql.error.GraphQLError` to cancel the operation.

        Args:
            op_id: Operation id.
            payload: Payload of the operation.
        """
        del op_id, payload

    # ------------------------------------------------------------------- IMPLEMENTATION

    # A prefix of Channel groups with subscription notifications.
    group_name_prefix: str = "GQLWS"

    # Structure that holds subscription information.
    @dataclasses.dataclass
    class _SubInf:
        """Subscription information structure."""

        # Subscription identifier - protocol operation identifier.
        sid: int
        # Subscription groups the subscription belongs to.
        groups: list[str]
        # A function which triggets subscription.
        enqueue_notification: Callable[[Any], None]
        # The callback to invoke when client unsubscribes.
        unsubscribed_callback: Callable[..., Awaitable[None]]

    def __init__(self, *args, **kwargs):
        """Consumer constructor."""

        assert self.schema is not None, (
            "An attribute 'schema' is not set! Subclasses must specify "
            "the schema which processes GraphQL subscription queries."
        )

        # Registry of active (subscribed) subscriptions.
        self._subscriptions: dict[
            int, GraphqlWsConsumer._SubInf
        ] = {}  # {'<sid>': '<SubInf>', ...}
        self._sids_by_group = {}  # {'<grp>': ['<sid0>', '<sid1>', ...], ...}

        # Tasks which send notifications to clients indexed by an
        # operation/subscription id.
        self._notifier_tasks: dict[int, asyncio.Task] = {}

        # Task that sends keepalive messages periodically.
        self._keepalive_task = None

        # Background tasks to clean it up when a client disconnects.
        # We use weak collection so finished task will be autoremoved.
        self._background_tasks: weakref.WeakSet = weakref.WeakSet()

        # Crafty weak collection with per-operation locks. It holds a
        # mapping from the operaion id (protocol message id) to the
        # `asyncio.Lock` used to serialize processing of start & stop
        # requests. Since the collection is weak, it automatically
        # throws away items when locks are garbage collected.
        self._operation_locks: weakref.WeakValueDictionary = (
            weakref.WeakValueDictionary()
        )

        super().__init__(*args, **kwargs)

    # ---------------------------------------------------------- CONSUMER EVENT HANDLERS

    async def connect(self):
        """Handle new WebSocket connection."""

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

        # Print debug or warning message depending on the value of the
        # connection close code. We consider all reserved codes (<999),
        # 1000 "Normal Closure", and 1001 "Going Away" as OK.
        # See: https://developer.mozilla.org/en-US/docs/Web/API/CloseEvent
        if not code:
            LOG.warning("WebSocket connection closed without a code!")
        elif code <= 1001:
            LOG.debug("WebSocket connection closed with code: %s.", code)
        else:
            LOG.warning("WebSocket connection closed with code: %s!", code)

        # The list of awaitables to simultaneously wait at the end.
        waitlist: list[asyncio.Task] = []

        # Unsubscribe from the Channels groups.
        waitlist += [
            asyncio.create_task(
                self._channel_layer.group_discard(group, self.channel_name)
            )
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

        # Stop tasks which listen to GraphQL lib and send notifications.
        for notifier_task in self._notifier_tasks.values():
            notifier_task.cancel()
            waitlist += [notifier_task]

        # Wait for tasks to stop.
        if waitlist:
            await asyncio.wait(waitlist)

        self._background_tasks.clear()
        self._keepalive_task = None
        self._notifier_tasks.clear()
        self._operation_locks.clear()
        self._sids_by_group.clear()
        self._subscriptions.clear()

    async def receive_json(self, content):  # pylint: disable=arguments-differ
        """Process WebSocket message received from the client.

        NOTE: We force 'STOP' message processing to wait until 'START'
        with the same operation id finishes (if it is running). This
        protects us from race conditions which may happen when a client
        stops operation immediately after starting it. An illustrative
        example is a subscribe-unsubscribe pair. If we spawn processing
        of both messages concurrently we can deliver subscription
        confirmation after unsubscription confirmation.
        """

        # Extract message type based on which we select how to proceed.
        msg_type = content["type"].upper()

        if msg_type == "CONNECTION_INIT":
            task = self._on_gql_connection_init(payload=content["payload"])

        elif msg_type == "CONNECTION_TERMINATE":
            task = self._on_gql_connection_terminate()

        elif msg_type == "START":
            op_id = content["id"]

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
                    # User hook which raises to cancel processing.
                    await self.on_operation(op_id, payload=content["payload"])
                    # START message processing.
                    await self._on_gql_start(op_id, payload=content["payload"])
                except Exception as ex:  # pylint: disable=broad-except
                    await self._send_gql_error(op_id, ex)
                finally:
                    op_lock.release()

            task = on_start()

        elif msg_type == "STOP":
            op_id = content["id"]

            async def on_stop():
                # Wait until START message processing finishes, if any.
                async with self._operation_locks.setdefault(op_id, asyncio.Lock()):
                    await self._on_gql_stop(op_id)

            task = on_stop()

        else:
            task = self._send_gql_error(
                content["id"] if "id" in content else None,
                Exception(f"Wrong message type '{msg_type}'!"),
            )

        # If strict ordering is required then simply wait until the
        # message processing finishes. Otherwise spawn a task so
        # Channels may continue calling `receive_json` while requests
        # (i.e. GraphQL documents) are being processed.
        if self.strict_ordering:
            await task
        else:
            self._spawn_background_task(task)

    async def broadcast(self, message):
        """The broadcast message handler.

        Method is called when new `broadcast` message (sent by
        `Subscription.broadcast`) received from the Channels group.

        """
        # If strict ordering is required then simply wait until all the
        # broadcast messages are sent. Otherwise spawn a task so this
        # consumer will continue receiving messages.
        if self.strict_ordering:
            await self._process_broadcast(message)
        else:
            self._spawn_background_task(self._process_broadcast(message))

    async def _process_broadcast(self, message):
        """Process the broadcast message.

        This triggers subscription notification to all the subscriptions
        belonging to the group received in the `message`.

        NOTE: Depending on the value of the `strict_ordering` setting
        this method is either awaited directly or offloaded to an async
        task by the `broadcast` method (message handler).
        """
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
            subinf.enqueue_notification(payload)

    async def unsubscribe(self, message):
        """The unsubscribe message handler.

        Method is called when new `unsubscribe` message received from
        the Channels group. The message is typically sent by the method
        `Subscription.unsubscribe`. Here we figure out the group message
        received from and stop all the subscriptions in this group.
        """
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
        if self._sids_by_group[group]:
            await asyncio.wait(
                [
                    asyncio.create_task(self.receive_json({"type": "stop", "id": sid}))
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
        try:
            # Notify subclass a new client is connected.
            await self.on_connect(payload)
        except Exception as ex:  # pylint: disable=broad-except
            await self._send_gql_connection_error(ex)
            # Close the connection. NOTE: We use the 4000 code because
            # there are two reasons: A) We can not use codes greater
            # than 1000 and less than 3000 because Daphne and Autobahn
            # do not allow this (see `sendClose` from
            # `autobahn/websocket/protocol.py` and
            # `daphne/ws_protocol.py`). B)
            # https://developer.mozilla.org/en-US/docs/Web/API/CloseEvent
            # Mozilla offers codes 4000–4999 available for all apps.
            await self.close(code=4000)
        else:
            # Send CONNECTION_ACK message.
            await self._send_gql_connection_ack()
            # If keepalive enabled then send one message immediately and
            # schedule periodic messages.
            if self.send_keepalive_every is not None:
                send_keepalive_every = self.send_keepalive_every

                async def keepalive_sender():
                    """Send keepalive messages periodically."""
                    while True:
                        await asyncio.sleep(send_keepalive_every)
                        await self._send_gql_connection_keep_alive()

                self._keepalive_task = asyncio.create_task(keepalive_sender())
                # Immediately send keepalive message cause it is
                # required by the protocol description.
                await self._send_gql_connection_keep_alive()

    async def _on_gql_connection_terminate(self):
        """Process the CONNECTION_TERMINATE message.

        NOTE: Depending on the value of the `strict_ordering` setting
        this method is either awaited directly or offloaded to an async
        task. See the `receive_json` handler.
        """

        # Close the connection.
        await self.close(code=1000)

    async def _on_gql_start(self, op_id, payload):
        """Process the START message.

        Handle the message with query, mutation or subscription request.

        NOTE: Depending on the value of the `strict_ordering` setting
        this method is either awaited directly or offloaded to an async
        task. See the `receive_json` handler.
        """
        try:
            if op_id in self._subscriptions:
                raise graphql.error.GraphQLError(
                    f"Subscription with msg_id={op_id} already exists!"
                )

            # Get the message data.
            query = payload["query"]
            op_name = payload.get("operationName")
            variables = payload.get("variables", {})

            # Prepare a context object.
            context = DictAsObject({})
            context.graphql_ws_consumer = self
            context.graphql_operation_id = op_id
            context.graphql_operation_name = op_name
            context.channels_consumer = self
            context.channels_scope = self.scope
            context.channel_name = self.channel_name

            # Process the request with Graphene and GraphQL-core.
            doc_ast, op_ast, errors = await self._on_gql_start__parse_query(
                op_name, query
            )
            if errors:
                await self._send_gql_data(op_id, None, errors)
                await self._send_gql_complete(op_id)
                return
            # Assert values are not None to suppress MyPy complains.
            assert doc_ast is not None
            assert op_ast is not None

            # If the operation is subscription.
            if op_ast.operation == graphql.language.ast.OperationType.SUBSCRIPTION:
                LOG.debug(
                    "Subscription request. Operation ID: %s, operation name: %s.)",
                    op_id,
                    op_name,
                )

                # This returns asynchronous generator or ExecutionResult
                # instance in case of error.
                subscr_result = await self._on_gql_start__subscribe(
                    doc_ast,
                    operation_name=op_name,
                    root_value=None,
                    variable_values=variables,
                    context_value=context,
                    middleware_manager=graphql.MiddlewareManager(
                        self._on_gql_start__root_middleware,
                        *self.middleware,
                    ),
                )

                # When subscr_result is an AsyncGenerator, consume
                # stream of notifications and send them to clients.
                if hasattr(subscr_result, "__aiter__"):
                    stream = cast(AsyncIterator[graphql.ExecutionResult], subscr_result)
                    # Send subscription activation message (if enabled)
                    # NOTE: We do it before reading the the stream
                    # stream to guarantee that no notifications are sent
                    # before the subscription confirmation message.
                    if self.confirm_subscriptions:
                        await self._send_gql_data(
                            op_id,
                            data=self.subscription_confirmation_message["data"],
                            errors=self.subscription_confirmation_message["errors"],
                        )

                    consumer_init_done = asyncio.Event()

                    async def consume_stream():
                        consumer_init_done.set()
                        try:
                            async for item in stream:
                                try:
                                    await self._send_gql_data(
                                        op_id, item.data, item.errors
                                    )
                                except asyncio.CancelledError:
                                    break
                        except Exception as ex:  # pylint: disable=broad-except
                            LOG.debug(
                                "Exception in the subscription GraphQL resolver!"
                                "Operation %s(%s).",
                                op_name,
                                op_id,
                                exc_info=ex,
                            )
                            await self._send_gql_data(op_id, None, [ex])

                    # We need to end this task when client drops
                    # connection or unsubscribes, so lets store it.
                    self._notifier_tasks[op_id] = asyncio.create_task(consume_stream())

                    # We must be sure here that the subscription
                    # initialization is finished and the stream consumer
                    # is active before we exit this function. Because in
                    # the outer scope we have locking mechanism of start
                    # and stop operations. And we want to say
                    # "subscription operation is started" only when it
                    # actually is.
                    # This allows us to avoid the race condition between
                    # simultaneous subscribe and unsubscribe calls.
                    await consumer_init_done.wait()

                # Else (when gql_subscribe returns ExecutionResult
                # containing error) fallback to standard handling below.

            # If the operation is query or mutation.
            else:
                LOG.debug("New query/mutation. Operation %s(%s).", op_name, op_id)

                if self.warn_operation_timeout:
                    start_time = time.perf_counter()

                # Standard name for "IntrospectionQuery". We might also
                # check that
                # `doc_ast.definitions[0].selection_set.selections[0].name.value`
                # equals to `__schema`. This is a more robust way. But
                # it will eat up more CPU pre each query. For now lets
                # check only a query name.
                if op_name == "IntrospectionQuery":
                    # No need to call middlewares for the
                    # IntrospectionQuery. There no real resolvers. Only
                    # the type information.
                    middleware_manager = None
                else:
                    middleware_manager = graphql.execution.MiddlewareManager(
                        self._on_gql_start__root_middleware,
                        *self.middleware,
                    )
                exec_result = graphql.execution.execute(
                    self.schema.graphql_schema,
                    document=doc_ast,
                    root_value=None,
                    operation_name=op_name,
                    variable_values=variables,
                    context_value=context,
                    middleware=middleware_manager,
                )
                if inspect.isawaitable(exec_result):
                    exec_result = await exec_result

                if self.warn_operation_timeout:
                    duration = time.perf_counter() - start_time
                    if duration >= self.warn_operation_timeout:
                        LOG.warning(
                            "Operation %s(%s) took %.6f seconds. Debug"
                            " log contains full operation details.",
                            op_name,
                            op_id,
                            duration,
                        )
                        LOG.debug(
                            "Operation %s(%s) took %.6f seconds. Query:"
                            " %r, variables: %r.",
                            op_name,
                            op_id,
                            duration,
                            query,
                            variables,
                        )
            # Respond to a query or mutation immediately.
            await self._send_gql_data(
                op_id,
                cast(graphql.ExecutionResult, exec_result).data,
                cast(graphql.ExecutionResult, exec_result).errors,
            )
            await self._send_gql_complete(op_id)

        except Exception as ex:  # pylint: disable=broad-except
            if isinstance(ex, graphql.error.GraphQLError):
                # Respond with details of GraphQL execution error.
                LOG.warning(
                    "GraphQL error! Operation %s(%s).", op_name, op_id, exc_info=True
                )
                await self._send_gql_data(op_id, None, [ex])
                await self._send_gql_complete(op_id)
            else:
                # Respond with general error responce.
                await self._send_gql_error(op_id, ex)

    async def _on_gql_start__parse_query(
        self, op_name: str, query: str
    ) -> tuple[
        Optional[graphql.DocumentNode],
        Optional[graphql.OperationDefinitionNode],
        Optional[Sequence[graphql.GraphQLError]],
    ]:
        """Parse and validate GraphQL query.

        It is highly likely that the same operation will be parsed many
        times, so this function is wrapped with LRU cache.

        This async function offloads the GraphQL processing to the
        worker thread cause according to our experiments even GraphQL
        document parsing and validation take a while and depends approx.
        linearly on the size of the selection set.

        This is a part of START message processing routine so the name
        prefixed with `_on_gql_start__` to make this explicit.

        Returns:
            Tuple with three optional fields:
                0: AST of parsed GraphQL document.
                1: GraphQL operation definition.
                2: Sequence of errors.
        """

        res = await self.sync_to_async(self._on_gql_start__parse_query_sync_cached)(
            op_name, query
        )

        doc_ast: Optional[graphql.DocumentNode] = res[0]
        op_ast: Optional[graphql.OperationDefinitionNode] = res[1]
        errors: Optional[Sequence[graphql.GraphQLError]] = res[2]

        return (doc_ast, op_ast, errors)

    @functools.lru_cache(maxsize=128)
    def _on_gql_start__parse_query_sync_cached(
        self, op_name: str, query: str
    ) -> tuple[
        Optional[graphql.DocumentNode],
        Optional[graphql.OperationDefinitionNode],
        Optional[Sequence[graphql.GraphQLError]],
    ]:
        """Parse and validate GraphQL query. Cached sync implementation.

        This is a part of START message processing routine so the name
        prefixed with `_on_gql_start__` to make this explicit.
        """

        # Parsing.
        try:
            doc_ast = graphql.parse(query)
        except graphql.GraphQLError as ex:
            return None, None, [ex]

        # Validation.
        validation_errors: list[graphql.GraphQLError] = graphql.validate(
            self.schema.graphql_schema, doc_ast
        )
        if validation_errors:
            return None, None, validation_errors

        op_ast = graphql.utilities.get_operation_ast(doc_ast, op_name)

        return doc_ast, op_ast, None

    async def _on_gql_start__subscribe(
        self,
        document: graphql.DocumentNode,
        middleware_manager: graphql.MiddlewareManager,
        root_value: Any = None,
        context_value: Any = None,
        variable_values: Optional[dict[str, Any]] = None,
        operation_name: Optional[str] = None,
        field_resolver: Optional[graphql.GraphQLFieldResolver] = None,
        subscribe_field_resolver: Optional[graphql.GraphQLFieldResolver] = None,
    ) -> Union[AsyncIterator[graphql.ExecutionResult], graphql.ExecutionResult]:
        """Create a GraphQL subscription.

        This is a copy of `graphql.execution.subscribe.subscribe` from
        the GraphQL-core library v3.2.3 improved to support middlewares.

        This is a part of START message processing routine so the name
        prefixed with `_on_gql_start__` to make this explicit.
        """

        result_or_stream = await graphql.create_source_event_stream(
            self.schema.graphql_schema,
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

            For each payload yielded from a subscription, map it over
            the normal GraphQL :func:`~graphql.execute` function, with
            `payload` as the `root_value`. This implements the
            "MapSourceToResponseEvent" algorithm described in the
            GraphQL specification. The :func:`~graphql.execute` function
            provides the "ExecuteSubscriptionEvent" algorithm, as it is
            nearly identical to the "ExecuteQuery" algorithm, for which
            :func:`~graphql.execute` is also used.
            """
            result = await graphql.execute(
                self.schema.graphql_schema,
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

        # Map every source value to a ExecutionResult value.
        return graphql.MapAsyncIterator(result_or_stream, map_source_to_response)

    async def _on_gql_start__root_middleware(
        self, next_middleware, root, info: graphql.GraphQLResolveInfo, *args, **kwds
    ):
        """Root middleware injected right before resolver invocation.

        This middleware is here to do two things:
        1. Offload sync resolvers to the thread our of main event loop.
        2. Issue a warning if resolver execution time exceeds a limit.

        It is highly probable that resolvers will invoke
        blocking operations, e.g. database operations. To avoid
        blocking eventloop this method offloads sync resolvers
        to the thread pool wrapping it into sync_to_async.

        Since this middleware always comes first in the list of
        middlewares, it always receives resolver as the first
        argument instead of another middleware.

        This is a part of START message processing routine so the name
        prefixed with `_on_gql_start__` to make this explicit.

        Args:
            resolver: Resolver to "wrap" into this middleware
            root: Anything. Eventually passed to the resolver.
            info: Passed to the resolver.

        Returns:
            Any value: result returned by the resolver.
            AsyncGenerator: when subscription starts.
        """

        # Unwrap resolver from functools.partial or other wrappers.
        real_resolver = self._on_gql_start__unwrap(next_middleware)
        module = getattr(real_resolver, "__module__", "")

        # Do not offload async resolvers and resolvers from
        # GraphQL-core/Graphene since they are not blocking.
        if not module.startswith(("graphql.type.", "graphene.types.")) and (
            not asyncio.iscoroutinefunction(real_resolver)
        ):
            # Offload synchronous resolvers.
            @functools.wraps(next_middleware)
            def wrapped_next_middleware(root, info, *args, **kwds):
                result = next_middleware(root, info, *args, **kwds)
                # Manually evaluate QuerySet, otherwise we will
                # eventually receive SynchronousOnlyOperation error:
                # "You cannot call this from an async context - use a
                # thread or sync_to_async.". This happens when
                # unevaluated QuerySet moves out of its sync context.
                if isinstance(result, django.db.models.query.QuerySet):
                    result = list(result)
                return result

            next_middleware = self.sync_to_async(wrapped_next_middleware)

        # Start measuring resolver execution time.
        if self.warn_resolver_timeout:
            start_time = time.perf_counter()

        # Execute resolver.
        result = next_middleware(root, info, *args, **kwds)
        if inspect.isawaitable(result):
            result = await result

        # Warn about long resolver execution if the time limit exceeds.
        if self.warn_resolver_timeout:
            duration = time.perf_counter() - start_time
            if duration >= self.warn_resolver_timeout:
                resolver_fn = self._on_gql_start__unwrap(next_middleware)
                pretty_name = (
                    f"{resolver_fn.__self__.__qualname__}.{resolver_fn.__qualname__}"
                    if hasattr(resolver_fn, "__self__")
                    else f"{resolver_fn.__qualname__}"
                )
                LOG.warning(
                    "Resolver %s took %.3f seconds (>%.3f)!"
                    " Operation %s(%s), path: %s.",
                    pretty_name,
                    duration,
                    self.warn_resolver_timeout,
                    info.context.graphql_operation_name,
                    info.context.graphql_operation_id,
                    info.path,
                )

        return result

    def _on_gql_start__unwrap(self, fn: Callable) -> Callable:
        """Auxiliary method which unwraps given function.

        This is a part of START message processing routine so the name
        prefixed with `_on_gql_start__` to make this explicit.
        """
        if isinstance(fn, functools.partial):
            fn = self._on_gql_start__unwrap(fn.func)
        elif hasattr(fn, "__wrapped__"):
            fn = self._on_gql_start__unwrap(fn.__wrapped__)
        return fn

    async def _on_gql_start__register_subscription(
        self,
        op_id,
        groups,
        enqueue_notification,
        unsubscribed_callback,
    ):
        """Register a new subscription when client subscribes.

        This function is called from the Subscription._resolver() when a
        client subscribes.

        This is a part of START message processing routine so the name
        prefixed with `_on_gql_start__` to make this explicit.

        Args:
            op_id: GraphQL operation id.
            groups: A list of subscription group names to put the
                subscription into.
            enqueue_notification: A function that triggers the
                subscription notification.
            unsubscribed_callback: Called to notify when a client
                unsubscribes.
        """
        # Start listening for broadcasts (subscribe to the Channels
        # groups), spawn the notification processing task and put
        # subscription information into the registry.
        # NOTE: Update of `_sids_by_group` & `_subscriptions` must be
        # atomic i.e. without `awaits` in between.
        waitlist = []
        for group in groups:
            self._sids_by_group.setdefault(group, []).append(op_id)
            waitlist.append(
                asyncio.create_task(
                    self._channel_layer.group_add(group, self.channel_name)
                )
            )
        self._subscriptions[op_id] = self._SubInf(
            groups=groups,
            sid=op_id,
            unsubscribed_callback=unsubscribed_callback,
            enqueue_notification=enqueue_notification,
        )
        if waitlist:
            await asyncio.wait(waitlist)

    async def _on_gql_stop(self, op_id):
        """Process the STOP message.

        Handle an unsubscribe request.

        NOTE: Depending on the value of the `strict_ordering` setting
        this method is either awaited directly or offloaded to an async
        task. See the `receive_json` handler.
        """
        LOG.debug("Stop handling or unsubscribe operation %s.", op_id)

        # Currently only subscriptions can be stopped. But we see but
        # some clients (e.g. GraphiQL) send the stop message even for
        # queries and mutations. We also see that the Apollo server
        # ignores such messages, so we ignore them as well.
        if op_id not in self._subscriptions:
            return

        waitlist: list[asyncio.Task] = []

        # Remove the subscription from the registry.
        subinf = self._subscriptions.pop(op_id)

        # Cancel the task which watches the notification queue.
        consumer_task = self._notifier_tasks.pop(op_id, None)
        if consumer_task:
            consumer_task.cancel()
            waitlist.append(consumer_task)

        # Stop listening for corresponding groups.
        for group in subinf.groups:
            # Remove the subscription from groups it belongs to. Remove
            # the group itself from the `_sids_by_group` if there are no
            # subscriptions left in it.
            assert self._sids_by_group[group].count(op_id) == 1, (
                f"Registry is inconsistent: group '{group}' has "
                f"{self._sids_by_group[group].count(op_id)} "
                "occurrences of op_id={op_id}!"
            )
            self._sids_by_group[group].remove(op_id)
            if not self._sids_by_group[group]:
                del self._sids_by_group[group]
                waitlist.append(
                    asyncio.create_task(
                        self._channel_layer.group_discard(group, self.channel_name)
                    )
                )

        if waitlist:
            await asyncio.wait(waitlist)

        await subinf.unsubscribed_callback()

        # Send the unsubscription confirmation message.
        await self._send_gql_complete(op_id)

    # -------------------------------------------------------- GRAPHQL PROTOCOL MESSAGES

    async def _send_gql_connection_ack(self):
        """Sent in reply to the `connection_init` request."""
        await self.send_json({"type": "connection_ack"})

    async def _send_gql_connection_error(self, error: Exception):
        """Connection error sent in reply to the `connection_init`."""
        LOG.warning("GraphQL connection error: %s!", error, exc_info=error)
        await self.send_json(
            {"type": "connection_error", "payload": self._format_error(error)}
        )

    async def _send_gql_data(
        self, op_id, data: Optional[dict], errors: Optional[Sequence[Exception]]
    ):
        """Send GraphQL `data` message to the client.

        Args:
            data: Dict with GraphQL query response.
            errors: List of exceptions occurred during processing the
                GraphQL query. (Errors happened in resolvers.)
        """
        # Log errors with tracebacks so we can understand what happened
        # in a failed resolver.
        for ex in errors or []:
            # Typical exception here is `GraphQLLocatedError` which has
            # reference to the original error raised from a resolver.
            tb = ex.__traceback__
            LOG.warning(
                "GraphQL resolver failed! Operation id: %s:\n%s",
                op_id,
                "".join(traceback.format_exception(type(ex), ex, tb)).strip(),
            )

        await self.send_json(
            {
                "type": "data",
                "id": op_id,
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

    async def _send_gql_error(self, op_id, error: Exception):
        """Tell client there is a query processing error.

        Server sends this message upon a failing operation.
        It can be an unexpected or unexplained GraphQL execution error
        or a bug in the code. It is unlikely that this is GraphQL
        validation errors (such errors are part of data message and
        must be sent by the `_send_gql_data` method).

        Args:
            op_id: Id of the operation that failed on the server.
            error: String with the information about the error.

        """
        LOG.warning("Operation %s processing error: %s!", op_id, error, exc_info=error)
        formatted_error = self._format_error(error)
        await self.send_json(
            {
                "type": "error",
                "id": op_id,
                "payload": {"errors": [formatted_error]},
            }
        )

    async def _send_gql_complete(self, op_id):
        """Send GraphQL `complete` message to the client.

        Args:
            op_id: Id of the corresponding operation.

        """
        await self.send_json({"type": "complete", "id": op_id})

    async def _send_gql_connection_keep_alive(self):
        """Send the keepalive (ping) message."""
        await self.send_json({"type": "ka"})

    # ---------------------------------------------------------------------- AUXILIARIES

    @staticmethod
    def _format_error(error: Exception) -> graphql.GraphQLFormattedError:
        """Format given exception `error` to send over a network.

        This function will add the "extensions.code" field containing an
        exception class name. A frontend may use this value to handle
        errors properly.

        If your backend throws an Exception, then an error will be formatted
        for a client like this:
            {
                "id": "NNN",
                "type": "data",
                "payload": {
                    "data": {...},
                    "errors": [{
                        "message": "Test error",
                        "locations": [{"line": NNN, "column": NNN}],
                        "path": ["somepath"],
                        "extensions": {"code": "Exception"}
                    }]
                }
            }

        If you define custom exception class (`class
        CustomErr(Exception)`), then the error code in the "extensions"
        field will equals to the "CustomErr":
            "extensions": {"code": "Exception"}

        There is a special case of errors on connection. They behave
        using same logic: in the "code" field there will be an
        exception class name:
            {
                "payload": {
                    "message": "message from a exception",
                    "extensions": {"code": "UserUnauthenticatedError"}
                },
                "type": "connection_error"
            }

        NOTE: If you need to add more fields to the error, then override
        this function in a subclass. Another way to enrich errors is to
        use a GraphQLError based classes for your exceptions.
        """
        if isinstance(error, graphql.error.GraphQLError):
            if error.extensions and "code" not in error.extensions:
                if error.original_error:
                    error.extensions["code"] = type(error.original_error).__name__
            return error.formatted

        # Usually the GraphQL-core library wraps any exception with
        # GraphQLError. So this code should be unreachable, unless there
        # are some bugs in the library.
        return {
            "message": f"{type(error).__name__}: {str(error)}",
            "extensions": {"code": type(error).__name__},
        }

    def _spawn_background_task(self, awaitable):
        """Spawn background task.

        Tasks are canceled and awaited when a client disconnects.
        Args:
            awaitable: An awaitable to run in a task.
        Returns:
            A started `asyncio.Task` instance.

        """
        background_task = asyncio.create_task(awaitable)
        self._background_tasks.add(background_task)
        return background_task

    @property
    def _channel_layer(self):
        """Channel layer."""
        # We cannot simply check existence of channel layer in the
        # consumer constructor, so we added this property.
        assert self.channel_layer is not None, "Channel layer is not configured!"
        return self.channel_layer
