# Django Channels based WebSocket GraphQL server with Graphene-like subscriptions

- [Django Channels based WebSocket GraphQL server with Graphene-like subscriptions](#django-channels-based-websocket-graphql-server-with-graphene-like-subscriptions)
    - [Features](#features)
    - [Installation](#installation)
    - [Getting started](#getting-started)
    - [Details](#details)
        - [Automatic Django model serialization](#automatic-django-model-serialization)
        - [Execution](#execution)
        - [Authentication](#authentication)
        - [Testing](#testing)
        - [Subscription activation confirmation](#subscription-activation-confirmation)
    - [Alternatives](#alternatives)
    - [Development](#development)
    - [Contributing](#contributing)
    - [Acknowledgements](#acknowledgements)

## Features

- WebSocket-based GraphQL server implemented on the Django Channels.
- Graphene-like subscriptions.
- Parallel (asynchronous) requests execution.
- Customizable notification strategies:
    - Single subscription can be put to multiple subscription groups.
    - Notification can be suppressed in the resolver. Useful to avoid
      self-notifications.
- Optional subscription confirmation message. Necessary to avoid race
  conditions in the client logic.

## Installation

```bash
pip install django-channels-graphql-ws
```

## Getting started

Create a GraphQL schema using Graphene. Note the `MySubscription` class.

```python
import channels_graphql_ws
import graphene

class MySubscription(channels_graphql_ws.Subscription):
    """Simple GraphQL subscription."""

    # Subscription payload.
    event = graphene.String()

    class Arguments:
        """That is how subscription arguments are defined."""
        arg1 = graphene.String()
        arg2 = graphene.String()

    @staticmethod
    def subscribe(root, info, arg1, arg2):
        """Called when user subscribes."""

        # Return the list of subscription group names.
        return ['group42']

    @staticmethod
    def publish(payload, info, arg1, arg2):
        """Called to notify the client."""

        # Here `payload` contains the `payload` from the `broadcast()`
        # invocation (see below). You can return `MySubscription.SKIP`
        # if you wish to suppress the notification to a particular
        # client. For example, this allows to avoid notifications for
        # the actions made by this particular client.

        return MySubscription(event='Something has happened!')

class Query(graphene.ObjectType):
    """Root GraphQL query."""
    # Check Graphene docs to see how to define queries.
    pass

class Mutation(graphene.ObjectType):
    """Root GraphQL mutation."""
    # Check Graphene docs to see how to define mutations.
    pass

class Subscription(graphene.ObjectType):
    """Root GraphQL subscription."""
    my_subscription = MySubscription.Field()

graphql_schema = graphene.Schema(
    query=Query,
    mutation=Mutation,
    subscription=Subscription,
)
```

Make your own WebSocket consumer subclass and set the schema it serves:

```python
class MyGraphqlWsConsumer(channels_graphql_ws.GraphqlWsConsumer):
    """Channels WebSocket consumer which provides GraphQL API."""
    schema = graphql_schema

    # Uncomment to send keepalive message every 42 seconds.
    # send_keepalive_every = 42

    # Uncomment to process requests sequentially (useful for tests).
    # strict_ordering = True

    async def on_connect(self, payload):
        """New client connection handler."""
        # You can `raise` from here to reject the connection.
        print("New client connected!")
```

Setup Django Channels routing:

```python
application = channels.routing.ProtocolTypeRouter({
    'websocket': channels.routing.URLRouter([
        django.urls.path('graphql/', MyGraphqlWsConsumer),
    ])
})
```

Notify clients when some event happens:

```python
MySubscription.broadcast(
    # Subscription group to notify clients in.
    group='group42',
    # Dict delivered to the `publish` method.
    payload={},
)
```

## Details

The `channels_graphql_ws` module provides two classes:

- `GraphqlWsConsumer`: Django Channels WebSocket consumer which
    maintains WebSocket connection with the client.
- `Subscription`: Subclass this to define GraphQL subscription. Very
    similar to defining mutations with Graphene. (The class itself is a
    "creative" copy of the Graphene `Mutation` class.)

For details check the [source code](channels_graphql_ws/graphql_ws.py)
which is thoroughly commented. (The docstrings of the `Subscription`
class in especially useful.)

Since the WebSocket handling is based on the Django Channels and
subscriptions are implemented in the Graphene-like style it is
recommended to have a look the documentation of these great projects:

- [Django Channels](http://channels.readthedocs.io)
- [Graphene](http://graphene-python.org/)

The implemented WebSocket-based protocol was taken from the library
[subscription-transport-ws](https://github.com/apollographql/subscriptions-transport-ws)
which is used by the [Apollo GraphQL](https://github.com/apollographql).
Check the [protocol description](https://github.com/apollographql/subscriptions-transport-ws/blob/master/PROTOCOL.md)
for details.

### Automatic Django model serialization

The `Subscription.broadcast` uses Channels groups to deliver a message
to the `Subscription`'s `publish` method. [ASGI specification](https://github.com/django/asgiref/blob/master/specs/asgi.rst#events)
clearly states what can be sent over a channel, and Django models are
not in the list. Since it is common to notify clients about Django
models changes we manually serialize the `payload` using [MessagePack](https://github.com/msgpack/msgpack-python)
and hack the process to automatically serialize Django models following
the the Django's guide [Serializing Django objects](https://docs.djangoproject.com/en/dev/topics/serialization/).

### Execution

- Different requests from different WebSocket client are processed
  asynchronously.
- By default different requests (WebSocket messages) from a single
  client are processed concurrently in different worker threads. So
  there is no guarantee that requests will be processed in the same
  the client sent these requests. Actually, with HTTP we have this
  behavior for decades.
- It is possible to serialize message processing by setting
  `strict_ordering` to `True`. But note, this disables parallel requests
  execution - in other words, the server will not start processing
  another request from the client before it finishes the current one.
  See comments in the class `GraphqlWsConsumer`.

### Authentication

Implementing authentication is straightforward. Follow
[the Channels documentation](https://channels.readthedocs.io/en/latest/topics/authentication.html).

Here is an example. Note the `channels.auth.AuthMiddlewareStack` class.

```python
application = channels.routing.ProtocolTypeRouter({
    'websocket': channels.auth.AuthMiddlewareStack(
        channels.routing.URLRouter([
            django.urls.path('graphql/', MyGraphqlWsConsumer),
        ])
    ),
})
```

This gives you a Django user `info.context.user` in all the resolvers.

### Testing

To test GraphQL WebSocket API read the [appropriate page in the Channels
documentation](https://channels.readthedocs.io/en/latest/topics/testing.html).

In order to simplify testing we also provide a class
`channels_graphql_ws.testing.GraphqlWsCommunicator` (subclass of the
`channels.testing.WebsocketCommunicator`) which has multiple GraphQL-
related methods. Check its docstrings and also see [tests](/tests) for
examples.

### Subscription activation confirmation

The original Apollo's protocol does not allow client to know when a
subscription activates. This inevitable leads to the race conditions on
the client side. Sometimes it is not that crucial, but there are cases
when this leads to serious issues. [Here is the discussion](https://github.com/apollographql/subscriptions-transport-ws/issues/451)
in the [`subscriptions-transport-ws`](https://github.com/apollographql/subscriptions-transport-ws)
tracker.

To solve this problem, there is the `GraphqlWsConsumer` setting
`confirm_subscriptions` which when set to `True` will make the consumer
issue an additional `data` message which confirms the subscription
activation. Please note, you have to modify the client's code to make it
consume this message, otherwise it will be mistakenly considered as the
first subscription notification.

## Alternatives

There is a [Tomáš Ehrlich](https://gist.github.com/tricoder42)
GitHubGist [GraphQL Subscription with django-channels](https://gist.github.com/tricoder42/af3d0337c1b33d82c1b32d12bd0265ec)
which this implementation was initially based on.

There is a promising [GraphQL WS](https://github.com/graphql-python/graphql-ws)
library by the Graphene authors. In particular
[this pull request](https://github.com/graphql-python/graphql-ws/pull/9)
gives a hope that there will be native Graphene implementation of the
WebSocket transport with subscriptions one day.

## Development

Just a reminder of how to setup an environment for the development:

```bash
> python3 -m venv .venv
> direnv allow
> pip install poetry
> poetry install
> pre-commit install
> pytest
```

Use:

[![Code style: black](
    https://img.shields.io/badge/code%20style-black-000000.svg
)](
    https://github.com/ambv/black
)

## Contributing

This project is developed and maintained by DATADVANCE LLC. Please
submit an issue if you have any questions or want to suggest an
improvement.

## Acknowledgements

This work is supported by the Russian Foundation for Basic Research
(project No. 15-29-07043).
