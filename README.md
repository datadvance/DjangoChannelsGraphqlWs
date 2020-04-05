<!--
Copyright (C) DATADVANCE, 2010-2020

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be included
in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
-->

# Django Channels based WebSocket GraphQL server with Graphene-like subscriptions

[![PyPI](https://img.shields.io/pypi/v/django-channels-graphql-ws.svg)](https://pypi.org/project/django-channels-graphql-ws/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/django-channels-graphql-ws.svg)](https://pypi.org/project/django-channels-graphql-ws/)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/django-channels-graphql-ws)](https://pypi.org/project/django-channels-graphql-ws/)
[![GitHub Release Date](https://img.shields.io/github/release-date/datadvance/DjangoChannelsGraphqlWs)](https://github.com/datadvance/DjangoChannelsGraphqlWs/releases)
[![Travis CI Build Status](https://travis-ci.org/datadvance/DjangoChannelsGraphqlWs.svg?branch=master)](https://travis-ci.org/datadvance/DjangoChannelsGraphqlWs)
[![GitHub Actions Tests](https://github.com/datadvance/DjangoChannelsGraphqlWs/workflows/Tests/badge.svg)](https://github.com/datadvance/DjangoChannelsGraphqlWs/actions?query=workflow%3ATests)
[![Code style](https://img.shields.io/badge/code%20style-black-black.svg)](https://github.com/ambv/black)
[![PyPI - License](https://img.shields.io/pypi/l/django-channels-graphql-ws.svg)](https://github.com/datadvance/DjangoChannelsGraphqlWs/blob/master/LICENSE)

- [Django Channels based WebSocket GraphQL server with Graphene-like subscriptions](#django-channels-based-websocket-graphql-server-with-graphene-like-subscriptions)
  - [Features](#features)
  - [Installation](#installation)
  - [Getting started](#getting-started)
  - [Example](#example)
  - [Details](#details)
    - [Automatic Django model serialization](#automatic-django-model-serialization)
    - [Execution](#execution)
    - [Context](#context)
    - [Authentication](#authentication)
    - [The Python client](#the-python-client)
    - [The GraphiQL client](#the-graphiql-client)
    - [Testing](#testing)
    - [Subscription activation confirmation](#subscription-activation-confirmation)
    - [GraphQL middleware](#graphql-middleware)
  - [Alternatives](#alternatives)
  - [Development](#development)
    - [Bootstrap](#bootstrap)
    - [Making release](#making-release)
  - [Contributing](#contributing)
  - [Acknowledgements](#acknowledgements)

## Features

- WebSocket-based GraphQL server implemented on the
  [Django Channels v2](https://github.com/django/channels).
- WebSocket protocol is compatible with
  [Apollo GraphQL](https://github.com/apollographql) client.
- [Graphene](https://github.com/graphql-python/graphene)-like
  subscriptions.
- All GraphQL requests are processed concurrently (in parallel).
- Subscription notifications delivered in the order they were issued.
- Optional subscription activation message can be sent to a client. This
  is useful to avoid race conditions on the client side. Consider the
  case when client subscribes to some subscription and immediately
  invokes a mutations which triggers this subscription. In such case the
  subscription notification can be lost, cause these subscription and
  mutation requests are processed concurrently. To avoid this client
  shall wait for the subscription activation message before sending such
  mutation request.
- Customizable notification strategies:
    - A subscription can be put to one or many subscription groups. This
      allows to granularly notify only selected clients, or, looking
      from the client's perspective - to subscribe to some selected
      source of events. For example, imaginary subscription
      "OnNewMessage" may accept argument "user" so subscription will
      only trigger on new messages from the selected user.
    - Notification can be suppressed in the subscription resolver method
      `publish`. For example, this is useful to avoid sending
      self-notifications.
- All GraphQL "resolvers" run in a threadpool so they never block the
  server itself and may communicate with database or perform other
  blocking tasks.
- Resolvers (including subscription's `subscribe` & `publish`) can be
  represented both as synchronous or asynchronous (`async def`) methods.
- Subscription notifications can be sent from both synchronous and
  asynchronous contexts. Just call `MySubscription.broadcast()` or
  `await MySubscription.broadcast()` depending on the context.
- Clients for the GraphQL WebSocket server:
    - AIOHTTP-based client.
    - Client for unit test based on the Django Channels testing
      communicator.
- Supported Python 3.6 and newer (tests run on 3.6, 3.7, and 3.8).
- Works on Linux, macOS, and Windows.

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

Notify<sup>[1](#redis-layer)</sup> clients when some event happens using the `broadcast()`
or `broadcast_sync()` method from the OS thread where
there is no running event loop:

```python
MySubscription.broadcast(
    # Subscription group to notify clients in.
    group='group42',
    # Dict delivered to the `publish` method.
    payload={},
)
```

Notify<sup>[1](#redis-layer)</sup> clients in an coroutine function using the `broadcast()`
or `broadcast_async()` method:

```python
await MySubscription.broadcast(
    # Subscription group to notify clients in.
    group='group42',
    # Dict delivered to the `publish` method.
    payload={},
)
```

<a name="redis-layer">1</a>: in case you are testing your client code
by notifying it from the Django Shell, you have to setup a [channel layer](https://channels.readthedocs.io/en/latest/topics/channel_layers.html#configuration) in order for the two instance of
your application. The same applies in production with workers.

## Example

You can find simple usage example in the [example](example/) directory.

Run:
```bash
cd example/
# Initialize database.
./manage.py migrate
# Create 'user' with password 'user'.
./manage.py createsuperuser
# Run development server.
./manage.py runserver
```

Play with the API though the GraphiQL browser at http://127.0.0.1:8000.

You can start with the following GraphQL requests:
```graphql

# Check there are no messages.
query read { history(chatroom: "kittens") { chatroom text sender }}

# Send a message as Anonymous.
mutation send { sendChatMessage(chatroom: "kittens", text: "Hi all!"){ ok }}

# Check there is a message from `Anonymous`.
query read { history(chatroom: "kittens") { text sender } }

# Login as `user`.
mutation send { login(username: "user", password: "pass") { ok } }

# Send a message as a `user`.
mutation send { sendChatMessage(chatroom: "kittens", text: "It is me!"){ ok }}

# Check there is a message from both `Anonymous` and from `user`.
query read { history(chatroom: "kittens") { text sender } }

# Subscribe, do this from a separate browser tab, it waits for events.
subscription s { onNewChatMessage(chatroom: "kittens") { text sender }}

# Send something again to check subscription triggers.
mutation send { sendChatMessage(chatroom: "kittens", text: "Something ;-)!"){ ok }}
```

## Details

The `channels_graphql_ws` module provides the following key classes:

- `GraphqlWsConsumer`: Django Channels WebSocket consumer which
    maintains WebSocket connection with the client.
- `Subscription`: Subclass this to define GraphQL subscription. Very
    similar to defining mutations with Graphene. (The class itself is a
    "creative" copy of the Graphene `Mutation` class.)
- `GraphqlWsClient`: A client for the GraphQL backend. Executes strings
    with queries and receives subscription notifications.
- `GraphqlWsTransport`: WebSocket transport interface for the client.
- `GraphqlWsTransportAiohttp`: WebSocket transport implemented on the
    [AIOHTTP](https://github.com/aio-libs/aiohttp) library.

For details check the [source code](channels_graphql_ws/) which is
thoroughly commented. The docstrings of classes are especially useful.

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
  client are processed concurrently in different worker threads. (It is
  possible to change the maximum number of worker threads with the
  `max_worker_threads` setting.) So there is no guarantee that requests
  will be processed in the same the client sent these requests.
  Actually, with HTTP we have this behavior for decades.
- It is possible to serialize message processing by setting
  `strict_ordering` to `True`. But note, this disables parallel requests
  execution - in other words, the server will not start processing
  another request from the client before it finishes the current one.
  See comments in the class `GraphqlWsConsumer`.
- All subscription notifications are delivered in the order they were
  issued.

### Context

The context object (`info.context` in resolvers) is an object-like
wrapper around [Channels
scope](https://channels.readthedocs.io/en/latest/topics/consumers.html#scope)
typically available as `self.scope` in the Channels consumers. So you
can access Channels scope as `info.context`. Modifications made in
`info.context` are stored in the Channels scope, so they are persisted
as long as WebSocket connection lives. You can work with `info.context`
both as with `dict` or as with `SimpleNamespace`:

```python
def resolve_something(self, info):
    info.context.fortytwo = 42
    assert info.context["fortytwo"] == 42
```

### Authentication

To enable authentication it is typically enough to wrap your ASGI
application into the  `channels.auth.AuthMiddlewareStack`:

```python
application = channels.routing.ProtocolTypeRouter({
    'websocket': channels.auth.AuthMiddlewareStack(
        channels.routing.URLRouter([
            django.urls.path('graphql/', MyGraphqlWsConsumer),
        ])
    ),
})
```

This gives you a Django user `info.context.user` in all the
resolvers. To authenticate user you can create a `Login` mutation like
the following:

```python
class Login(graphene.Mutation, name="LoginPayload"):
    """Login mutation."""

    ok = graphene.Boolean(required=True)

    class Arguments:
        """Login request arguments."""

        username = graphene.String(required=True)
        password = graphene.String(required=True)

    def mutate(self, info, username, password):
        """Login request."""

        # Ask Django to authenticate user.
        user = django.contrib.auth.authenticate(username=username, password=password)
        if user is None:
            return Login(ok=False)

        # Use Channels to login, in other words to put proper data to
        # the session stored in the scope. The `info.context` is
        # practically just a wrapper around Channel `self.scope`, but
        # the `login` method requires dict, so use `_asdict`.
        asgiref.sync.async_to_sync(channels.auth.login)(info.context._asdict(), user)
        # Save the session,cause `channels.auth.login` does not do this.
        info.context.session.save()

        return Login(ok=True)
```

The authentication is based on the Channels authentication mechanisms.
Check [the Channels
documentation](https://channels.readthedocs.io/en/latest/topics/authentication.html).
Also take a look at the example in the [example](example/) directory.

### The Python client

There is the `GraphqlWsClient` which implements GraphQL client working
over the WebSockets. The client needs a transport instance which
communicates with the server. Transport is an implementation of the
`GraphqlWsTransport` interface (class must be derived from it). There is
the `GraphqlWsTransportAiohttp` which implements the transport on the
[AIOHTTP](https://github.com/aio-libs/aiohttp) library. Here is an
example:

```python
transport = channels_graphql_ws.GraphqlWsTransportAiohttp(
    "ws://backend.endpoint/graphql/", cookies={"sessionid": session_id}
)
client = channels_graphql_ws.GraphqlWsClient(transport)
await client.connect_and_init()
result = await client.execute("query { users { id login email name } }")
users = result["data"]
await client.finalize()
```

See the `GraphqlWsClient` class docstring for the details.

### The GraphiQL client

The GraphiQL provided by Graphene doesn't connect to your GraphQL endpoint
via WebSocket ; instead you should use a modified GraphiQL template under
`graphene/graphiql.html` which will take precedence over the one of Graphene.
One such modified GraphiQL is provided in the [example](example/) directory.

### Testing

To test GraphQL WebSocket API read the [appropriate page in the Channels
documentation](https://channels.readthedocs.io/en/latest/topics/testing.html).

In order to simplify unit testing there is a `GraphqlWsTransport`
implementation based on the Django Channels testing communicator:
`channels_graphql_ws.testing.GraphqlWsTransport`. Check its docstring
and take a look at the [tests](/tests) to see how to use it.

### Subscription activation confirmation

The original Apollo's protocol does not allow client to know when a
subscription activates. This inevitably leads to the race conditions on
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

To customize the confirmation message itself set the `GraphqlWsConsumer`
setting `subscription_confirmation_message`. It must be a dictionary
with two keys `"data"` and `"errors"`. By default it is set to
`{"data": None, "errors": None}`.

### GraphQL middleware

It is possible to inject middleware into the GraphQL operation
processing. For that define `middleware` setting of your

```python
def my_middleware(next_middleware, root, info, *args, **kwds):
    """My custom GraphQL middleware."""
    # Invoke next middleware.
    return next_middleware(root, info, *args, **kwds)

class MyGraphqlWsConsumer(channels_graphql_ws.GraphqlWsConsumer):
    ...
    middleware = [my_middleware]
```

For more information about GraphQL middleware please take a look at the
[relevant section in the Graphene
documentation](https://docs.graphene-python.org/en/latest/execution/middleware/#middleware).

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

### Bootstrap

_A reminder of how to setup an environment for the development._

1. Install Poetry to the system Python.
   ```bash
   $ wget https://raw.githubusercontent.com/python-poetry/poetry/master/get-poetry.py
   $ python3.x get-poetry.py  # ← Replace 'x' with proper Python version.
   $ rm get-poetry.py
   ```
   It is important to install Poetry in the system Python. For details
   see Poetry docs: https://python-poetry.org/docs/#installation
2. Create local virtualenv in `.venv`, install all project dependencies
   (from `pyproject.toml`), and upgrade pip.:
   ```bash
   $ poetry install
   $ pip install --upgrade pip
   ```
3. Tell Direvn you trust local `.envrc` file (if you use Direnv):
   ```bash
   $ direnv allow
   ```
4. Install pre-commit hooks to check code style automatically:
   ```bash
   $ pre-commit install

Use:

[![Code style: black](
    https://img.shields.io/badge/code%20style-black-000000.svg
)](
    https://github.com/ambv/black
)

### Running tests

_A reminder of how to run tests._

- Run all tests on all supported Python versions:
   ```bash
   $ tox
   ```
- Run all tests on a single Python version, e.g on Python 3.7:
   ```bash
   $ tox -e py37
   ```
- Example of running a single test:
   ```bash
   $ tox -e py36 -- tests/test_basic.py::test_main_usecase
   ```
- Running on currently active Python directly with Pytest:
   ```bash
   $ poetry run pytest
   ```

### Making release

_A reminder of how to make and publish a new release._

1. Update version: `poetry version minor`.
2. Update [CHANGELOG.md](./CHANGELOG.md).
3. Update [README.md](./README.md) (if needed).
4. Commit changes made above.
5. Git tag: `git tag vX.X.X && git push --tags`.
6. Publish release to PyPI: `poetry publish --build`.
7. Update
   [release notes](https://github.com/datadvance/DjangoChannelsGraphqlWs/releases)
   on GitHub.

## Contributing

This project is developed and maintained by DATADVANCE LLC. Please
submit an issue if you have any questions or want to suggest an
improvement.

## Acknowledgements

This work is supported by the Russian Foundation for Basic Research
(project No. 15-29-07043).
