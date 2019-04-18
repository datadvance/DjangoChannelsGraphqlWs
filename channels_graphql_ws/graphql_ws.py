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

"""GraphQL over WebSockets implementation with subscriptions.

This module contains implementation of GraphQL WebSocket protocol. The
implementation bases on the Graphene and the Channels 2.

The `Subscription` class itself is a "creative" copy of `Mutation` class
from the Graphene (`graphene/types/mutation.py`).

The `GraphqlWsConsumer` is a Channels WebSocket consumer which maintains
WebSocket connection with the client.

Implementation assumes that client uses the protocol implemented by the
library `subscription-transport-ws` (which is used by Apollo).
"""

# NOTE: The motivation is that currently there is no viable Python-based
# GraphQL subscriptions implementation out of the box. Hopefully there
# is a promising GraphQL WS https://github.com/graphql-python/graphql-ws
# library by the Graphene authors. In particular this pull request
# https://github.com/graphql-python/graphql-ws/pull/9 gives a hope that
# implementation in the current file can be replaced with GraphQL WS one
# day.

# NOTE: Links based on which this functionality is implemented:
# - Protocol description:
# https://github.com/apollographql/subscriptions-transport-ws/blob/master/PROTOCOL.md
# https://github.com/apollographql/subscriptions-transport-ws/blob/master/src/message-types.ts
# - ASGI specification for WebSockets:
# https://github.com/django/asgiref/blob/master/specs/www.rst#websocket
# - GitHubGist with the root of inspiration:
# https://gist.github.com/tricoder42/af3d0337c1b33d82c1b32d12bd0265ec


import asyncio
import collections
import concurrent
import dataclasses
import functools
import hashlib
import inspect
import logging
import traceback
import types
import typing
import weakref

import asgiref.sync
import channels.db
import channels.generic.websocket as ch_websocket
import channels.layers
import django.core.serializers
import django.db
import graphene
import graphene.types.objecttype
import graphene.types.utils
import graphene.utils.get_unbound_function
import graphene.utils.props
import graphql
import graphql.error
import graphql.execution.executors.asyncio
import msgpack
import promise
import rx


# Module logger.
log = logging.getLogger(__name__)

# WebSocket subprotocol used for the GraphQL.
GRAPHQL_WS_SUBPROTOCOL = "graphql-ws"


