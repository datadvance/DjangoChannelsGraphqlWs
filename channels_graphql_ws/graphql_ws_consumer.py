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
import django.db.models.query
import graphql
import graphql.error
import graphql.execution
import graphql.pyutils
import graphql.utilities

from .private_subscription_context import PrivateSubscriptionContext
from .scope_as_context import ScopeAsContext

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

    # Issue a warning to the log when operation/resolver takes longer
    # than specified number in seconds. None disables printing.
    operation_warn_timeout: Optional[float] = 1
    resolver_warn_timeout: Optional[float] = 1

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
    #     # Write user code here
    #     result = next_middleware(root, info, *args, **kwds)
    #     if graphql.pyutils.is_awaitable(result):
    #        result = await result
    #     return result
    # ```
    # For more information read docs:
    # - https://docs.graphene-python.org/en/latest/execution/middleware/#middleware
    # - https://graphql-core-3.readthedocs.io/en/latest/diffs.html#custom-middleware
    # On async middleware calls read the source code of the GraphQL-core
    # library.
    middleware: Sequence = []

    # A function to execute synchronous code.
    # By default we rely on the `channels.db.database_sync_to_async`
    # function.  Because it clears unused database connections after a
    # wrapped function execution is finished. Actual implementation of
    # the `database_sync_to_async` uses a thread pool inside.
    # For more information read:
    # https://channels.readthedocs.io/en/latest/topics/databases.html#database-sync-to-async
    sync_to_async: asgiref.sync.SyncToAsync = channels.db.database_sync_to_async

    async def on_connect(self, payload):
        """Client connection handler.

        Called after CONNECTION_INIT message from client. Overwrite and
        raise an Exception to tell the server to reject the connection
        when it's necessary.

        Args:
            payload: Payload from CONNECTION_INIT message.
        """
        del payload

    async def on_operation_start(self, operation_id, payload):
        """Process business logic before operation processing will start.

        For example this hook can check that user session is active (not
        yet expired).

        If you want to cancel operation with an error you can throw a
        `graphql.error.GraphQLError` here.

        Args:
            operation_id: Operation id.
            payload: Payload of the operation.
        """
        del operation_id, payload

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
        # A function to call when new notification received.
        receive_notification_callback: Callable[[], None]
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
        self._async_parse_and_validate = self.sync_to_async(self._parse_and_validate)

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
            LOG.warning("WebSocket connection closed without a code")
        elif code <= 1001:
            LOG.debug("WebSocket connection closed with code: %s.", code)
        else:
            LOG.warning("WebSocket connection closed with code: %s!", code)

        # The list of awaitables to simultaneously wait at the end.
        waitlist: List[asyncio.Task] = []

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

        # Stop subscription event processing.
        for consumer_task in self._subscription_consumers.values():
            consumer_task.cancel()
            waitlist.append(consumer_task)

        # Wait for tasks to stop.
        if waitlist:
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
        protects us from race conditions which may happen when a client
        stops operation immediately after starting it. An illustrative
        example is a subscribe-unsubscribe pair. If we spawn processing
        of both messages concurrently we can deliver subscription
        confirmation after unsubscription confirmation.
        """
        # Disable this check cause the current version of Pylint
        # improperly complains when we assign a coroutine object to
        # a local variable `task` below.
        # pylint: disable=assignment-from-no-return

        # Extract message type based on which we select how to proceed.
        msg_type = content["type"].upper()

        if msg_type == "CONNECTION_INIT":
            task = self._on_gql_connection_init(payload=content["payload"])

        elif msg_type == "CONNECTION_TERMINATE":
            task = self._on_gql_connection_terminate()

        elif msg_type == "START":
            op_id = content["id"]

            try:
                await self.on_operation_start(op_id, payload=content["payload"])
            except Exception as ex:  # pylint: disable=broad-except
                await self._send_gql_error(op_id, ex)
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
                Exception(error_msg),
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
        Channels group. The message itself is sent by
        `Subscription.broadcast`.

        NOTE: There is an issue in the `channels_redis` implementation
        which lead to the possibility to receive broadcast messages in
        wrong order: https://github.com/django/channels_redis/issues/151
        Currently we recommend to monkey-patch the `channels_redis` to
        avoid this.
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

        For the group from the message we taking a list of registered
        (active) subscriptions. And then we push the message to each
        subscription notification queue.

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
            subinf.receive_notification_callback(payload)

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

    async def _on_gql_start(self, operation_id, payload):
        """Process the START message.

        Handle the message which holds query, mutation or subscription
        request.

        NOTE: Depending on the value of the `strict_ordering` setting
        this method is either awaited directly or offloaded to an async
        task. See the `receive_json` handler.
        """
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
            # useful for some use cases, take a look at the issue for
            # more details:
            # https://github.com/datadvance/DjangoChannelsGraphqlWs/issues/41#issuecomment-663951595
            context.channel_name = self.channel_name

            async def sync_to_async_middleware(
                next_middleware, root, info: graphql.GraphQLResolveInfo, *args, **kwds
            ):
                """Wraps resolver calls with `sync_to_async`.

                It is highly probable that resolvers will invoke
                blocking DB operations and might be slow.

                By wrapping resolvers with `sync_to_async` this
                middleware ensures us that thouse slow operations will
                be executed in separate worker thread.

                Args:
                    next_middleware: An middleware callable. When called
                        executes a chain of middleware functions. And,
                        in the end, executes a resolver and returns its
                        result back.
                    root: Any value. Passed to the resolver inside
                        `next_middleware` chain.
                    info: graphql.GraphQLResolveInfo. Passed to the
                        resolver inside `next_middleware` chain.

                Returns:
                    Any value: result returned by an resolver.
                    AsyncGenerator: when subscription started.
                """
                # Speed optimizations with some black magic.
                #
                # We know for sure that resolvers for the GraphQL-core
                # and the Graphene libraries are not blocking.
                # And therefore there is no need to wrap them with
                # `sync_to_async`.
                if isinstance(next_middleware, functools.partial):
                    module = getattr(next_middleware.func, "__module__", "")
                else:
                    module = getattr(next_middleware, "__module__", "")
                if module.startswith("graphql.type.") or module.startswith(
                    "graphene.types."
                ):
                    return next_middleware(root, info, *args, **kwds)

                # Check if resolver function needs to be wrapped and
                # wrap it.
                if asyncio.iscoroutinefunction(next_middleware) or (
                    hasattr(next_middleware, "func")
                    and asyncio.iscoroutinefunction(next_middleware.func)
                ):
                    next_func = next_middleware
                else:
                    # The function is sync, so wrap it.abs
                    def wrapped_middleware(root, info, *args, **kwds):
                        resp = next_middleware(root, info, *args, **kwds)

                        # If users code returned an unevaluated
                        # QuerySet, then we should force it to evaluate
                        # here.
                        #
                        # Because we are going to move that QuerySet out
                        # out its sync context. And somewhere later, in
                        # a point of real evaluation the django will
                        # raise the SynchronousOnlyOperation error: "You
                        # cannot call this from an async context - use a
                        # thread or sync_to_async."
                        if isinstance(resp, django.db.models.query.QuerySet):
                            resp = list(resp)
                        return resp

                    # The timeout_warning_middleware will use this to
                    # get nice function name for logs.
                    wrapped_middleware.__orig_func = next_middleware  # type: ignore
                    next_func = self.sync_to_async(wrapped_middleware)

                result = next_func(root, info, *args, **kwds)
                if inspect.isawaitable(result):
                    result = await result
                return result

            async def timeout_warning_middleware(
                next_middleware, root, info: graphql.GraphQLResolveInfo, *args, **kwds
            ):
                """Measures execution time of resolvers and middlewares.

                Prints warning messages for slow resolvers and
                operations.

                Args:
                    next_middleware: An middleware callable. When called
                        executes a chain of middleware functions. And,
                        in the end, executes a resolver and returns its
                        result back.
                    root: Any value. Passed to the resolver inside
                        `next_middleware` chain.
                    info: graphql.GraphQLResolveInfo. Passed to the
                        resolver inside `next_middleware` chain.

                Returns:
                    Any value: result returned by an resolver.
                    AsyncGenerator: when subscription started.
                """
                if self.resolver_warn_timeout:
                    start_time = time.perf_counter()

                result = next_middleware(root, info, *args, **kwds)
                if inspect.isawaitable(result):
                    result = await result

                if self.resolver_warn_timeout:
                    duration = time.perf_counter() - start_time
                    if duration >= self.resolver_warn_timeout:
                        LOG.warning(
                            "Execution time of resolver %s "
                            "for %s.%s is %.6f "
                            "(operation_id=%s, op_name=%r)",
                            _get_nice_name_for_callable(
                                next_middleware,
                                wrapper_func=sync_to_async_middleware,
                            ),
                            info.parent_type.name,
                            info.field_name,
                            duration,
                            operation_id,
                            op_name,
                        )
                return result

            # Process the request with Graphene/GraphQL.
            document_ast, op_ast, error = await self._async_parse_and_validate(
                op_name, query
            )
            if error:
                LOG.exception(
                    "GraphQL query failed. (operation_id=%s, error=%s)",
                    operation_id,
                    error,
                )
                await self._send_gql_data(operation_id, None, error)
                await self._send_gql_complete(operation_id)
                return

            # If the operation is subscription.
            if op_ast.operation == graphql.language.ast.OperationType.SUBSCRIPTION:
                LOG.debug(
                    "New subscription request (operation_id=%s, op_name=%s)",
                    operation_id,
                    op_name,
                )

                # The `self.sync_to_async` and the
                # `self.group_name_prefix` fields are public and could
                # be changed by the user in a `GraphqlWsConsumer`
                # subclass.
                #
                # Both fields are used in a `Subscription`
                # implementation.
                #
                # We pass those values to a `Subscription` through
                # `context`, because there is no access from
                # `Subscription` to real `GraphqlWsConsumer` instance
                # available.

                # pylint: disable=protected-access
                context._channels_graphql_ws = PrivateSubscriptionContext(
                    operation_id=operation_id,
                    sync_to_async=self.sync_to_async,
                    group_name_prefix=self.group_name_prefix,
                    register_subscription=self._register_subscription,
                    subscription_notification_queue_limit=(
                        self.subscription_notification_queue_limit
                    ),
                    middleware_manager_for_subscriptions=(
                        graphql.MiddlewareManager(
                            sync_to_async_middleware,
                            timeout_warning_middleware,
                            *self.middleware,
                        )
                    ),
                )

                # Normally the `_subscribe` call returns an
                # AsyncGenerator (stream). But in case of error it might
                # return an ExecutionResult containing error).
                result = await _subscribe(
                    self.schema.graphql_schema,
                    document_ast,
                    operation_name=op_name,
                    root_value=None,
                    variable_values=variables,
                    context_value=context,
                    middleware_manager=graphql.MiddlewareManager(
                        sync_to_async_middleware,
                        timeout_warning_middleware,
                        *self.middleware,
                    ),
                )

                # If the result is an AsyncGenerator (stream), then
                # consume stream of notifications and send them to
                # clients.
                if hasattr(result, "__aiter__"):
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

                    consumer_init_done = asyncio.Event()

                    async def consume_stream():
                        consumer_init_done.set()
                        try:
                            async for item in result:
                                try:
                                    await self._send_gql_data(
                                        operation_id, item.data, item.errors
                                    )
                                except asyncio.CancelledError:
                                    break
                        except Exception as ex:  # pylint: disable=broad-except
                            LOG.exception(
                                "Subscription events consumer got exception "
                                "(operation_id=%s).",
                                operation_id,
                            )
                            await self._send_gql_data(operation_id, None, [ex])

                    # We need to end this task when client drops
                    # connection or unsubscribes, so lets store it.
                    self._subscription_consumers[operation_id] = asyncio.create_task(
                        consume_stream()
                    )

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

                # Else (when `_subscribe` call returned ExecutionResult
                # containing error) fallback to standard handling below.

            # If the operation is query or mutation.
            else:
                LOG.debug(
                    "New operation (operation_id=%s, op_name=%s)",
                    operation_id,
                    op_name,
                )

                if self.operation_warn_timeout:
                    start_time = time.perf_counter()

                # Standard name for "IntrospectionQuery".
                # We might also check that
                # `document_ast.definitions[0].selection_set.selections[0].name.value`
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
                        sync_to_async_middleware,
                        timeout_warning_middleware,
                        *self.middleware,
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

                if self.operation_warn_timeout:
                    duration = time.perf_counter() - start_time
                    if duration >= self.operation_warn_timeout:
                        LOG.warning(
                            "Operation %r (ID %r) took %.6f seconds. "
                            "Debug log contains a full query and variables.",
                            op_name,
                            operation_id,
                            duration,
                        )
                        LOG.debug(
                            "Operation %r (ID %r) took %.6f seconds. "
                            "(query=%r, vars=%r)",
                            op_name,
                            operation_id,
                            duration,
                            query,
                            variables,
                        )
        except Exception as ex:  # pylint: disable=broad-except
            # Something is wrong - send error message.
            LOG.exception("GraphQL operation error (operation_id=%s)", operation_id)
            if isinstance(ex, graphql.error.GraphQLError):
                await self._send_gql_data(operation_id, None, [ex])
                await self._send_gql_complete(operation_id)
            else:
                await self._send_gql_error(operation_id, ex)
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

    @functools.lru_cache
    def _parse_and_validate(self, op_name, query):
        """Parse and validate query and return ast.

        Returns:
            Tuple:
                document_ast, op_ast, error
        """
        # Do parsing.
        try:
            document_ast = graphql.parse(query)
        except graphql.GraphQLError as ex:
            LOG.exception("GraphQL parse failed.")
            return None, None, [ex]

        # Do validation.
        validation_errors: List[graphql.GraphQLError] = graphql.validate(
            self.schema.graphql_schema, document_ast
        )
        if validation_errors:
            LOG.warning(
                "GraphQL validation failed for operation %s with error: %s.",
                op_name,
                validation_errors,
            )
            return None, None, validation_errors

        op_ast = graphql.utilities.get_operation_ast(
            document_ast, operation_name=op_name
        )
        return document_ast, op_ast, None

    async def _register_subscription(
        self,
        operation_id,
        groups,
        receive_notification_callback,
        unsubscribed_callback,
    ):
        """Register a new subscription when client subscribes.

        This function is called (by the subscription implementation -
        the "resolver" function `_subscribe`) when a client subscribes
        to register necessary callbacks.

        Args:
            operation_id: Id of the protocol operation.
            groups: A list of subscription group names to put the
                subscription into.
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
            receive_notification_callback=receive_notification_callback,
        )
        if waitlist:
            await asyncio.wait(waitlist)

    async def _on_gql_stop(self, operation_id):
        """Process the STOP message.

        Handle an unsubscribe request.

        NOTE: Depending on the value of the `strict_ordering` setting
        this method is either awaited directly or offloaded to an async
        task. See the `receive_json` handler.
        """
        LOG.debug("Stop handling operation %r (unsubscribe)", operation_id)

        # Currently only subscriptions can be stopped. But we see but
        # some clients (e.g. GraphiQL) send the stop message even for
        # queries and mutations. We also see that the Apollo server
        # ignores such messages, so we ignore them as well.
        if operation_id not in self._subscriptions:
            return

        waitlist: List[asyncio.Task] = []

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
                    asyncio.create_task(
                        self._channel_layer.group_discard(group, self.channel_name)
                    )
                )

        if waitlist:
            await asyncio.wait(waitlist)

        await subinf.unsubscribed_callback()

        # Send the unsubscription confirmation message.
        await self._send_gql_complete(operation_id)

    # -------------------------------------------------------- GRAPHQL PROTOCOL MESSAGES

    async def _send_gql_connection_ack(self):
        """Sent in reply to the `connection_init` request."""
        await self.send_json({"type": "connection_ack"})

    async def _send_gql_connection_error(self, error: Exception):
        """Connection error sent in reply to the `connection_init`."""
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
        # Log errors with tracebacks so we can understand what happened
        # in a failed resolver.
        for ex in errors or []:
            # Typical exception here is `GraphQLLocatedError` which has
            # reference to the original error raised from a resolver.
            tb = ex.__traceback__
            LOG.error(
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

    async def _send_gql_error(self, operation_id, error: Exception):
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
        LOG.warning("Send error (operation_id=%s): %s", operation_id, error)
        formatted_error = self._format_error(error)
        await self.send_json(
            {
                "type": "error",
                "id": operation_id,
                "payload": {"errors": [formatted_error]},
            }
        )

    async def _send_gql_complete(self, operation_id):
        """Send GraphQL `complete` message to the client.

        Args:
            operation_id: If of the corresponding operation.

        """
        await self.send_json({"type": "complete", "id": operation_id})

    async def _send_gql_connection_keep_alive(self):
        """Send the keepalive (ping) message."""
        await self.send_json({"type": "ka"})

    # ------------------------------------------------------------------------ AUXILIARY

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

        Note: If you need to add more fields to the error, then override
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


def _get_nice_name_for_callable(func, wrapper_func=None):
    """Get a callable name to show to a developer in logs.

    Unfortunately this is not always possible. Lambda functions, for
    example, have no name.

    Returns:
        str: function name.
    """
    try:
        # If the func variable is the functools.partial with the
        # `sync_to_async_middleware` (wrapper_func) inside, then unwrap
        # it to get a real resolver name.
        if isinstance(func, functools.partial):
            if func.func == wrapper_func:
                return _get_nice_name_for_callable(func.args[0])

        # Unwrap sync_to_async helper wrapper of
        # sync_to_async_middleware.
        if hasattr(func, "__orig_func"):
            return _get_nice_name_for_callable(func.__orig_func)

        if isinstance(func, functools.partial):
            return f"{func.func.__qualname__}"

        if hasattr(func, "__self__"):
            # it is a bound method with self variable?
            return f"{func.__self__.__qualname__}." f"{func.__qualname__}"

        return f"{func.__qualname__}"
    except Exception:  # pylint: disable=broad-except
        LOG.exception(
            "Failed to form nice name for a function %s",
            func,
        )
        return str(func)


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

    This is a copy of a function from the GraphQL-core library (v3.2.3).
    The original version of this code were found in the
    `graphql.execution.subscribe` module - the function `subscribe`.

    This version is extended with the `middleware_manager` argument
    handling.
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

    # Map every source value to a ExecutionResult value
    # as described above.
    return graphql.MapAsyncIterator(result_or_stream, map_source_to_response)
