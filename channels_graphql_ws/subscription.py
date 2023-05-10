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

"""Graphene-like subscription class.

The `Subscription` class itself is a "creative" copy of `Mutation` class
from the Graphene (`graphene/types/mutation.py`).
"""

import asyncio
import collections
import hashlib
import logging
from typing import Optional

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
            notification with the error. Technically the WebSocket
            message will contain extra field "extensions.code" holding
            the classname of the exception raised. To suppress the
            notification return `None`.

            Can be implemented as both asynchronous (`async def`) or
            synchronous (`def`) function. Asynchronous implementation
            runs blazingly fast in the main event loop of the main
            thread. You must be careful with blocking calls though. You
            can offload blocking operations to a thread in such cases.
            Synchronous implementation always runs in a worker thread
            which comes with a price of extra overhead.

            Required.

            Args:
                payload: The `payload` from the `broadcast` invocation.
                info: The value of `info.context` is a Channels
                    websocket context with all the connection
                    information.
                args, kwds: Values of the GraphQL subscription inputs.
            Returns:
                The same that any Graphene resolver returns.

        [async] subscribe(root, info, *args, **kwds):
            Called when client subscribes. Define this to do some extra
            work when client subscribes and to group subscriptions into
            different subscription groups. Method signature is the same
            as in other GraphQL "resolver" methods but it may return the
            subscription groups names to put the subscription into.

            Can be implemented as both asynchronous (`async def`) or
            synchronous (`def`) function. Asynchronous implementation
            runs blazingly fast in the main event loop of the main
            thread. You must be careful with blocking calls though. You
            can offload blocking operations to a thread in such cases.
            Synchronous implementation always runs in a worker thread
            which comes with a price of extra overhead.

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

            Can be implemented as both asynchronous (`async def`) or
            synchronous (`def`) function. Asynchronous implementation
            runs blazingly fast in the main event loop of the main
            thread. You must be careful with blocking calls though. You
            can offload blocking operations to a thread in such cases.
            Synchronous implementation always runs in a worker thread
            which comes with a price of extra overhead.

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
        broadcast(): Call this to notify all subscriptions in the group.
        unsubscribe(): Call this to stop all subscriptions in the group.

    NOTE: If you call any of these methods from the asynchronous context
    then `await` the result of the call.
    """

    # ----------------------------------------------------------------------- PUBLIC API

    # Subscription notifications queue limit. Set this to control the
    # amount of notifications server keeps in the queue when
    # notifications come faster than server processes them. Setting this
    # to 1 drops all notifications in the queue except the latest one.
    # Useful to skip intermediate notifications, e.g. progress reports.
    notification_queue_limit: Optional[int] = None

    @classmethod
    def broadcast(cls, *, group=None, payload=None):
        """Call this method to notify all subscriptions in the group.

        Can be called from both synchronous and asynchronous contexts.

        It is necessary to `await` if called from the async context.

        Args:
            group: Name of the subscription group which members must be
                notified. `None` means that all the subscriptions of
                type will be triggered.
            payload: The payload delivered to the `publish` handler.
                NOTE: The `payload` is serialized before sending to the
                subscription group.

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
        # Manually serialize the `payload` to allow transfer of Django
        # models inside `payload`, auto serialization does not do this.
        serialized_payload = await channels.db.database_sync_to_async(
            Serializer.serialize, thread_sensitive=False
        )(payload)

        # Send the message to the Channels group.
        group = cls._group_name(group)
        group_send = cls._channel_layer().group_send
        # Will result in a call of `GraphqlWsConsumer.broadcast`.
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
        sync_channel_layer_group_send = asgiref.sync.async_to_sync(
            cls._channel_layer().group_send
        )
        # Will result in a call of `GraphqlWsConsumer.broadcast`.
        sync_channel_layer_group_send(
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
        sync_channel_layer_group_send = asgiref.sync.async_to_sync(
            cls._channel_layer().group_send
        )
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
        """Represent subscription as a field to mount it to the schema.

        Typical usage:
            class Subscription(graphene.ObjectType):
                on_new_chat_message = OnNewChatMessage.Field()

        """
        return graphene.Field(
            cls._meta.output,
            args=cls._meta.arguments,
            resolver=cls._meta.publish,
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
        """Prepare subscription on subclass creation.

        This method is invoked by the superclass `__init__subclass__`.
        It is needed to process class fields, `Meta` and inheritance
        parameters. This is genuine Graphene approach inherited/cloned
        from the original Mutation class implementation.
        """
        if not _meta:
            _meta = SubscriptionOptions(cls)

        output = output or getattr(cls, "Output", None)
        # Collect fields if output class is not explicitly defined.
        fields: dict = {}
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
        unsubscribed = unsubscribed or getattr(cls, "unsubscribed", None)
        assert publish is not None, (
            f"Subscription '{cls.__qualname__}' does not define a"
            " method 'publish'! All subscriptions must define"
            " 'publish' which processes GraphQL queries!"
        )

        if _meta.fields:
            _meta.fields.update(fields)
        else:
            _meta.fields = fields

        # Auxiliary alias.
        graphene_get_function = graphene.utils.get_unbound_function.get_unbound_function

        # pylint: disable=attribute-defined-outside-init
        _meta.arguments = arguments
        _meta.output = output
        _meta.publish = graphene_get_function(publish)
        _meta.subscribe = graphene_get_function(subscribe)
        _meta.unsubscribed = graphene_get_function(unsubscribed)

        super().__init_subclass_with_meta__(_meta=_meta, **options)

    @classmethod
    def _group_name(cls, group=None):
        """Group name based on the name of the subscription class."""
        suffix = f"{cls.__module__}.{cls.__qualname__}"
        if group is not None:
            suffix += "-" + group

        # Wrap the suffix into SHA256 to guarantee that the length of
        # the group name is limited. Otherwise Channels will complain
        # about that the group name is wrong (actually is too long).
        suffix_sha256 = hashlib.sha256()
        suffix_sha256.update(suffix.encode("utf-8"))

        return f"{GraphqlWsConsumer.group_name_prefix}-{suffix_sha256.hexdigest()}"

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
    unsubscribed = None