class Subscription(graphene.ObjectType):
    """Subscription type definition.

    Subclass this class to define a GraphQL subscription. The class
    works with `GraphqlWsConsumer` which maintains a WebSocket
    connection with the client.

    The subclass specifies the following methods. You can define each of
    them as a `@classmethod`, as a `@staticmethod`, or even as a regular
    method (like Graphene typically does). It shall work fine either
    way. NOTE, if you define the method as a regular method (not a
    classmethod or a staticmethod) you will receive the first argument
    (`payload`/`root`) into the `self` argument.

        publish(payload, info, *args, **kwds):
            This method invoked each time subscription "triggers".
            Raising an exception here will lead to sending the
            notification with the error. To suppress the notification
            return `Subscription.SKIP`.
            Required.

            Args:
                payload: The `payload` from the `broadcast` invocation.
                info: The value of `info.context` is a Channels
                    websocket context with all the connection
                    information.
                args, kwds: Values of the GraphQL subscription inputs.
            Returns:
                The same the any Graphene resolver returns. Returning
                a special object `Subscription.SKIP` indicates that this
                notification shall not be sent to the client at all.

        subscribe(root, info, *args, **kwds):
            Called when client subscribes. Define this to do some extra
            work when client subscribes and to group subscriptions into
            different subscription groups. Method signature is the same
            as in other GraphQL "resolver" methods but it may return
            the subscription groups names to put the subscription into.
            Optional.

            Args:
                root: Root resolver object. Typically `None`.
                info: The value of `info.context` is a Channels
                    websocket context with all the connection
                    information.
                args, kwds: Values of the GraphQL subscription inputs.

            Returns:
                The list or tuple of subscription group names this
                subscription instance belongs to. Later the subscription
                will trigger on publishes to any of that groups. If method
                returns None (default behavior) then the subscription is
                only put to the default group (the one which corresponds to
                the `Subscription` subclass).

        unsubscribed(root, info, *args, **kwds):
            Called when client unsubscribes. Define this to be notified
            when client unsubscribes. Optional.

            Args:
                root: Always `None`.
                info: The value of `info.context` is a Channels
                    websocket context with all the connection
                    information.
                args, kwds: Values of the GraphQL subscription inputs.

    The methods enlisted above receives "standard" set of GraphQL
    resolver arguments. The `info` field has `context` which can be used
    to transmit some useful payload between these methods. For example
    if `subscribe` sets `info.context.zen=42` then `publish` will have
    access to this value as `info.context.zen`.

    Static methods of subscription subclass:
        broadcast: Call this method to notify all subscriptions in the
            group. NOTE: If call is in an asynchronous context then await
            the result of call.
        unsubscribe: Call this method to stop all subscriptions in the
            group.
    """

    # ----------------------------------------------------------------------- PUBLIC API

    # Return this from the `publish` to suppress the notification.
    SKIP = object()

    @classmethod
    def broadcast(cls, *, group=None, payload=None):
        """Call this method to notify all subscriptions in the group.

        NOTE: This method can be used in the asynchronous context,
        because it can implicitly return coroutine object!
        Simply await the returned object to notify subscriptions.

        If there is a running event loop in the current OS thread then
        this method returns the coroutine object by calling a
        `broadcast_async()` coroutine function. Otherwise it simply
        executes the `broadcast_sync()` method.

        NOTE: The `payload` argument will be serialized before sending
        to the subscription group.

        Args:
            group: Name of the subscription group which members must be
                notified. `None` means that all the subscriptions of
                type will be triggered.
            payload: The payload delivered to the `publish` handler.

        Returns:
            coroutine: Coroutine object returned by calling the
                `broadcast_async()` if there is a running event loop in
                the current thread. Await this to notify subscriptions.
            None: If there is not a running event loop in the
                current thread.
        """

        try:
            event_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            if event_loop.is_running():
                assert cls._from_coroutine(), (
                    "You cannot await coroutine object in synchronous"
                    " function where there is the running event loop."
                    " Call and await `broadcast()` or `broadcast_async()`"
                    " from coroutine function. Or call `broadcast()` or"
                    " `broadcast_sync()` function in a synchronous context"
                    " from the OS thread where there is no the running"
                    " event loop."
                )
                return cls.broadcast_async(group=group, payload=payload)

        return cls.broadcast_sync(group=group, payload=payload)

    @classmethod
    async def broadcast_async(cls, *, group=None, payload=None):
        """Notifies all subscriptions in the group.

        NOTE: For broadcasting in the synchronous context use the
        `broadcast_sync()` method instead.
        You can also call the `broadcast()` function that either
        returns the coroutine object of this `broadcast_async()`
        coroutine function or executes the `broadcast_sync()` method
        directly.

        NOTE: The `payload` argument will be serialized with MessagePack
        before sending to the subscription group. Also we offload
        potentially long operation with the database to some working
        thread. Channels help us with this by implementing
        `channels.db.database_sync_to_async`.

        Args:
            group: Name of the subscription group which members must be
                notified. `None` means that all the subscriptions of
                type will be triggered.
            payload: The payload delivered to the `publish` handler.
        """

        # Offload to the thread cause it do DB operations and may work
        # slowly.
        db_sync_to_async = channels.db.database_sync_to_async

        # Manually serialize the payload with the MessagePack
        # (https://msgpack.org) like Redis channel layer backend does.
        # We do this here to allow user to transfer Django models inside
        # the `payload`.
        serialized_payload = await db_sync_to_async(Serializer.serialize)(payload)

        # Send the message to the Channels group.
        group = cls._group_name(group)
        group_send = channels.layers.get_channel_layer().group_send
        await group_send(
            group=group,
            message={
                "type": "broadcast",
                "group": group,
                "payload": serialized_payload,
            },
        )

    @classmethod
    def broadcast_sync(cls, *, group=None, payload=None):
        """Notifies all subscriptions in the group.

        NOTE: For broadcasting from the OS thread with a running event
        loop use the `broadcast_async()` method instead.
        You can also call the `broadcast()` function that either
        returns the coroutine object of this `broadcast_async()`
        coroutine function or executes the `broadcast_sync()` method
        directly.

        NOTE: The `payload` argument will be serialized with MessagePack
        before sending to the subscription group.

        Args:
            group: Name of the subscription group which members must be
                notified. `None` means that all the subscriptions of
                type will be triggered.
            payload: The payload delivered to the `publish` handler.
        """

        # Manually serialize the payload with the MessagePack
        # (https://msgpack.org) like Redis channel layer backend does.
        # We do this here to allow user to transfer Django models inside
        # the `payload`.
        serialized_payload = Serializer.serialize(payload)

        # Send the message to the Channels group.
        group = cls._group_name(group)
        group_send = asgiref.sync.async_to_sync(
            channels.layers.get_channel_layer().group_send
        )
        group_send(
            group=group,
            message={
                "type": "broadcast",
                "group": group,
                "payload": serialized_payload,
            },
        )

    @classmethod
    def unsubscribe(cls, *, group=None):
        """Call this method to stop all subscriptions in the group.

        Args:
            group: Name of the subscription group which members must be
                unsubscribed. `None` means that all the client of the
                subscription will be unsubscribed.
        """

        # Send the message to the Channels group.
        group_send = asgiref.sync.async_to_sync(
            channels.layers.get_channel_layer().group_send
        )
        group = cls._group_name(group)
        group_send(group=group, message={"type": "unsubscribe", "group": group})

    @classmethod
    def Field(
        cls, name=None, description=None, deprecation_reason=None, required=False
    ):
        """Represent subscription as a field to "deploy" it."""
        return graphene.Field(
            cls._meta.output,
            args=cls._meta.arguments,
            resolver=cls._meta.resolver,
            name=name,
            description=description,
            deprecation_reason=deprecation_reason,
            required=required,
        )

    # ------------------------------------------------------------------- IMPLEMENTATION

    @classmethod
    def __init_subclass_with_meta__(
        cls,
        subscribe=None,
        publish=None,
        unsubscribed=None,
        output=None,
        arguments=None,
        _meta=None,
        **options,
    ):  # pylint: disable=arguments-differ
        """Prepare subscription when on subclass creation.

        This method is invoked by the superclass `__init__subclass__`.
        It is needed to process class fields, `Meta` and inheritance
        parameters. This is genuine Graphene approach.
        """
        if not _meta:
            _meta = SubscriptionOptions(cls)

        output = output or getattr(cls, "Output", None)
        # Collect fields if output class is not explicitly defined.
        fields = {}
        if not output:
            fields = collections.OrderedDict()
            for base in reversed(cls.__mro__):
                fields.update(
                    graphene.types.utils.yank_fields_from_attrs(
                        base.__dict__, _as=graphene.Field
                    )
                )
            output = cls

        if not arguments:
            input_class = getattr(cls, "Arguments", None)

            if input_class:
                arguments = graphene.utils.props.props(input_class)
            else:
                arguments = {}

        # Get `publish`, `subscribe`, and `unsubscribe` handlers.
        subscribe = subscribe or getattr(cls, "subscribe", None)
        publish = publish or getattr(cls, "publish", None)
        assert publish is not None, (
            f"Subscription '{cls.__qualname__}' does not define a"
            " method 'publish'! All subscriptions must define"
            " 'publish' which processes a GraphQL query!"
        )

        unsubscribed = unsubscribed or getattr(cls, "unsubscribed", None)

        if _meta.fields:
            _meta.fields.update(fields)
        else:
            _meta.fields = fields

        # Auxiliary alias.
        get_function = graphene.utils.get_unbound_function.get_unbound_function

        # pylint: disable=attribute-defined-outside-init
        _meta.arguments = arguments
        _meta.output = output
        _meta.resolver = get_function(cls._subscribe)
        _meta.subscribe = get_function(subscribe)
        _meta.publish = get_function(publish)
        _meta.unsubscribed = get_function(unsubscribed)

        super().__init_subclass_with_meta__(_meta=_meta, **options)

    @classmethod
    def _subscribe(cls, obj, info, *args, **kwds):
        """Subscription request received.

        This is called by the Graphene when a client subscribes.
        """

        # Extract function which associates the callback with the groups
        # and remove it from the context so extra field does not pass to
        # the `subscribe` method of a subclass.
        register = info.context.register
        del info.context.register

        # Attach current subscription to the group corresponding to the
        # concrete class. This allows to trigger all the subscriptions
        # of the current type, by invoking `publish` without setting
        # the `group` argument.
        groups = [cls._group_name()]

        # Invoke the subclass-specified `subscribe` method to get the
        # groups subscription must be attached to.
        if cls._meta.subscribe is not None:
            subclass_groups = cls._meta.subscribe(obj, info, *args, **kwds)
            # Properly handle `async def subscribe`.
            if asyncio.iscoroutinefunction(cls._meta.subscribe):
                subclass_groups = asyncio.get_event_loop().run_until_complete(
                    subclass_groups
                )
            assert subclass_groups is None or isinstance(
                subclass_groups, (list, tuple)
            ), (
                f"Method 'subscribe' returned a value of an incorrect type"
                f" {type(subclass_groups)}! A list, a tuple, or 'None' expected."
            )
            subclass_groups = subclass_groups or []
        else:
            subclass_groups = []

        groups += [cls._group_name(group) for group in subclass_groups]

        # Register callbacks to call `publish` and `unsubscribed`.
        # Function `register` provides an observable which must
        # be returned from here, cause that is what GraphQL expects from
        # the subscription "resolver" functions.
        def publish_callback(payload):
            """Call `publish` with the payload."""
            result = cls._meta.publish(payload, info, *args, **kwds)
            # Properly handle `async def publish`.
            if asyncio.iscoroutinefunction(cls._meta.publish):
                result = asyncio.get_event_loop().run_until_complete(result)
            return result

        def unsubscribed_callback():
            """Call `unsubscribed` with `None`."""
            if cls._meta.unsubscribed is not None:
                cls._meta.unsubscribed(None, info, *args, **kwds)

        return register(groups, publish_callback, unsubscribed_callback)

    @classmethod
    def _group_name(cls, group=None):
        """Group name based on the name of the subscription class."""

        prefix = GraphqlWsConsumer.group_name_prefix
        suffix = f"{cls.__module__}.{cls.__qualname__}"
        if group is not None:
            suffix += "-" + group

        # Wrap the suffix into MD5 to guarantee that the length of the
        # group name is limited. Otherwise Channels will complain about
        # that the group name is wrong (actually is too long).
        suffix_md5 = hashlib.md5()
        suffix_md5.update(suffix.encode("utf-8"))
        suffix_md5 = suffix_md5.hexdigest()

        return f"{prefix}-{suffix_md5}"

    @staticmethod
    def _from_coroutine() -> bool:
        """Determines whether the current function is called from
        a synchronous function or from a coroutine function
        (native coroutine or generator-based coroutine or
        asynchronous generator function).

        NOTE: That it's only recommended to use for debugging,
        not as part of your production code's functionality.
        """

        frame = inspect.currentframe()
        try:
            coroutine_function_flags = (
                inspect.CO_COROUTINE
                | inspect.CO_ASYNC_GENERATOR
                | inspect.CO_ITERABLE_COROUTINE
            )
            return bool(frame.f_back.f_back.f_code.co_flags & coroutine_function_flags)
        finally:
            del frame


