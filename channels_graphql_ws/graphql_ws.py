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
import traceback
import types

import asgiref.sync
import channels.db
import channels.generic.websocket as ch_websocket
import channels.layers
import graphene
import graphene.types.objecttype
import graphene.types.utils
import graphene.utils.get_unbound_function
import graphene.utils.props
import graphene_django.views
import graphql.error
from namedlist import namedlist
import rx


class Subscription(graphene.ObjectType):
    """Subscription type definition.

    Subclass this class to define a GraphQL subscription. The class
    works with `GraphqlWsConsumer` which maintains a WebSocket
    connection with the client.

    The subclass specifies the following methods.
        publish: Called when subscription triggers. Method signature is
            the same as in other GraphQL "resolver" methods, except that
            the `self` argument holds the payload specified in the
            `broadcast` invocation triggered the subscription. Required.
        subscribe: Method called when client subscribes. Method
            signature is the same as in other GraphQL "resolver" methods
            but it must return names of subscription groups to put
            subscription into. Optional.
        unsubscribed: Called when user unsubscribes. Method signature is
            the same as in other GraphQL "resolver" methods. Optional.

    The methods enlisted above receives "standard" set of GraphQL
    resolver arguments (`self, info, ...`). The `info` field has
    `context` which can be used to transmit some useful payload between
    these methods. For example if `subscribe` sets `info.context.zen=42`
    then `publish` will have access to this value `info.context.zen`.

    Static methods of subscription subclass:
        broadcast: Call this method to notify all subscriptions in the
            group.
        unsubscribe: Call this method to stop all subscriptions in the
            group.
    """

    # ------------------------------------------------------------ OVERWRITE IN SUBCLASS

    def publish(self, info, *args, **kwds):
        """GraphQL resolver for subscription notifications.

        Overwrite this to "resolve" subscription query. This method
        invoked each time subscription "triggers".

        Args:
            self: The value provided as `payload` when `publish` called.
            info: The value of `info.context` is a Channels websocket
                context with all the connection information.
            args, kwds: Values of the GraphQL subscription inputs.
        Returns:
            The same the any Graphene resolver returns.
        """
        raise NotImplementedError()

    def subscribe(self, info, *args, **kwds):  # pylint: disable=unused-argument
        """Called when client subscribes.

        Overwrite this to do some extra work when client subscribes and
        to groups subscriptions into different subscription groups.

        Args:
            self: Typically `None`.
            info: The value of `info.context` is a Channels websocket
                context with all the connection information.
            args, kwds: Values of the GraphQL subscription inputs.

        Returns:
            The list or tuple of subscription group names this
            subscription instance belongs to. Later the subscription
            will trigger on publishes to any of that groups. If method
            returns None (default behavior) then the subscription is
            only put to the default group (the one which corresponds to
            the `Subscription` subclass).
        """
        return

    def unsubscribed(self, info, *args, **kwds):
        """Called when client unsubscribes.

        Overwrite to be notified when client unsubscribes.

        Args:
            self: None
            info: The value of `info.context` is a Channels websocket
                context with all the connection information.
            args, kwds: Values of the GraphQL subscription inputs.
        """
        pass

    # --------------------------------------------------- SUBSCRIPTION CONTROL INTERFACE

    @classmethod
    def broadcast(cls, *, group=None, payload=None):
        """Call this method to notify all subscriptions in the group.

        Args:
            group: Name of the subscription group which members must be
                notified. `None` means that all the subscriptions of
                type will be triggered.
            payload: The payload delivered to the `publish` handler as
                the `self` argument.
        """
        # Send the message to the Channels group.
        group_send = asgiref.sync.async_to_sync(
            channels.layers.get_channel_layer().group_send
        )
        group = cls._group_name(group)
        group_send(
            group=group,
            message={"type": "broadcast", "group": group, "payload": payload},
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
    ):  # pylint: arguments-differ
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
        subscribe = subscribe or getattr(cls, "subscribe")
        publish = publish or getattr(cls, "publish")
        assert publish is not Subscription.publish, (
            f"Subscription `{cls.__qualname__}` does not define a "
            "method `publish`! All subscriptions must define "
            "`publish` which processes a GraphQL query!"
        )

        unsubscribed = unsubscribed or getattr(cls, "unsubscribed")

        if _meta.fields:
            _meta.fields.update(fields)
        else:
            _meta.fields = fields

        # Auxiliary alias.
        get_function = graphene.utils.get_unbound_function.get_unbound_function

        _meta.arguments = arguments
        _meta.output = output
        _meta.resolver = get_function(cls._subscribe)
        _meta.subscribe = get_function(subscribe)
        _meta.publish = get_function(publish)
        _meta.unsubscribed = get_function(unsubscribed)

        super().__init_subclass_with_meta__(_meta=_meta, **options)

    @classmethod
    def _subscribe(cls, obj, info, *args, **kwds):  # pylint: disable=unused-argument
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
        subclass_groups = cls._meta.subscribe(obj, info, *args, **kwds)
        subclass_groups = subclass_groups or []
        assert isinstance(
            subclass_groups, (list, tuple)
        ), "Subscribe must return a list or a tuple of group names!"

        groups += [cls._group_name(group) for group in subclass_groups]

        # Register callbacks to call `publish` and `unsubscribed`.
        # Function `register` provides an observable which must
        # be returned from here, cause that is what GraphQL expects from
        # the subscription "resolver" functions.
        def publish_callback(payload):
            """Call `publish` with the payload as `self`."""
            return cls._meta.publish(payload, info, *args, **kwds)

        def unsubscribed_callback():
            """Call `unsubscribed` with `None` as `self`."""
            return cls._meta.unsubscribed(None, info, *args, **kwds)

        return register(groups, publish_callback, unsubscribed_callback)

    @classmethod
    def _group_name(cls, group=None):
        """Group name based on the name of the subscription class."""

        name = f"SUBSCRIPTION-{cls.__module__}.{cls.__qualname__}"
        if group is not None:
            name += "." + group

        return name


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
    NOTE: The class made async only to send keepalive messages using
    eventloop. I am not sure it is brilliant idea though.

    This class implements the WebSocket-based GraphQL protocol used by
    `subscriptions-transport-ws` library (used by Apollo):
    https://github.com/apollographql/subscriptions-transport-ws/blob/master/PROTOCOL.md
    """

    # ------------------------------------------------------------ OVERWRITE IN SUBCLASS

    # Overwrite this in the subclass to specify the GraphQL schema which
    # processes GraphQL queries.
    schema = None

    # The interval to send keepalive messages to the clients.
    send_keepalive_every = None

    async def on_connect(self, payload):
        """Called after CONNECTION_INIT message from client.

        Overwrite to raise an Exception to tell the server
        to reject the connection when it's necessary.

        Args:
            payload: payload from CONNECTION_INIT message.
        """
        pass

    # ------------------------------------------------------------------- IMPLEMENTATION

    # Subscription WebSocket subprotocol.
    GRAPHQL_WS_SUBPROTOCOL = "graphql-ws"

    # Structure that holds subscription information.
    _SubInf = namedlist("_SubInf", ["groups", "op_id", "trigger", "unsubscribed"])

    def __init__(self, *args, **kwargs):
        assert self.schema is not None, (
            "An attribute `schema` is not set! Subclasses must specify "
            "the schema which processes GraphQL subscription queries."
        )

        # Registry of active (subscribed) subscriptions.
        self._subscriptions = {}  # {'<sid>': '<SubInf>', ...}
        self._sids_by_group = {}  # {'<grp>': ['<sid0>', '<sid1>', ...], ...}

        # Task that sends keepalive messages periodically.
        self._keepalive_task = None

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
        assert self.GRAPHQL_WS_SUBPROTOCOL in (
            (sp.decode() if isinstance(sp, bytes) else sp)
            for sp in self.scope["subprotocols"]
        ), (
            f"WebSocket client does not request for the subprotocol "
            f"{self.GRAPHQL_WS_SUBPROTOCOL}!"
        )

        # Accept connection with the GraphQL-specific subprotocol.
        await self.accept(subprotocol=self.GRAPHQL_WS_SUBPROTOCOL)

    async def disconnect(
        self, close_code
    ):  # pylint: disable=unused-argument,arguments-differ
        """WebSocket disconnection handler."""

        # Remove itself from Channels groups and clear triggers.
        await self._cleanup()

    async def receive_json(self, content):  # pylint: disable=arguments-differ
        """Process WebSocket message received from the client."""

        # Extract message type based on which we select how to proceed.
        msg_type = content["type"].upper()

        if msg_type == "CONNECTION_INIT":
            await self._on_gql_connection_init(payload=content["payload"])

        elif msg_type == "CONNECTION_TERMINATE":
            await self._on_gql_connection_terminate()

        elif msg_type == "START":
            await self._on_gql_start(
                operation_id=content["id"], payload=content["payload"]
            )

        elif msg_type == "STOP":
            await self._on_gql_stop(operation_id=content["id"])

        else:
            await self._send_gql_error(
                content["id"], f'Message of unknown type "{msg_type}" received!'
            )

    async def broadcast(self, message):
        """The broadcast message handler.

        Method is called when new `broadcast` message received from the
        Channels group. The message is typically sent by the method
        `Subscription.broadcast`. Here we figure out the group message
        received from and trigger the observable which makes the
        subscription process the query and notify the client.
        """
        group = message["group"]
        payload = message["payload"]

        # Offload trigger to the thread (in threadpool) cause it may
        # work slowly and do DB operations.
        db_sync_to_async = channels.db.database_sync_to_async
        triggers = (
            db_sync_to_async(self._subscriptions[op_id].trigger)
            for op_id in self._sids_by_group[group]
        )
        await asyncio.wait([trigger(payload) for trigger in triggers])

    async def unsubscribe(self, message):
        """The unsubscribe message handler.

        Method is called when new `_unsubscribe` message received from
        the Channels group. The message is typically sent by the method
        `Subscription.unsubscribe`. Here we figure out the group message
        received from and stop all the active subscriptions in this
        group.
        """
        group = message["group"]

        # Unsubscribe all active subscriptions current client has in
        # the subscription group `group`.
        await asyncio.wait(
            [self._on_gql_stop(sid) for sid in self._sids_by_group[group]]
        )

    # ---------------------------------------------------------- GRAPHQL PROTOCOL EVENTS

    async def _on_gql_connection_init(self, payload):  # pylint: disable=unused-argument
        """Process the CONNECTION_INIT message.

        Start sending keepalive messages if `send_keepalive_every` set.
        Respond with either CONNECTION_ACK or CONNECTION_ERROR message.
        """

        try:
            # Notify subclass a new client is connected.
            await self.on_connect(payload)
        except Exception as exc:
            # Send CONNECTION_ERROR message.
            error_to_dict = graphene_django.views.GraphQLView.format_error
            await self._send_gql_connection_error(error_to_dict(exc))
            # Close the connection.
            await self._cleanup()
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
        """Process the CONNECTION_TERMINATE message."""

        # Remove itself from Channels groups and clear triggers.
        await self._cleanup()

        # Close the connection.
        await self.close(code=1000)

    async def _on_gql_start(self, operation_id, payload):
        """Process the START message.

        This message holds query, mutation or subscription request.
        """

        try:
            if operation_id in self._subscriptions:
                raise graphql.error.GraphQLError(
                    f"Subscription with the given `id={operation_id}` "
                    "already exists! Would you like to unsubscribe first?"
                )

            # Get the message data.
            op_id = operation_id
            query = payload["query"]
            operation_name = payload.get("operationName")
            variables = payload.get("variables", {})

            # Local alias for convenience.
            async_to_sync = asgiref.sync.async_to_sync

            # The subject we will trigger on the `publish` message.
            publishes = rx.subjects.Subject()

            # This function is called by subscription to associate a
            # callback (which publishes notifications) with the groups.
            def register(groups, publish_callback, unsubscribed_callback):
                """Associate publish callback with the groups."""
                # Put subscription information into the registry and
                # start listening to the subscription groups.
                trigger = publishes.on_next
                subinf = self._SubInf(
                    groups=groups,
                    op_id=op_id,
                    trigger=trigger,
                    unsubscribed=unsubscribed_callback,
                )
                self._subscriptions[op_id] = subinf
                for group in groups:
                    self._sids_by_group.setdefault(group, []).append(op_id)
                    async_to_sync(self.channel_layer.group_add)(
                        group, self.channel_name
                    )
                return publishes.map(publish_callback)

            # Create object-like context (like in `Query` or `Mutation`)
            # from the dict-like one provided by the Channels. The
            # subscriptions groups must be set by the subscription using
            # the `attach_to_groups` function.
            context = types.SimpleNamespace(**self.scope)
            context.register = register

            # Process GraphQL request with Graphene. Offload it to the
            # thread cause it may work slowly and do DB operations.
            db_sync_to_async = channels.db.database_sync_to_async
            result = await db_sync_to_async(self.schema.execute)(
                query,
                operation_name=operation_name,
                variable_values=variables,
                context_value=context,
                allow_subscriptions=True,
            )
        except Exception as exc:
            # something is wrong - send ERROR message
            await self._send_gql_error(operation_id, traceback.format_exc())
        else:
            # Receiving an observer means the subscription has been
            # processed. Otherwise it is just regular query or mutation.
            if isinstance(result, rx.Observable):
                # Client subscribed so subscribe to the observable
                # returned from GraphQL and respond with
                # the confirmation message.

                # Subscribe to the observable.
                # NOTE: Function `on_next` is called from a thread
                # where trigger runs, so it is necessary to wrap our
                # async method into `async_to_sync`, which carefully
                # runs given a coroutine in the current eventloop.
                # (See the implementation of `async_to_sync`
                # for details.)
                send_gql_data = async_to_sync(self._send_gql_data)
                result.subscribe(lambda r: send_gql_data(op_id, r.data, r.errors))
            else:
                # Query or mutation received - send a response
                # immediately.

                # We respond with a message of type `data`.
                # If Graphene complains that the request is invalid
                # (e.g. query contains syntax error), then the `data`
                # argument is None and the 'errors' argument contains
                # all errors that occurred before or during execution.
                # `result` is instance of ExecutionResult
                # Respond with data.
                await self._send_gql_data(op_id, result.data, result.errors)
                # Tell the client that the request processing is over.
                await self._send_gql_complete(op_id)

    async def _on_gql_stop(self, operation_id):
        """Process the STOP message."""
        # Currently only subscriptions can be stopped.
        # But we see but some clients (e.g. GraphiQL) send
        # the stop message even for queries and mutations.
        # We also see that the Apollo server ignores such messages,
        # so we ignore them as well.
        if not operation_id in self._subscriptions:
            return

        # Unsubscribe: stop listening corresponding groups and
        # subscription from the registry.
        waitlist = []
        subinf = self._subscriptions.pop(operation_id)
        for group in subinf.groups:
            waitlist.append(self.channel_layer.group_discard(group, self.channel_name))
            # Remove operation if from groups it belongs to. And remove
            # group from `_sids_by_group` if there is not subscriptions
            # in it.
            assert self._sids_by_group[group].count(operation_id) == 1, (
                f"Registry is inconsistent: group `{group}` has "
                f"`{self._sids_by_group[group].count(operation_id)}` "
                "occurrences of operation_id=`{operation_id}`!"
            )

            self._sids_by_group[group].remove(operation_id)
            if not self._sids_by_group[group]:
                del self._sids_by_group[group]
        await asyncio.wait(waitlist)

        # Call subscription class `unsubscribed` handler.
        subinf.unsubscribed()

        # Send the unsubscription confirmation message.
        await self._send_gql_complete(operation_id)

    # -------------------------------------------------------- GRAPHQL PROTOCOL MESSAGES

    async def _send_gql_connection_ack(self):
        """Sent in reply to the `connection_init` request."""
        await self.send_json({"type": "connection_ack"})

    async def _send_gql_connection_error(self, error):
        """Connection error sent in reply to the `connection_init`."""
        await self.send_json({"type": "connection_error", "payload": error})

    async def _send_gql_data(self, operation_id, data, errors):
        """Send GraphQL `data` message to the client.

        Args:
            data: Dict with GraphQL query responce.
            errors: List with exceptions occurred during processing the
                GraphQL query. (Errors happened in the resolvers.)
        """
        error_to_dict = graphene_django.views.GraphQLView.format_error
        await self.send_json(
            {
                "type": "data",
                "id": operation_id,
                "payload": {
                    "data": data,
                    **(
                        {"errors": [error_to_dict(e) for e in errors]} if errors else {}
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
        await self.send_json(
            {"type": "error", "id": operation_id, "payload": {"errors": [error]}}
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

    async def _cleanup(self):
        """Cleanup before disconnect.

        Remove itself from the Channels groups, clear triggers and stop
        sending keepalive messages.
        """

        # The list of awaitables to simultaneously wait at the end.
        waitlist = []

        # Unsubscribe from the Channels groups.
        waitlist += [
            self.channel_layer.group_discard(group, self.channel_name)
            for group in self._sids_by_group.keys()
        ]

        if self._keepalive_task is not None:
            # Stop sending keepalive messages.
            self._keepalive_task.cancel()
            waitlist += [self._keepalive_task]

        if waitlist:
            await asyncio.wait(waitlist)

        self._subscriptions.clear()
        self._sids_by_group.clear()
