# Copyright (C) DATADVANCE, 2010-2020
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

import asgiref.sync
import channels.db
import channels.layers
import graphene
import graphene.types.objecttype
import graphene.types.utils
import graphene.utils.get_unbound_function
import graphene.utils.props

from .graphql_ws_consumer import GraphqlWsConsumer
from .serializer import Serializer

# Module logger.
LOG = logging.getLogger(__name__)


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

        [async] publish(payload, info, *args, **kwds):
            This method invoked each time subscription "triggers".
            Raising an exception here will lead to sending the
            notification with the error. To suppress the notification
            return `Subscription.SKIP`.

            Can be implemented both as a synchronous or as a coroutine
            function. In both cases a method runs in a worker thread
            from the GraphQL-processing threadpool.

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
            function. In both cases a method runs in a worker thread
            from the GraphQL-processing threadpool.

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
            function. In both cases a method runs in a worker thread
            from the GraphQL-processing threadpool.

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

    @classmethod
    def broadcast(cls, *, group=None, payload=None):
        """Call this method to notify all subscriptions in the group.

        This method can be called from both synchronous and asynchronous
        contexts. If you call it from the asynchronous context then you
        have to `await`.

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
                assert cls._from_coroutine(), (
                    "The eventloop is running so this call is going to return"
                    " a coroutine object, but the function is called from"
                    " a synchronous context, so you cannot simply 'await' the result!"
                    " This may indicate a wrong usage. To force some particular"
                    " behavior directly call 'broadcast_sync' or 'broadcast_async'."
                )
                return cls.broadcast_async(group=group, payload=payload)

        return cls.broadcast_sync(group=group, payload=payload)

    @classmethod
    async def broadcast_async(cls, *, group=None, payload=None):
        """Broadcast, asynchronous version."""
        # Offload to the thread cause it do DB operations and may work
        # slowly.
        db_sync_to_async = channels.db.database_sync_to_async

        # Manually serialize the `payload` to allow transfer of Django
        # models inside the `payload`.
        serialized_payload = await db_sync_to_async(Serializer.serialize)(payload)

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

        # Send the message to the Channels group.
        group = cls._group_name(group)
        group_send = asgiref.sync.async_to_sync(cls._channel_layer().group_send)
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

        This method can be called from both synchronous and asynchronous
        contexts. If you call it from the asynchronous context then you
        have to `await`.

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
                assert cls._from_coroutine(), (
                    "The eventloop is running so this call is going to return"
                    " a coroutine object, but the function is called from"
                    " a synchronous context, so you cannot simply 'await' the result!"
                    " This may indicate a wrong usage. To force some particular"
                    " behavior directly call 'unsubscribe_sync' or 'unsubscribe_async'."
                )
                return cls.unsubscribe_async(group=group)

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
        group_send = asgiref.sync.async_to_sync(cls._channel_layer().group_send)
        group_send(group=group, message={"type": "unsubscribe", "group": group})

    @classmethod
    def Field(  # pylint: disable=invalid-name
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
    def _subscribe(cls, root, info, *args, **kwds):
        """Subscription request received.

        This is called by the Graphene when a client subscribes.
        """
        # Extract function which associates the callback with the groups
        # and bring real root back.
        register_subscription = root.register_subscription
        root = root.real_root

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
            """Call `unsubscribed` notification."""
            if cls._meta.unsubscribed is None:
                return None
            result = cls._meta.unsubscribed(None, info, *args, **kwds)
            # Properly handle `async def unsubscribed`.
            if asyncio.iscoroutinefunction(cls._meta.unsubscribed):
                result = asyncio.get_event_loop().run_until_complete(result)
            # There is not particular purpose of returning result of the
            # callback. We do it just for uniformity with `publish` and
            # `subscribe`.
            return result

        return register_subscription(groups, publish_callback, unsubscribed_callback)

    @classmethod
    def _group_name(cls, group=None):
        """Group name based on the name of the subscription class."""
        prefix = GraphqlWsConsumer.group_name_prefix
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
