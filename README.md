<!--
Copyright (C) DATADVANCE, 2010-2023

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
[![Travis CI Build Status](https://travis-ci.com/datadvance/DjangoChannelsJobmanager.svg?branch=master)](https://travis-ci.com/datadvance/DjangoChannelsJobmanager)
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
    - [How to read the code](#how-to-read-the-code)
    - [Running tests](#running-tests)
    - [Making release](#making-release)
  - [Contributing](#contributing)
  - [Acknowledgements](#acknowledgements)

## Features

- WebSocket-based GraphQL server implemented on the
  [Django Channels v3](https://github.com/django/channels).
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
- All GraphQL "resolvers" run either in an eventloop or in a threadpool.
  So asynchronous "resolvers" able to execute blocking calls with
  `sync_to_async`.  And synchronous "resolvers" never block the server
  itself and may communicate with database or perform other blocking
  tasks.
- Resolvers (including subscription's `subscribe` & `publish`) can be
  represented both as synchronous or asynchronous (`async def`) methods.
- Subscription notifications can be sent from both synchronous and
  asynchronous contexts. Just call `MySubscription.broadcast()` or
  `await MySubscription.broadcast()` depending on the context.
- Clients for the GraphQL WebSocket server:
    - AIOHTTP-based client.
    - Client for unit test based on the Django Channels testing
      communicator.
- Supported Python 3.8 and newer (tests run on 3.8, 3.9, 3.10).
- Works on Linux, macOS, and Windows.
- Supports Django 4.1.

## Installation

```shell
pip install django-channels-graphql-ws
```

## Getting started

Create a GraphQL schema using Graphene. Note the `MySubscription` class.

```python
import channels_graphql_ws
import graphene

class MySubscription(channels_graphql_ws.Subscription):
    """Simple GraphQL subscription."""

    # Leave only latest 64 messages in the server queue.
    notification_queue_limit = 64

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
        return ["group42"]

    @staticmethod
    def publish(payload, info, arg1, arg2):
        """Called to notify the client."""

        # Here `payload` contains the `payload` from the `broadcast()`
        # invocation (see below). You can return `MySubscription.SKIP`
        # if you wish to suppress the notification to a particular
        # client. For example, this allows to avoid notifications for
        # the actions made by this particular client.

        return MySubscription(event="Something has happened!")

class Query(graphene.ObjectType):
    """Root GraphQL query.

    The Graphene library requires us to define a Query with at least one
    field. This value field is not used in this example.
    """
    # Check Graphene docs to see how to define queries.
    value = graphene.String()

    async def resolve_value(self):
        """Resolver to return predefined value."""
        return "test"

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
    "websocket": channels.routing.URLRouter([
        django.urls.path("graphql/", MyGraphqlWsConsumer.as_asgi()),
    ])
})
```

Notify<sup>[﹡](#redis-layer)</sup> clients when some event happens using
the `broadcast()` or `broadcast_sync()` method from the OS thread where
there is no running event loop:

```python
MySubscription.broadcast(
    # Subscription group to notify clients in.
    group="group42",
    # Dict delivered to the `publish` method.
    payload={},
)
```

Notify<sup>[﹡](#redis-layer)</sup> clients in a coroutine function
with async `broadcast()` or `broadcast_async()` method:

```python
await MySubscription.broadcast(
    # Subscription group to notify clients in.
    group="group42",
    # Dict delivered to the `publish` method.
    payload={},
)
```

<a name="redis-layer">﹡)</a> In case you are testing your client code
by notifying it from the Django Shell, you have to setup a
[channel layer](https://channels.readthedocs.io/en/latest/topics/channel_layers.html#configuration)
in order for the two instance of your application. The same applies in
production with workers.

You should prefer async resolvers and async middleware over sync ones.
Async versions will result in faster code execution. To do DB operations
you can use
[Django 4 asynchronous queries](https://docs.djangoproject.com/en/4.1/topics/async/).

## Example

You can find simple usage example in the [example](example/) directory.

Run:
```shell
cd example/
# Initialize database.
./manage.py migrate
# Create "user" with password "user".
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
Check the
[protocol description](https://github.com/apollographql/subscriptions-transport-ws/blob/master/PROTOCOL.md)
for details.

### Automatic Django model serialization

The `Subscription.broadcast` uses Channels groups to deliver a message
to the `Subscription`'s `publish` method.
[ASGI specification](https://github.com/django/asgiref/blob/master/specs/asgi.rst#events)
clearly states what can be sent over a channel, and Django models are
not in the list. Since it is common to notify clients about Django
models changes we manually serialize the `payload` using
[MessagePack](https://github.com/msgpack/msgpack-python)
and hack the process to automatically serialize Django models following
the the Django's guide
[Serializing Django objects](https://docs.djangoproject.com/en/dev/topics/serialization/).

### Execution

- Different requests from different WebSocket client are processed
  asynchronously.
- By default different requests (WebSocket messages) from a single
  client are processed concurrently by using an event loop (for async
  resolvers) or different worker threads (for sync resolvers). (It is
  possible to change the maximum number of worker threads with the
  `ASGI_THREADS` environment variable of the
  [asgiref](https://github.com/django/asgiref/) library). So there is no
  guarantee that requests will be processed in the same order the client
  sent these requests. Actually, with HTTP we have this behavior for
  decades.
- It is possible to serialize message processing by setting
  `strict_ordering` to `True`. But note, this disables parallel requests
  execution - in other words, the server will not start processing
  another request from the client before it finishes the current one.
  See comments in the class `GraphqlWsConsumer`.
- All subscription notifications are delivered in the order they were
  issued.

Each request (WebSocket message) processing starts in the main thread of
the process. With exception that the request's parsing and validation is
offloaded into a thread pool. Resolver calls made from the main thread.
And for each resolver it checks whether the resolver is a coroutine
function. If it is a coroutine function, then the resolver is launched
from the main thread. If it is not a coroutine function (a synchronous
function), then the resolver is launched from the thread pool. As you
know an asyncio eventloop call is faster than a thread call. So you
should prefer asynchronous resolvers in your code. It will work faster.

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

The context object contains several fields defined by this library:
- `channel_name` - [The channel name from Channels](https://channels.readthedocs.io/en/stable/topics/channel_layers.html?highlight=channel_name#single-channels)

### Authentication

To enable authentication it is typically enough to wrap your ASGI
application into the  `channels.auth.AuthMiddlewareStack`:

```python
application = channels.routing.ProtocolTypeRouter({
    "websocket": channels.auth.AuthMiddlewareStack(
        channels.routing.URLRouter([
            django.urls.path("graphql/", MyGraphqlWsConsumer),
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
Check
[the Channels documentation](https://channels.readthedocs.io/en/latest/topics/authentication.html).
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

The GraphiQL provided by Graphene doesn't connect to your GraphQL
endpoint via WebSocket; instead you should use a modified GraphiQL
template under `graphene/graphiql.html` which will take precedence over
the one of Graphene. One such modified GraphiQL is provided in the
[example](example/) directory.

### Testing

To test GraphQL WebSocket API read the
[appropriate page in the Channels documentation](https://channels.readthedocs.io/en/latest/topics/testing.html).

In order to simplify unit testing there is a `GraphqlWsTransport`
implementation based on the Django Channels testing communicator:
`channels_graphql_ws.testing.GraphqlWsTransport`. Check its docstring
and take a look at the [tests](/tests) to see how to use it.

### Subscription activation confirmation

The original Apollo's protocol does not allow client to know when a
subscription activates. This inevitably leads to the race conditions on
the client side. Sometimes it is not that crucial, but there are cases
when this leads to serious issues.

To solve this problem, there is the `GraphqlWsConsumer` setting
`confirm_subscriptions` which when set to `True` will make the consumer
issue an additional `next` message which confirms the subscription
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
`GraphqlWsConsumer` subclass, like this:

```python
async def my_middleware(next_middleware, root, info, *args, **kwds):
    """My custom GraphQL middleware."""
    # Invoke next middleware.
    result = next_middleware(root, info, *args, **kwds)
    if graphql.pyutils.is_awaitable(result):
       result = await result
    return result

class MyGraphqlWsConsumer(channels_graphql_ws.GraphqlWsConsumer):
    ...
    middleware = [my_middleware]
```

It is recommended to write asynchronous middlewares. But synchronous
middlewares are also supported:

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
[relevant section in the Graphene documentation](https://docs.graphene-python.org/en/latest/execution/middleware/#middleware).

## Alternatives

There is a [Tomáš Ehrlich](https://gist.github.com/tricoder42)
GitHubGist
[GraphQL Subscription with django-channels](https://gist.github.com/tricoder42/af3d0337c1b33d82c1b32d12bd0265ec)
which this implementation was initially based on.

There is a promising
[GraphQL WS](https://github.com/graphql-python/graphql-ws)
library by the Graphene authors. In particular
[this pull request](https://github.com/graphql-python/graphql-ws/pull/9)
gives a hope that there will be native Graphene implementation of the
WebSocket transport with subscriptions one day.

## Development

### Bootstrap

_A reminder of how to setup an environment for the development._

1. Install PyEnv to be able to work with many Python versions at once
   [PyEnv→Installation](https://github.com/pyenv/pyenv#installation).
2. Install Python versions needed. The command should be executed in the
   project's directory:
   ```shell
   $ pyenv local | xargs -L1 pyenv install
   ```
3. Check that pyenv works correctly. The command:
   ```shell
   $ pyenv versions
   ```
   should show python versions enlisted in
   [.python-version](.python-version).  If everything is set up
   correctly pyenv will switch version of python when you enter and
   leave the project's directory. Inside the directory `pyenv which
   python` should show you a python installed in pyenv, outside the dir
   it should be the system python.
3. Install Poetry to the system Python.
   ```shell
   $ curl -sSL https://install.python-poetry.org | python3 -
   ```
   It is important to install Poetry into the system Python, NOT in your
   virtual environment. For details see Poetry docs: https://python-poetry.org/docs/#installation
4. Create local virtualenv in `.venv`, install all project dependencies
   (from `pyproject.toml`) except the project itself.
   ```shell
   $ poetry install --no-root
   ```
5. Activate virtualenv
   There are options:
   - With Poetry:
     ```shell
     $ poetry shell
     ```
   - Manually:
     ```shell
     $ source .venv/bin/activate
     ```
   - With VS Code: Choose `.venv` with "Python: Select interpreter" and
     reopen the terminal.

6. Upgrade Pip:
   ```shell
   $ pip install --upgrade pip
   ```

Use:

[![Code style: black](
    https://img.shields.io/badge/code%20style-black-000000.svg
)](
    https://github.com/ambv/black
)

### How to read the code

The code is quite complex. You might need some time to dive into. Here
are quick insights to help you to get on track.

Important classes are `GraphqlWsConsumer` and `Subscription`.

When server receives JSON from the client, the
`GraphqlWsConsumer.receive_json` method is called. Then the request is
passed to the `_on_gql_start` method. Most magic happens here.

The `sync_to_async_middleware` is used to allow execution of blocking
operations inside of resolvers.

Queries and mutations are handled in one way, and subscriptions in
another: you can find the big if-else block inside of the
`_on_gql_start` method.

`GraphqlWsConsumer` and `Subscription` instances are connected one to
each other with help of `context._channels_graphql_ws`. The data they
share is specified in the `PrivateSubscriptionContext` data type.

The `GraphqlWsConsumer._register_subscription` method is quite important
for subscriptions to work. The `Subscription._subscribe_resolver` method
is calling it on new subscription. And the `_subscribe_resolver` method
is responsible for handling subscription lifecycle.

Read through implementations of those methods.

### Running tests

_A reminder of how to run tests._

- Run all tests on all supported Python versions:
   ```shell
   $ tox
   ```
- Run all tests on a single Python version, e.g on Python 3.8:
   ```shell
   $ tox -e py38
   ```
- Example of running a single test:
   ```shell
   $ tox -e py310 -- tests/test_basic.py::test_main_usecase
   ```
- Running on currently active Python directly with Pytest:
   ```shell
   $ poetry run pytest
   ```

### Making release

_A reminder of how to make and publish a new release._

1. Merge all changes to the master branch and switch to it.
2. Update version: `poetry version minor`.
3. Update [CHANGELOG.md](./CHANGELOG.md).
4. Update [README.md](./README.md) (if needed).
5. Commit changes made above.
6. Git tag: `git tag vX.X.X && git push --tags`.
7. Publish release to PyPI: `poetry publish --build`.
8. Update
   [release notes](https://github.com/datadvance/DjangoChannelsGraphqlWs/releases)
   on GitHub.

## Contributing

This project is developed and maintained by DATADVANCE LLC. Please
submit an issue if you have any questions or want to suggest an
improvement.

## Acknowledgements

This work is supported by the Russian Foundation for Basic Research
(project No. 15-29-07043).
