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

import pathlib
from collections import defaultdict
from typing import Any, DefaultDict, Dict

import channels
import channels.auth
import channels.db
import channels.routing
import django
import django.contrib.admin
import django.contrib.auth
import django.core.asgi
import graphene
import graphene_django.types
import graphql

import channels_graphql_ws

# It is OK, Graphene works this way.
# pylint: disable=unsubscriptable-object,invalid-name

# Fake storage for the chat history. Do not do this in production, it
# lives only in memory of the running server and does not persist.
chats: DefaultDict[str, list[Dict[str, Any]]] = defaultdict(list)


# ---------------------------------------------------------------------- TYPES & QUERIES


class Message(  # type: ignore
    graphene.ObjectType, default_resolver=graphene.types.resolver.dict_resolver
):
    """Message GraphQL type."""

    chatroom = graphene.String()
    text = graphene.String()
    sender = graphene.String()


class User(graphene_django.types.DjangoObjectType):
    """Show logged in user details via GraphQL.

    Example of working with 'info.context.channels_scope["user"]'.
    """

    class Meta:
        """Wrap Django user model."""

        model = django.contrib.auth.get_user_model()


class Query(graphene.ObjectType):
    """Root GraphQL query."""

    history = graphene.List(Message, chatroom=graphene.String())
    user = graphene.Field(User)

    def resolve_history(self, info, chatroom):
        """Return chat history."""
        del info
        return chats[chatroom] if chatroom in chats else []

    def resolve_user(self, info):
        """Provide currently logged in user."""
        if info.context.channels_scope["user"].is_authenticated:
            return info.context.channels_scope["user"]
        return None


# ---------------------------------------------------------------------------- MUTATIONS


class Login(graphene.Mutation, name="LoginPayload"):  # type: ignore
    """Login mutation.

    Login implementation, following the Channels guide:
    https://channels.readthedocs.io/en/latest/topics/authentication.html
    """

    ok = graphene.Boolean(required=True)

    class Arguments:
        """Login request arguments."""

        username = graphene.String(required=True)
        password = graphene.String(required=True)

    async def mutate(self, info, username, password):
        """Login request."""

        # Ask Django to authenticate user.
        user = await channels.db.database_sync_to_async(
            django.contrib.auth.authenticate
        )(username=username, password=password)
        if user is None:
            return Login(ok=False)

        # Use Channels to login, in other words to put proper data to
        # the session stored in the scope. The
        # `info.context.channels_scope` is a reference to Channel
        # `self.scope` member.
        await channels.auth.login(info.context.channels_scope, user)
        # Save the session, `channels.auth.login` does not do this.
        session = info.context.channels_scope["session"]
        await channels.db.database_sync_to_async(session.save)()

        return Login(ok=True)


class SendChatMessage(graphene.Mutation, name="SendChatMessagePayload"):  # type: ignore
    """Send chat message."""

    ok = graphene.Boolean()

    class Arguments:
        """Mutation arguments."""

        chatroom = graphene.String()
        text = graphene.String()

    async def mutate(self, info, chatroom, text):
        """Mutation "resolver" - store and broadcast a message."""

        # Use the username from the connection scope if authorized.
        user = info.context.channels_scope["user"]
        username = user.username if user.is_authenticated else "Anonymous"

        # Store a message.
        chats[chatroom].append({"chatroom": chatroom, "text": text, "sender": username})

        # Notify subscribers.
        await OnNewChatMessage.new_chat_message(
            chatroom=chatroom, text=text, sender=username
        )

        return SendChatMessage(ok=True)


class Mutation(graphene.ObjectType):
    """Root GraphQL mutation."""

    send_chat_message = SendChatMessage.Field()
    login = Login.Field()


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
        user = info.context.channels_scope["user"]
        if user.is_authenticated and new_msg_sender == user.username:
            return OnNewChatMessage.SKIP

        return OnNewChatMessage(
            chatroom=chatroom, text=new_msg_text, sender=new_msg_sender
        )

    @classmethod
    async def new_chat_message(cls, chatroom, text, sender):
        """Auxiliary function to send subscription notifications.

        It is generally a good idea to encapsulate broadcast invocation
        inside auxiliary class methods inside the subscription class.
        That allows to consider a structure of the `payload` as an
        implementation details.
        """
        await cls.broadcast(
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
    # Skip Graphiql introspection requests, there are a lot.
    if (
        info.operation.name is not None
        and info.operation.name.value != "IntrospectionQuery"
    ):
        print("Demo middleware report")
        print("    operation :", info.operation.operation)
        print("    name      :", info.operation.name.value)

    # Invoke next middleware.
    result = next_middleware(root, info, *args, **kwds)
    if graphql.pyutils.is_awaitable(result):
        result = await result
    return result


class MyGraphqlWsConsumer(channels_graphql_ws.GraphqlWsConsumer):
    """Channels WebSocket consumer which provides GraphQL API."""

    send_keepalive_every = 1

    async def on_connect(self, payload):
        """Handle WebSocket connection event."""

        # Use auxiliary Channels function `get_user` to replace an
        # instance of `channels.auth.UserLazyObject` with a native
        # Django user object (user model instance or `AnonymousUser`)
        # It is not necessary, but it helps to keep resolver code
        # simpler. Cause in both HTTP/WebSocket requests they can use
        # `info.context.channels_scope["user"]`, but not a wrapper. For
        # example objects of type Graphene Django type
        # `DjangoObjectType` does not accept
        # `channels.auth.UserLazyObject` instances.
        # https://github.com/datadvance/DjangoChannelsGraphqlWs/issues/23
        self.scope["user"] = await channels.auth.get_user(self.scope)

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
                [django.urls.path("graphql/", MyGraphqlWsConsumer.as_asgi())]
            )
        ),
    }
)


# -------------------------------------------------------------------- URL CONFIGURATION
def graphiql(request):
    """Trivial view to serve the `graphiql.html` file."""
    del request
    graphiql_filepath = pathlib.Path(__file__).absolute().parent / "graphiql.html"
    # It is better to specify an encoding when opening documents. Using the
    # system default implicitly can create problems on other operating systems.
    with open(graphiql_filepath, encoding="utf-8") as f:
        return django.http.response.HttpResponse(f.read())


urlpatterns = [
    django.urls.path("", graphiql),
    django.urls.path("admin", django.contrib.admin.site.urls),
]
