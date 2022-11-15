# Copyright (C) DATADVANCE, 2010-2022
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

"""Graphene-like subscription class.

The `Subscription` class itself is a "creative" copy of `Mutation` class
from the Graphene (`graphene/types/mutation.py`).
"""

import asyncio
import collections
import hashlib
import inspect
import logging

import channels.db
import channels.layers
import graphene
import graphene.types.objecttype
import graphene.types.utils
import graphene.utils.get_unbound_function
import graphene.utils.props
from asgiref.sync import async_to_sync

from .graphql_ws_consumer import GraphqlWsConsumer
from .serializer import Serializer

# Module logger.
LOG = logging.getLogger(__name__)


class SubscriptionField(graphene.Field):
    """Helper Field class to handle subscriptions."""

    def wrap_subscribe(self, parent_subscribe):
        """Wraps a function "subscribe"."""
        return self.resolver


class Subscription(graphene.ObjectType):
    """Subscription type definition.

    Subclass this the Subscription class to define a GraphQL
    subscription. The class works with the `GraphqlWsConsumer` which
    maintains a WebSocket connection with the client.

    The subclass specifies the following methods. You can define each of
    them as a `@classmethod`, as a `@staticmethod`, or even as a regular
    method (like Graphene typically does). It shall work fine either
    way. NOTE, if you define the method as a regular method (not a
    classmethod or a staticmethod) you will receive the first argument
    (`payload`/`root`) into the `self` argument.

        [async] publish(payload, info, *args, **kwds):
            This method invoked each time subscription "triggers".
            Raising an exception here will lead to sending the
            notification with the error. To suppress the notification
            return `Subscription.SKIP`.

            Can be implemented both as a synchronous or as a coroutine
            function. A synchronous method runs in a worker thread - you
            can call database operations freely. But an asynchronous
            method runs in the main thread with eventloop running. If
            you need to do an call that accesses a database (or execute
            other long running operations) from asynchronous function
            wrap those calls with `cls.db_sync_to_async` to offload the
            execution into separate worker thread.

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

        [async] subscribe(root, info, *args, **kwds):
            Called when client subscribes. Define this to do some extra
            work when client subscribes and to group subscriptions into
            different subscription groups. Method signature is the same
            as in other GraphQL "resolver" methods but it may return
            the subscription groups names to put the subscription into.

            Can be implemented both as a synchronous or as a coroutine
            function. A synchronous method runs in a worker thread - you
            can call database operations freely. But an asynchronous
            method runs in the main thread with eventloop running. If
            you need to do an call that accesses a database (or execute
            other long running operations) from asynchronous function
            wrap those calls with `cls.db_sync_to_async` to offload the
            execution into separate worker thread.

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
                will trigger on publishes to any of that groups. If
                method returns None (default behavior) then the
                subscription is only put to the default group (the one
                which corresponds to the `Subscription` subclass).

        [async] unsubscribed(root, info, *args, **kwds):
            Called when client unsubscribes. Define this to be notified
            when client unsubscribes.

            Can be implemented both as a synchronous or as a coroutine
            function. A synchronous method runs in a worker thread - you
            can call database operations freely. But an asynchronous
            method runs in the main thread with eventloop running. If
            you need to do an call that accesses a database (or execute
            other long running operations) from asynchronous function
            wrap those calls with `cls.db_sync_to_async` to offload the
            execution into separate worker thread.

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
            group.
        unsubscribe: Call this method to stop all subscriptions in the
            group.
    NOTE: If you call any of these methods from the asynchronous context
    then `await` the result of the call.

    """

    # ----------------------------------------------------------------------- PUBLIC API

    # Return this from the `publish` to suppress the notification.
    SKIP = GraphqlWsConsumer.SKIP

    # Subscription notifications queue limit. Set this to control the
    # amount of notifications server keeps in queue when notifications
    # come faster than server processing them. Set this limit to 1 drops
    # all notifications in queue except the latest one. Use this only if
    # you are sure that subscription always returns all required state
    # to the client and client does not loose information when
    # intermediate notification is missed.
    notification_queue_limit = None

    # A function to execute synchronous code. If set to None, will take
    # same function that is specified in the GraphqlWsConsumer class.
    db_sync_to_async = None

    # A prefix of the Channel group names used for the subscription
    # notifications. If set to None, will take value from
    # GraphqlWsConsumer class.
    group_name_prefix = None

    @classmethod
    def broadcast(cls, *, group=None, payload=None):
        """Call this method to notify all subscriptions in the group.

        This method can be called from both synchronous and asynchronous
        contexts. If you call it from the asynchronous context then you
        have to `await`. And when launched from synchronous context
        there is no active event_loop available.

        NOTE: If you need to do an call that accesses a database (or
        execute other long running operations) from asynchronous
        function wrap those calls with `cls.db_sync_to_async` to offload
        the execution into separate worker thread.

        Args:
            group: Name of the subscription group which members must be
                notified. `None` means that all the subscriptions of
                type will be triggered.
            payload: The payload delivered to the `publish` handler.
                NOTE: The `payload` is serialized before sending
                to the subscription group.

        """
        try:
            event_loop = asyncio.get_event_loop()
        except RuntimeError:
            pass
        else:
            if event_loop.is_running():
                return event_loop.create_task(
                    cls.broadcast_async(group=group, payload=payload)
                )

        return cls.broadcast_sync(group=group, payload=payload)

    @classmethod
    async def broadcast_async(cls, *, group=None, payload=None):
        """Broadcast, asynchronous version."""
        assert cls.db_sync_to_async, "No db_sync_to_async function in Subscription."
        # Manually serialize the `payload` to allow transfer of Django
        # models inside the `payload`.
        serialized_payload = await cls.db_sync_to_async(  # pylint: disable=not-callable
            Serializer.serialize
        )(payload)

        # Send the message to the Channels group.
        group = cls._group_name(group)
        group_send = cls._channel_layer().group_send
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
        """Broadcast, synchronous version."""
        # Manually serialize the `payload` to allow transfer of Django
        # models inside the `payload`.
        serialized_payload = Serializer.serialize(payload)

        group = cls._group_name(group)
        sync_channel_layer_group_send = async_to_sync(cls._channel_layer().group_send)
        sync_channel_layer_group_send(
            group,
            message={
                "type": "broadcast",
                "group": group,
                "payload": serialized_payload,
            },
        )

    @classmethod
    def unsubscribe(cls, *, group=None):
        """Call this method to stop all subscriptions in the group.

        This method can be called from both synchronous and asynchronous
        contexts. If you call it from the asynchronous context then you
        have to `await`.

        NOTE: If you need to do an call that accesses a database (or
        execute other long running operations) from asynchronous
        function wrap those calls with `cls.db_sync_to_async` to offload
        the execution into separate worker thread.

        Args:
            group: Name of the subscription group which members must be
                unsubscribed. `None` means that all the client of the
                subscription will be unsubscribed.
        """
        try:
            event_loop = asyncio.get_event_loop()
        except RuntimeError:
            pass
        else:
            if event_loop.is_running():
                return asyncio.create_task(cls.unsubscribe_async(group=group))

        return cls.unsubscribe_sync(group=group)

    @classmethod
    async def unsubscribe_async(cls, *, group=None):
        """Unsubscribe, asynchronous version."""
        # Send the 'unsubscribe' message to the Channels group.
        group = cls._group_name(group)
        await cls._channel_layer().group_send(
            group=group, message={"type": "unsubscribe", "group": group}
        )

    @classmethod
    def unsubscribe_sync(cls, *, group=None):
        """Unsubscribe, synchronous version."""
        # Send the message to the Channels group.
        group = cls._group_name(group)
        sync_channel_layer_group_send = async_to_sync(cls._channel_layer().group_send)
        sync_channel_layer_group_send(
            group=group,
            message={
                "type": "unsubscribe",
                "group": group,
            },
        )

    @classmethod
    def Field(  # pylint: disable=invalid-name
        cls, name=None, description=None, deprecation_reason=None, required=False
    ):
        """Represent subscription as a field to "deploy" it."""
        return SubscriptionField(
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
    ):  # pylint: disable=arguments-renamed
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
    async def _subscribe_resolver(cls, root, info, *args, **kwds):
        """Subscription request received.

        This is called by the Graphene when a client subscribes. Or when
        revolve required for the value of the received source event (an
        "MapSourceToResponseEvent" algorithm from the GraphQL spec, as
        implemented in graphql-core library).

        The root value is None when a client subscribes. And the root
        value contains the source event data when mapping requested.
        """
        try:
            # We don't do any additional processing here, since previous
            # version of the channels_graphql_ws library were just passing
            # values.
            if root is not None:
                return root

            # Extract function which associates the callback with the
            # groups.
            register_subscription = info.context.register_subscription

            # Attach current subscription to the group corresponding to the
            # concrete class. This allows to trigger all the subscriptions
            # of the current type, by invoking `publish` without setting
            # the `group` argument.
            groups = [cls._group_name()]

            # Invoke the subclass-specified `subscribe` method to get the
            # groups subscription must be attached to.
            if cls._meta.subscribe is not None:
                subclass_groups = cls._meta.subscribe(root, info, *args, **kwds)
                # Properly handle `async def subscribe`.
                if asyncio.iscoroutinefunction(cls._meta.subscribe):
                    subclass_groups = await subclass_groups
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
        except Exception as ex:
            LOG.exception("Subscription resolver got exception")
            raise ex

        # Register callbacks to call `publish` and `unsubscribed`.
        async def publish_callback(payload):
            """Call `publish` with the payload.

            The `cls._meta.publish` function might do database
            operations and could be slow.
            """
            # Properly handle `async def publish`.
            if asyncio.iscoroutinefunction(cls._meta.publish):
                result = await cls._meta.publish(payload, info, *args, **kwds)
            else:
                assert (
                    cls.db_sync_to_async
                ), "No db_sync_to_async function in Subscription."
                async_publish = cls.db_sync_to_async(  # pylint: disable=not-callable
                    cls._meta.publish
                )
                result = await async_publish(payload, info, *args, **kwds)
            return result

        async def unsubscribed_callback():
            """Call `unsubscribed` notification.

            The `cls._meta.unsubscribed` function might do database
            operations and could be slow.
            """
            if cls._meta.unsubscribed is None:
                return None
            # Properly handle `async def unsubscribed`.
            if asyncio.iscoroutinefunction(cls._meta.unsubscribed):
                result = await cls._meta.unsubscribed(None, info, *args, **kwds)
            else:
                assert (
                    cls.db_sync_to_async
                ), "No db_sync_to_async function in Subscription."
                async_unsubscribed = (
                    cls.db_sync_to_async(  # pylint: disable=not-callable
                        cls._meta.unsubscribed
                    )
                )
                result = await async_unsubscribed(None, info, *args, **kwds)
            # There is not particular purpose of returning result of the
            # callback. We do it just for uniformity with `publish` and
            # `subscribe`.
            return result

        return register_subscription(
            groups,
            publish_callback,
            unsubscribed_callback,
            cls.notification_queue_limit,
        )

    @classmethod
    async def _subscribe(cls, root, info, *args, **kwds):
        """Subscription request received.

        This is called by the Graphene when a client subscribes. Or when
        revolve required for the value of the received source event (an
        "MapSourceToResponseEvent" algorithm from the GraphQL spec, as
        implemented in graphql-core library).

        The root value is None when a client subscribes. And the root
        value contains the source event data when mapping requested.
        """

        # We need to copy and store configuration options from
        # GraphqlWsConsumer to be used in other places.
        if not cls.db_sync_to_async:
            cls.db_sync_to_async = info.context.db_sync_to_async
            assert cls.db_sync_to_async, "No db_sync_to_async function in Subscription."
        if not cls.group_name_prefix:
            cls.group_name_prefix = info.context.group_name_prefix
            assert (
                cls.group_name_prefix
            ), "No group_name_prefix value available in Subscription."

        middleware_manager = info.context.middleware_manager_for_subscriptions

        # We want to call `cls._subscribe_resolver` but with middlewares.
        resolve_fn = cls._subscribe_resolver
        if middleware_manager:
            resolve_fn = middleware_manager.get_field_resolver(resolve_fn)

        if not asyncio.iscoroutinefunction(resolve_fn):
            resolve_fn = cls.db_sync_to_async(  # pylint: disable=not-callable
                resolve_fn
            )

        return await resolve_fn(root, info, *args, **kwds)

    @classmethod
    def _group_name(cls, group=None):
        """Group name based on the name of the subscription class."""
        prefix = (
            cls.group_name_prefix
            if cls.group_name_prefix
            else "GRAPHQL_WS_SUBSCRIPTION"
        )
        suffix = f"{cls.__module__}.{cls.__qualname__}"
        if group is not None:
            suffix += "-" + group

        # Wrap the suffix into SHA256 to guarantee that the length of
        # the group name is limited. Otherwise Channels will complain
        # about that the group name is wrong (actually is too long).
        suffix_sha256 = hashlib.sha256()
        suffix_sha256.update(suffix.encode("utf-8"))
        suffix_sha256 = suffix_sha256.hexdigest()

        return f"{prefix}-{suffix_sha256}"

    @staticmethod
    def _from_coroutine() -> bool:
        """Check if called from the coroutine function.

        Determine whether the current function is called from a
        coroutine function (native coroutine, generator-based coroutine,
        or asynchronous generator function).

        NOTE: That it's only recommended to use for debugging, not as
        part of your production code's functionality.
        """
        try:
            frame = inspect.currentframe()
            if frame is None:
                return False
            coroutine_function_flags = (
                inspect.CO_COROUTINE  # pylint: disable=no-member
                | inspect.CO_ASYNC_GENERATOR  # pylint: disable=no-member
                | inspect.CO_ITERABLE_COROUTINE  # pylint: disable=no-member
            )
            if (
                frame is not None
                and frame.f_back is not None
                and frame.f_back.f_back is not None
            ):
                return bool(
                    frame.f_back.f_back.f_code.co_flags & coroutine_function_flags
                )
            return False
        finally:
            del frame

    @classmethod
    def _channel_layer(cls):
        """Channel layer."""
        # We cannot simply check existence of channel layer in the
        # consumer constructor, so we added this property.
        channel_layer = channels.layers.get_channel_layer()
        assert channel_layer is not None, "Channel layer is not configured!"
        return channel_layer


class SubscriptionOptions(graphene.types.objecttype.ObjectTypeOptions):
    """Options stored in the Subscription's `_meta` field."""

    arguments = None
    output = None
    subscribe = None
    publish = None
