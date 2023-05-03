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

"""Simple example of the DjangoChannelsGraphqlWs."""

import logging
import pathlib
from collections import defaultdict
from typing import Any, DefaultDict, Dict, List

import channels
import channels.auth
import channels.db
import channels.routing
import django
import django.contrib.admin
import django.contrib.auth
import django.core.asgi
import django.urls
import graphene
import graphql
from graphene_django.views import GraphQLView

import channels_graphql_ws

LOG = logging.getLogger(__name__)

# It is OK, Graphene works this way.
# pylint: disable=unsubscriptable-object,invalid-name

# Fake storage for the chat history. Do not do this in production, it
# lives only in memory of the running server and does not persist.
chats: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)


# ---------------------------------------------------------------------- TYPES & QUERIES


class Message(  # type: ignore
    graphene.ObjectType, default_resolver=graphene.types.resolver.dict_resolver
):
    """Message GraphQL type."""

    chatroom = graphene.String()
    text = graphene.String()
    sender = graphene.String()


class Query(graphene.ObjectType):
    """Root GraphQL query."""

    history = graphene.List(Message, chatroom=graphene.String())

    def resolve_history(self, info, chatroom):
        """Return chat history."""
        del info
        return chats[chatroom] if chatroom in chats else []


# ---------------------------------------------------------------------------- MUTATIONS
class SendChatMessage(graphene.Mutation, name="SendChatMessagePayload"):  # type: ignore
    """Send chat message."""

    ok = graphene.Boolean()

    class Arguments:
        """Mutation arguments."""

        chatroom = graphene.String()
        text = graphene.String()

    def mutate(self, info, chatroom, text):
        """Mutation "resolver" - store and broadcast a message."""

        # Here we identify the sender by `sessionid` cookie.
        user_session_key = info.context.session.session_key

        # Store a message.
        chats[chatroom].append(
            {"chatroom": chatroom, "text": text, "sender": user_session_key}
        )

        # Notify subscribers.
        OnNewChatMessage.new_chat_message(
            chatroom=chatroom, text=text, sender=user_session_key
        )

        return SendChatMessage(ok=True)


class Mutation(graphene.ObjectType):
    """Root GraphQL mutation."""

    send_chat_message = SendChatMessage.Field()


# ------------------------------------------------------------------------ SUBSCRIPTIONS


class OnNewChatMessage(channels_graphql_ws.Subscription):
    """Subscription triggers on a new chat message."""

    sender = graphene.String()
    chatroom = graphene.String()
    text = graphene.String()

    class Arguments:
        """Subscription arguments."""

        chatroom = graphene.String()

    def subscribe(self, info, chatroom=None):
        """Client subscription handler."""
        del info
        # Specify the subscription group client subscribes to.
        return [chatroom] if chatroom is not None else None

    def publish(self, info, chatroom=None):
        """Called to prepare the subscription notification message."""

        # The `self` contains payload delivered from the `broadcast()`.
        new_msg_chatroom = self["chatroom"]
        new_msg_text = self["text"]
        new_msg_sender = self["sender"]

        # Method is called only for events on which client explicitly
        # subscribed, by returning proper subscription groups from the
        # `subscribe` method. So he either subscribed for all events or
        # to particular chatroom.
        assert chatroom is None or chatroom == new_msg_chatroom

        # Avoid self-notifications.
        # The sender is identified by a `sessionid` cookie.
        user_session_key = info.context.channels_scope["session"].session_key
        if user_session_key == new_msg_sender:
            return None

        return OnNewChatMessage(
            chatroom=chatroom, text=new_msg_text, sender=new_msg_sender
        )

    @classmethod
    def new_chat_message(cls, chatroom, text, sender):
        """Auxiliary function to send subscription notifications.

        It is generally a good idea to encapsulate broadcast invocation
        inside auxiliary class methods inside the subscription class.
        That allows to consider a structure of the `payload` as an
        implementation details.
        """
        cls.broadcast(
            group=chatroom,
            payload={"chatroom": chatroom, "text": text, "sender": sender},
        )


class Subscription(graphene.ObjectType):
    """GraphQL subscriptions."""

    on_new_chat_message = OnNewChatMessage.Field()


# ----------------------------------------------------------- GRAPHQL WEBSOCKET CONSUMER


async def demo_middleware(next_middleware, root, info, *args, **kwds):
    """Demo GraphQL middleware.

    For more information read:
    https://docs.graphene-python.org/en/latest/execution/middleware/#middleware
    """
    print("Subscription name:", info.operation.name.value)

    # Invoke next middleware.
    result = next_middleware(root, info, *args, **kwds)
    if graphql.pyutils.is_awaitable(result):
        result = await result
    return result


class MyGraphqlWsConsumer(channels_graphql_ws.GraphqlWsConsumer):
    """Channels WebSocket consumer which provides GraphQL API."""

    schema = graphene.Schema(query=Query, mutation=Mutation, subscription=Subscription)
    middleware = [demo_middleware]


# ------------------------------------------------------------------------- ASGI ROUTING

# NOTE: Please note `channels.auth.AuthMiddlewareStack` wrapper, for
# more details about Channels authentication read:
# https://channels.readthedocs.io/en/latest/topics/authentication.html
application = channels.routing.ProtocolTypeRouter(
    {
        "http": django.core.asgi.get_asgi_application(),
        "websocket": channels.auth.AuthMiddlewareStack(
            channels.routing.URLRouter(
                [django.urls.path("graphql-ws/", MyGraphqlWsConsumer.as_asgi())]
            )
        ),
    }
)


# -------------------------------------------------------------------- URL CONFIGURATION
def graphiql(request):
    """Trivial view to serve the `graphiql.html` file."""
    # It is important to create a session if it hasn't already been created,
    # because `sessionid` cookie is used to identify the sender.
    if not request.session.session_key:
        request.session.create()
    del request
    graphiql_filepath = pathlib.Path(__file__).absolute().parent / "graphiql.html"
    # It is better to specify an encoding when opening documents. Using the
    # system default implicitly can create problems on other operating systems.
    with open(graphiql_filepath, encoding="utf-8") as f:
        return django.http.response.HttpResponse(f.read())


urlpatterns = [
    django.urls.path("", graphiql),
    # `GraphQLView` used to handle mutations and queries requests sended by http.
    django.urls.path(
        "graphql/",
        GraphQLView.as_view(
            schema=graphene.Schema(
                query=Query, mutation=Mutation, subscription=Subscription
            )
        ),
    ),
    django.urls.path("admin", django.contrib.admin.site.urls),
]