class SubscriptionOptions(graphene.types.objecttype.ObjectTypeOptions):
    """Options stored in the Subscription's `_meta` field."""

    arguments = None
    output = None
    subscribe = None
    publish = None


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
    send_keepalive_every = None

    # Set to `True` to process requests (i.e. GraphQL documents) from
    # a client in order of arrival, which is the same as sending order,
    # as guaranteed by the WebSocket protocol. This means that request
    # processing for this particular client becomes serial - in other
    # words, the server will not start processing another request
    # before it finishes the current one. Note that requests from
    # different clients (within different WebSocket connections)
    # are still processed asynchronously. Useful for tests.
    strict_ordering = False

    # When set to `True` the server will send an empty data message in
    # response to the subscription. This is needed to let client know
    # when the subscription activates, so he can be sure he doesn't miss
    # any notifications. Disabled by default, cause this is an extension
    # to the original protocol and the client must be tuned accordingly.
    confirm_subscriptions = False

    # The message sent to the client when subscription activation
    # confirmation is enabled.
    subscription_confirmation_message = {"data": None, "errors": None}

    # The maximum number of threads designated for GraphQL requests
    # processing. `None` means that default value is used. According to
    # the Python documentation it is "the number of processors on the
    # machine, multiplied by 5". See the `ThreadPoolExecutor` docs for
    # details:
    # https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.ThreadPoolExecutor
    max_worker_threads = None

    # The size of the subscription notification queue. If there are more
    # notifications (for a single subscription) than the given number,
    # then an oldest notification is dropped and a warning is logged.
    subscription_notification_queue_limit = 1024

    # A prefix of the Channel group names used for the subscription
    # notifications. You may change this to avoid name clashes in the
    # ASGI backend, e.g. in the Redis.
    group_name_prefix = "GRAPHQL_WS_SUBSCRIPTION"

    async def on_connect(self, payload):
        """Called after CONNECTION_INIT message from client.

        Overwrite and raise an Exception to tell the server
        to reject the connection when it's necessary.

        Args:
            payload: Payload from CONNECTION_INIT message.
        """

    # ------------------------------------------------------------------- IMPLEMENTATION

    # Threadpool used to process requests.
    _workers = concurrent.futures.ThreadPoolExecutor(
        thread_name_prefix="GraphqlWs.", max_workers=max_worker_threads
    )

    # Disable promise's "trampoline" (whatever it means), cause when it
    # is enabled (which is by default) the promise is not thread-safe.
    # This leads to hanging threads and unanswered requests.
    # https://github.com/syrusakbary/promise/issues/57#issuecomment-406778476
    promise.promise.async_instance.disable_trampoline()

    # Structure that holds subscription information.
    @dataclasses.dataclass
    class _SubInf:
        """Subscription information structure."""

        # Subscription identifier - protocol operation identifier.
        sid: int
        # Subscription groups the subscription belongs to.
        groups: typing.List[typing.AnyStr]
        # The subscription notification queue. Required to preserve the
        # order of notifications within a single subscription.
        notification_queue: asyncio.Queue
        # A task which listens to the notification queue and notifies
        # clients. Task triggers subscription `publish` resolvers.
        notifier_task: asyncio.Task
        # The callback to invoke when client unsubscribes.
        unsubscribed_callback: typing.Callable[[], None]

    def __init__(self, *args, **kwargs):
        assert self.schema is not None, (
            "An attribute 'schema' is not set! Subclasses must specify "
            "the schema which processes GraphQL subscription queries."
        )

        # Registry of active (subscribed) subscriptions.
        self._subscriptions = {}  # {'<sid>': '<SubInf>', ...}
        self._sids_by_group = {}  # {'<grp>': ['<sid0>', '<sid1>', ...], ...}

        # Task that sends keepalive messages periodically.
        self._keepalive_task = None

        # Background tasks to clean it up when a client disconnects.
        # We use weak collection so finished task will be autoremoved.
        self._background_tasks = weakref.WeakSet()

        # Remmember current eventloop so we can check it further in
        # `_assert_thread` method.
        self._eventloop = asyncio.get_running_loop()

        # Crafty weak collection with per-operation locks. It holds a
        # mapping from the operaion id (protocol message id) to the
        # `asyncio.Lock` used to serialize processing of start & stop
        # requests. Since the collection is weak, it automatically
        # throws away items when locks are garbage collected.
        self._operation_locks = weakref.WeakValueDictionary()

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
        """WebSocket disconnection handler.

        Remove itself from the Channels groups, clear triggers and stop
        sending keepalive messages.
        """

        # Assert we run in a proper thread.
        self._assert_thread()

        # Print debug or warning message depending on the value of the
        # connection close code. We consider all reserver codes (<999),
        # 1000 "Normal Closure", and 1001 "Going Away" as OK.
        # See: https://developer.mozilla.org/en-US/docs/Web/API/CloseEvent
        if not code:
            log.warning("WebSocket connection closed without a code")
        elif code <= 1001:
            log.debug("WebSocket connection closed with code: %s.", code)
        else:
            log.warning("WebSocket connection closed with code: %s!", code)

        # The list of awaitables to simultaneously wait at the end.
        waitlist = []

        # Unsubscribe from the Channels groups.
        waitlist += [
            self.channel_layer.group_discard(group, self.channel_name)
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

        if waitlist:
            await asyncio.wait(waitlist)

        self._subscriptions.clear()
        self._sids_by_group.clear()
        self._background_tasks.clear()

    async def receive_json(self, content):  # pylint: disable=arguments-differ
        """Process WebSocket message received from the client.

        # NOTE: We force 'STOP' message processing to wait until 'START'
        # with the same operation id finishes (if it is running). This
        # protects us from a rase conditions which may happen when
        # a client stops operation immediately after starting it. An
        # illustrative example is a subscribe-unsubscribe pair. If we
        # spawn processing of both messages concurrently we can deliver
        # subscription confirmation after unsubscription confirmation.
        """

        # Assert we run in a proper thread.
        self._assert_thread()

        # Extract message type based on which we select how to proceed.
        msg_type = content["type"].upper()

        if msg_type == "CONNECTION_INIT":

            async def task():
                await self._on_gql_connection_init(payload=content["payload"])

        elif msg_type == "CONNECTION_TERMINATE":

            async def task():
                await self._on_gql_connection_terminate()

        elif msg_type == "START":
            op_id = content["id"]
            # Create and lock a mutex for this particular operation id,
            # so STOP processing for the same operation id will wail
            # until START processing finishes. Locks are stored in a
            # weak collection so we do not have to manually clean it up.
            if op_id in self._operation_locks:
                raise graphql.error.GraphQLError(
                    f"Operation with id={op_id} is already running!"
                )
            op_lock = asyncio.Lock()
            self._operation_locks[op_id] = op_lock
            await op_lock.acquire()

            async def task():
                try:
                    await self._on_gql_start(
                        operation_id=op_id, payload=content["payload"]
                    )
                finally:
                    op_lock.release()

        elif msg_type == "STOP":
            op_id = content["id"]

            async def task():
                # Will until START message processing finishes, if any.
                async with self._operation_locks.setdefault(op_id, asyncio.Lock()):
                    await self._on_gql_stop(operation_id=op_id)

        else:

            async def task():
                await self._send_gql_error(
                    content["id"], f"Message of unknown type '{msg_type}' received!"
                )

        # If strict ordering is required then simply wait until the
        # message processing is finished. Otherwise spawn a task so
        # Channels may continue calling `receive_json` while requests
        # (i.e. GraphQL documents) are being processed.
        if self.strict_ordering:
            await task()
        else:
            self._spawn_background_task(task())

    async def broadcast(self, message):
        """The broadcast message handler.

        Method is called when new `broadcast` message received from the
        Channels group. The message is sent by `Subscription.broadcast`.
        Here we figure out the group message received from and trigger
        the observable which makes the subscription process the query
        and notify the client.

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
                    log.warning(
                        "Subscription notification dropped!"
                        " Subscription operation id: %s.",
                        sid,
                    )
                    subinf.notification_queue.get_nowait()

    async def unsubscribe(self, message):
        """The unsubscribe message handler.

        Method is called when new `_unsubscribe` message received from
        the Channels group. The message is typically sent by the method
        `Subscription.unsubscribe`. Here we figure out the group message
        received from and stop all the active subscriptions in this
        group.
        """

        # Assert we run in a proper thread.
        self._assert_thread()

        group = message["group"]

        # Assert we run in a proper thread. In particular, we can access
        # the `_subscriptions` and `_sids_by_group` without any locks.
        self._assert_thread()

        # Unsubscribe all active subscriptions current client has in
        # the subscription group `group`.
        await asyncio.wait(
            [self._on_gql_stop(sid) for sid in self._sids_by_group[group]]
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
            await self.on_connect(payload)
        except Exception as e:  # pylint: disable=broad-except
            await self._send_gql_connection_error(self._format_error(e))
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
                    f"Subscription with id={operation_id} already exists!"
                )

            # Get the message data.
            query = payload["query"]
            op_name = payload.get("operationName")
            variables = payload.get("variables", {})

            # Create object-like context (like in `Query` or `Mutation`)
            # from the dict-like one provided by the Channels.
            context = types.SimpleNamespace(**self.scope)

            # The `register` will be called from the worker thread
            # (spawned for a GraphQL processing) when a client
            # subscribes. So we "synchronize" it with `async_to_sync`.
            # NOTE: It is important to invoke `async_to_sync` in the
            # thread with the eventloop which serves this consumer. This
            # assures that IO operations is performed within a single
            # eventloop.
            context.register = asgiref.sync.async_to_sync(
                functools.partial(self._register_subscription, operation_id)
            )

            # Process the request with Graphene/GraphQL.

            # Offload the GraphQL processing to the worker thread cause
            # according to our experiments even GraphQL document parsing
            # may take a while (and depends approx. linearly on the size
            # of the selection set). Moreover it is highly probable that
            # resolvers will invoke blocking DB operations so it is
            # better to offload the whole thing to a worker thread.
            # NOTE: The `lambda` is required to force `AsyncioExecutor`
            # take an eventloop from the worker thread, not the current
            # one.
            result = await self._run_in_worker(
                lambda: graphql.graphql(
                    self.schema,
                    request_string=query,
                    operation_name=op_name,
                    variables=variables,
                    context=context,
                    allow_subscriptions=True,
                    executor=graphql.execution.executors.asyncio.AsyncioExecutor(),
                )
            )

        except Exception:  # pylint: disable=broad-except
            # Something is wrong - send error message.
            await self._send_gql_error(operation_id, traceback.format_exc())

        else:
            # Receiving an observer means the subscription has been
            # processed. Otherwise it is just regular query or mutation.
            if isinstance(result, rx.Observable):
                # Client subscribed to a subscription so we subscribe to
                # the observable returned.

                # Respond with the subscription activation message if
                # enabled in the consumer configuration.
                # NOTE: We intentionally do it before subscrubing to the
                # `result` stream. This guarantees that subscription
                # confirmation message is sent before any subscription
                # notifications.
                if self.confirm_subscriptions:
                    await self._send_gql_data(
                        operation_id,
                        data=self.subscription_confirmation_message["data"],
                        errors=self.subscription_confirmation_message["errors"],
                    )

                # Subscribe to the observable.
                # NOTE: The function `on_next` is called from a
                # threadpool which processes all GraphQL requests. So we
                # wrap `_send_gql_data` into `async_to_sync` here, so
                # eventually `_send_gql_data` will be invoked from the
                # current eventloop.
                send_gql_data = asgiref.sync.async_to_sync(self._send_gql_data)
                result.subscribe(
                    lambda r: send_gql_data(operation_id, r.data, r.errors)
                )

            else:
                # Respond to a query or mutation immediately.

                # If Graphene complains that the request is invalid
                # (e.g. query contains syntax error), then the `data`
                # argument is `None` and the 'errors' argument contains
                # all errors that occurred before or during execution.
                # The `result` is an instance of the `ExecutionResult`.
                await self._send_gql_data(operation_id, result.data, result.errors)
                # Tell the client that the request processing is over.
                await self._send_gql_complete(operation_id)

    async def _register_subscription(
        self, operation_id, groups, publish_callback, unsubscribed_callback
    ):
        """Register a new subscription when client subscribes.

        This function is called (by the subscription implementation -
        "resolver" function `_subscribe`) when a client subscribes to
        register necessary callbacks.

        Args:
            operation_id: If of the protocol operation.
            groups: A list of subscription group names to put the
                subscription into.
            publish_callback: Called to process the notification (invoke
                GraphQL resolvers). The callback will be associated with
                a Channels groups used to deliver messages to the
                subscription groups current subscription belongs to.
            unsubscribed_callback: Called to notify when a client
                unsubscribes.
        """

        # NOTE: It is important to invoke `group_add` from an
        # eventloop which serves the current consumer. Besides
        # this is a "proper" way of doing async things, it is
        # also crucial for at least one Channels layer
        # implementation - for the `channels_rabbitmq`. See:
        # https://github.com/CJWorkbench/channels_rabbitmq/issues/3
        # Moreover this allows us to access the `_subscriptions` and
        # `_sids_by_group` without any locks.
        self._assert_thread()

        # The subject we will trigger on the `broadcast` message.
        trigger = rx.subjects.Subject()

        # The subscription notification queue.
        notification_queue = asyncio.Queue(
            maxsize=self.subscription_notification_queue_limit
        )

        # Start an endless task which listens the `notification_queue`
        # and invokes subscription "resolver" on new notifications.
        async def notifier():
            """Watch the notification queue and notify clients."""

            # Assert we run in a proper thread.
            self._assert_thread()
            while True:
                payload = await notification_queue.get()
                # Run a subscription's `publish` method (invoked by the
                # `trigger.on_next` function) within the threadpool used
                # for processing other GraphQL resolver functions.
                # NOTE: `lambda` is important to run the deserialization
                # in the worker thread as well.
                await self._run_in_worker(
                    lambda: trigger.on_next(Serializer.deserialize(payload))
                )
                # Message processed. This allows `Queue.join` to work.
                notification_queue.task_done()

        # Enqueue the `publish` method execution. But do not notify
        # clients when `publish` returns `SKIP`.
        stream = trigger.map(publish_callback).filter(
            lambda publish_returned: publish_returned is not Subscription.SKIP
        )

        # Start listening - subscribe to the Channels groups.
        waitlist = []
        for group in groups:
            self._sids_by_group.setdefault(group, []).append(operation_id)
            waitlist.append(self.channel_layer.group_add(group, self.channel_name))
        await asyncio.wait(waitlist)

        # Put subscription information into the registry.
        self._subscriptions[operation_id] = self._SubInf(
            groups=groups,
            sid=operation_id,
            unsubscribed_callback=unsubscribed_callback,
            notification_queue=notification_queue,
            notifier_task=self._spawn_background_task(notifier()),
        )

        return stream

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

        # Currently only subscriptions can be stopped. But we see but
        # some clients (e.g. GraphiQL) send the stop message even for
        # queries and mutations. We also see that the Apollo server
        # ignores such messages, so we ignore them as well.
        if not operation_id in self._subscriptions:
            return

        # Unsubscribe:
        # - Throw away the subscription from the registry.
        # - Cancel the task which watches over the notification queue.
        # - Stop listening corresponding groups.
        waitlist = []
        subinf = self._subscriptions.pop(operation_id)
        subinf.notifier_task.cancel()
        waitlist.append(subinf.notifier_task)
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
                    self.channel_layer.group_discard(group, self.channel_name)
                )
        await asyncio.wait(waitlist)

        # Call the subscription class `unsubscribed` handler in a worker
        # thread, cause it may invoke long-running synchronous tasks.
        await self._run_in_worker(subinf.unsubscribed_callback)

        # Send the unsubscription confirmation message.
        await self._send_gql_complete(operation_id)

    # -------------------------------------------------------- GRAPHQL PROTOCOL MESSAGES

    async def _send_gql_connection_ack(self):
        """Sent in reply to the `connection_init` request."""
        self._assert_thread()
        await self.send_json({"type": "connection_ack"})

    async def _send_gql_connection_error(self, error):
        """Connection error sent in reply to the `connection_init`."""
        self._assert_thread()
        await self.send_json({"type": "connection_error", "payload": error})

    async def _send_gql_data(self, operation_id, data, errors):
        """Send GraphQL `data` message to the client.

        Args:
            data: Dict with GraphQL query response.
            errors: List with exceptions occurred during processing the
                GraphQL query. (Errors happened in the resolvers.)
        """
        self._assert_thread()
        await self.send_json(
            {
                "type": "data",
                "id": operation_id,
                "payload": {
                    "data": data,
                    **(
                        {"errors": [self._format_error(e) for e in errors]}
                        if errors
                        else {}
                    ),
                },
            }
        )

    async def _send_gql_error(self, operation_id, error):
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
    def _format_error(error):
        """Format exception `error` to send over a network."""

        if isinstance(error, graphql.error.GraphQLError):
            return graphql.error.format_error(error)

        return {"message": f"{type(error).__name__}: {str(error)}"}

    async def _run_in_worker(self, func):
        """Run a function in a thread with an event loop.

        Run the `func` in a thread within a thread pool and wait for it
        to finish. Each such thread has initialized event loop which is
        NOT running. So the `func` can use it, for example by running
        `asyncio.get_event_loop().run_until_complete(...)`. We also
        cleanup Django database connections when `func` finishes.

        Args:
            func: The function to run in a thread.

        Returns:
            The result of the `func` invocation.
        """

        # Assert we run in a proper thread.
        self._assert_thread()

        def thread_func():
            """Wrap the `func` to initialize evenloop and to cleanup."""

            # Create an eventloop if this worker thread does not have
            # one yet. Eventually each worker will have its own
            # eventloop for `func` can use it.
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Run a given function in a thread.
            try:
                return func()
            finally:
                # The `func` can open the connections to the database
                # so let's close them. Django Channels does the same in
                # the `channels.db.database_sync_to_async`.
                django.db.close_old_connections()

        return await asyncio.get_running_loop().run_in_executor(
            self._workers, thread_func
        )

    def _assert_thread(self):
        """Assert that function is invoked from a thread where eventloop
        serving current consumer is running."""

        assert asyncio.get_running_loop() == self._eventloop, (
            "Function is called from an inappropriate thread! This"
            " function must be called from the thread where the"
            " eventloop serving the consumer is running."
        )

    def _spawn_background_task(self, awaitable):
        """Spawn background task.

        Tasks are cancelled and awaited when a client disconnects.
        Args:
            awaitable: An awaitable to run in a task.
        Returns:
            A started `asyncio.Task` instance.
        """
        background_task = asyncio.create_task(awaitable)
        self._background_tasks.add(background_task)

        return background_task


class Serializer:
    """Serialize/deserialize Python collection with Django models.

    Serialize/deserialize the data with the MessagePack like Redis
    Channels layer backend does.

    If `data` contains Django models, then it is serialized by the
    Django serialization utilities. For details see:
        Django serialization:
            https://docs.djangoproject.com/en/dev/topics/serialization/
        MessagePack:
            https://github.com/msgpack/msgpack-python
    """

    @staticmethod
    def serialize(data):
        """Serialize the `data`."""

        def encode_django_model(obj):
            """MessagePack hook to serialize the Django model."""

            if isinstance(obj, django.db.models.Model):
                return {
                    "__djangomodel__": True,
                    "as_str": django.core.serializers.serialize("json", [obj]),
                }
            return obj

        return msgpack.packb(data, default=encode_django_model, use_bin_type=True)

    @staticmethod
    def deserialize(data):
        """Deserialize the `data`."""

        def decode_django_model(obj):
            """MessagePack hook to deserialize the Django model."""
            if "__djangomodel__" in obj:
                obj = next(
                    django.core.serializers.deserialize("json", obj["as_str"])
                ).object
            return obj

        return msgpack.unpackb(data, object_hook=decode_django_model, raw=False)
