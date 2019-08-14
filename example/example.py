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

"""Simple example of the DjangoChannelsGraphqlWs."""

import pathlib
from typing import Dict, List

import channels
import django
import graphene

import channels_graphql_ws


# It is OK, Graphene works this way.
# pylint: disable=no-self-use,unsubscriptable-object,invalid-name

# ---------------------------------------------------------------------- GRAPHQL BACKEND

# Store chat history right here.
chats: Dict[str, List[str]] = {}


class Message(
    graphene.ObjectType, default_resolver=graphene.types.resolver.dict_resolver
):
    """Message GraphQL type."""

    chatroom = graphene.String()
    message = graphene.String()
    sender = graphene.String()


class Query(graphene.ObjectType):
    """Root GraphQL query."""

    history = graphene.List(Message, chatroom=graphene.String())

    def resolve_history(self, info, chatroom):
        """Return chat history."""
        del info
        return chats[chatroom] if chatroom in chats else []


class SendChatMessage(graphene.Mutation):
    """Send chat message."""

    class Output(graphene.ObjectType):
        """Mutation result."""

        ok = graphene.Boolean()

    class Arguments:
        """Mutation arguments."""

        chatroom = graphene.String()
        message = graphene.String()
        username = graphene.String()

    def mutate(self, info, chatroom, message, username):
        """Mutation "resolver" - broadcast a message to the chatroom."""
        del info

        # Store a message.
        history = chats.setdefault(chatroom, [])
        history.append({"chatroom": chatroom, "message": message, "sender": username})

        # Notify subscribers.
        OnNewChatMessage.new_chat_message(
            chatroom=chatroom, message=message, sender=username
        )

        return SendChatMessage.Output(ok=True)


class Mutation(graphene.ObjectType):
    """GraphQL mutations."""

    send_chat_message = SendChatMessage.Field()


class OnNewChatMessage(channels_graphql_ws.Subscription):
    """Subscription triggers on a new chat message."""

    sender = graphene.String()
    chatroom = graphene.String()
    message = graphene.String()

    class Arguments:
        """Subscription arguments."""

        username = graphene.String()
        chatroom = graphene.String()

    def subscribe(self, info, username, chatroom=None):
        """Client subscription handler."""
        del info, username
        # Specify the subscription group client subscribes to.
        return [chatroom] if chatroom is not None else None

    def publish(self, info, username, chatroom=None):
        """Called to prepare the subscription notification message."""
        del info

        # The `self` contains payload delivered from the `broadcast()`.
        message = self["message"]
        sender = self["sender"]

        # Avoid self-notifications.
        if sender == username:
            return OnNewChatMessage.SKIP

        return OnNewChatMessage(chatroom=chatroom, message=message, sender=sender)

    @classmethod
    def new_chat_message(cls, chatroom, message, sender):
        """Auxiliary function to send subscription notifications.

        It is generally a good idea to encapsulate broadcast invocation
        inside auxiliary class methods inside the subscription class.
        That allows to consider a structure of the `payload` as an
        implementation details.
        """
        cls.broadcast(group=chatroom, payload={"message": message, "sender": sender})


class Subscription(graphene.ObjectType):
    """GraphQL subscriptions."""

    on_chat_message_sent = OnNewChatMessage.Field()


graphql_schema = graphene.Schema(
    query=Query, mutation=Mutation, subscription=Subscription
)

# ----------------------------------------------------------- GRAPHQL WEBSOCKET CONSUMER


class MyGraphqlWsConsumer(channels_graphql_ws.GraphqlWsConsumer):
    """Channels WebSocket consumer which provides GraphQL API."""

    schema = graphql_schema


# ------------------------------------------------------------------------- ASGI ROUTING
application = channels.routing.ProtocolTypeRouter(
    {
        "websocket": channels.routing.URLRouter(
            [django.urls.path("graphql/", MyGraphqlWsConsumer)]
        )
    }
)

# -------------------------------------------------------------------- URL CONFIGURATION
def graphiql(request):
    """Trivial view to serve the `graphiql.html` file."""
    del request
    graphiql_filepath = pathlib.Path(__file__).absolute().parent / "graphiql.html"
    with open(graphiql_filepath) as f:
        return django.http.response.HttpResponse(f.read())


urlpatterns = [django.urls.path("", graphiql)]
