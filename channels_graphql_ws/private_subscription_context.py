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

"""Datastructure needed to configure `Subscription` instance."""

import asgiref.sync
import graphql


class PrivateSubscriptionContext:
    """Private data to pass to the `Subscription` via `context`.

    The `Subscription` instance needs data (configurations options and
    internal functions) from the `GraphqlWsConsumer` instance.

    That data is saved to the `context` by the `GraphqlWsConsumer` class
    and loaded in the `Subscription` class.

    Contains internal data needed for the `Subscription` implementation.
    """

    __slots__ = (
        "operation_id",
        "sync_to_async",
        "group_name_prefix",
        "register_subscription",
        "middleware_manager_for_subscriptions",
        "subscription_notification_queue_limit",
    )

    def __init__(
        self,
        operation_id,
        sync_to_async: asgiref.sync.SyncToAsync,
        group_name_prefix: str,
        register_subscription,
        subscription_notification_queue_limit: int,
        middleware_manager_for_subscriptions: graphql.MiddlewareManager,
    ):
        """The constructor.

        Args:
            operation_id: Operation id.
            sync_to_async: A function passed as a configuration to the
                GraphqlWsConsumer class.
            group_name_prefix: A string passed as a configuration to the
                GraphqlWsConsumer class.
            register_subscription: A reference to the
                `GraphqlWsConsumer._register_subscription` function.
                This function is called when a client subscribes.
            middleware_manager_for_subscriptions: Middleware manager
                to use in subscription resolvers.
            subscription_notification_queue_limit: maximum number of
                notifications that a subscription queue should store.
        """
        self.operation_id = operation_id
        self.sync_to_async: asgiref.sync.SyncToAsync = sync_to_async
        self.group_name_prefix: str = group_name_prefix
        self.register_subscription = register_subscription
        self.middleware_manager_for_subscriptions: graphql.MiddlewareManager = (
            middleware_manager_for_subscriptions
        )
        self.subscription_notification_queue_limit: int = (
            subscription_notification_queue_limit
        )
