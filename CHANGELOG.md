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

# Changelog

## [1.0.0rc7] - 2022-05-12

- Support for a new WebSocket sub-protocol `graphql-transport-ws` added.
  Sub-protocol specification:
  https://github.com/enisdenjo/graphql-ws/blob/master/PROTOCOL.md.

## [1.0.0rc6] - 2023-05-10

- GraphQL parsing and message serialization now perform concurrently
  by `sync_to_async(...,thread_sensitive=False)`.

## [1.0.0rc5] - 2023-05-05

WARNING: Release contains backward incompatible changes!

- To suppress/drop subscription notification just return `None` from the
  subscription resolver. Previously it was necessary to return special
  `SKIP` object which is no longer the case..
- Python 3.8 compatibility brought back. Tests pass OK.

## [1.0.0rc4] - 2023-05-03

- `GraphqlWsConsumer.warn_resolver_timeout` removed to avoid mess with
  user specified middlewares. This functionality can easily be
  implemented on the library user level by creating a designated
  middleware.
- `GraphqlWsConsumer.middleware` accepts an instance of
  `graphql.MiddlewareManager` or the list of functions. Same as the
  argument `middleware` of `graphql.execute` method.
- Fixed broken example.

## [1.0.0rc3] - 2023-05-02

- Invoke synchronous resolvers in the main thread with eventloop. So
  there is no difference in this aspect with async resolvers. This
  corresponds to the behavior of the
  [`graphql-core`](https://github.com/graphql-python/graphql-core)
  library.
- Added example of middleware which offloads synchronous resolvers to
  the threadpool.
- Fixed bug with GraphQL WrappingTypes like GraphQLNonNull causing
  exceptions when used as subscription field.
- Fixed broken example.

## [1.0.0rc2] - 2023-04-28

Broken support of previous Python version brought back.

## [1.0.0rc1] - 2023-04-28

- DjangoChannelsGraphqlWs has migrated to the recent versions of Django,
  Channels, and Graphene. All other Python dependencies updated.
- Server outputs a warning to the log when operation/resolver takes
  longer than specified timeout, which is one second by default. The
  settings `GraphqlWsConsumer.warn_operation_timeout` and
  `GraphqlWsConsumer.warn_resolver_timeout` allow to tune the timeout or
  even disable the warning at all.
- If exception raises from the resolver a response now contains a field
  "extensions.code" with a class name of the exception.
- Added support for async resolvers and middlewares.
- WARNING: This release is not backward compatible with previous ones!
  The main cause is a major update of Django, Channels, and Graphene,
  but there are some introduced by the library itself. In particular:
  - Context lifetime and content have changed. See README.md for
    details.

## [0.9.1] - 2022-01-27

- Minor fix in logging.

## [0.9.0] - 2021-10-19

- Ability to configure server notification queue limit per subscription.

## [0.8.0] - 2021-02-12

- Switched to Channels 3.
- Python dependencies updated.

## [0.7.5] - 2020-12-06

- Django channel name added to the context as the `channel_name` record.
- Python dependencies updated.

## [0.7.4] - 2020-07-27

- Client method 'execute' consumes 'complete' message in case of error.

## [0.7.3] - 2020-07-26

- Logging slightly improved. Thanks to @edouardtheron.

## [0.7.2] - 2020-07-26

- Quadratic growth of threads number has stopped. The problem was
  observer on Python 3.6 and 3.7 and was not on 3.8, because starting
  with 3.8 `ThreadPoolExecutor` does not spawn new thread if there are
  idle threads in the pool already. The issue was in the fact that for
  each of worker thread we run an event loop which default executor is
  the `ThreadPoolExecutor` with default (by Python) number of threads.
  All this eventually ended up in hundreds of thread created for each
  `GraphqlWsConsumer` subclass.

## [0.7.1] - 2020-07-25

- Python 3.6 compatibility brought back.

## [0.7.0] - 2020-07-25

- Subscription `payload` now properly serializes the following Python
  `datetime` types:
  - `datetime.datetime`
  - `datetime.date`
  - `datetime.time`

## [0.6.0] - 2020-07-25

- Allow `msgpack v1.*` in the dependencies requirements.
- Windows support improved: tests fixed, example fixed.
- Development instructions updated in the `README.md`.
- Removed `graphql-core` version lock, it is hold by `graphene` anyway.
- Many CI-related fixes.

## [0.5.0] - 2020-04-05

- Added support for Python 3.6.
- Dependencies updated and relaxed, now Django 3 is allowed.
- Testing framework improved to run on 3.6, 3.7, 3.8 with Tox.
- Client setup documentation (Python, GraphiQL) improved. Thanks to
  Rigel Kent.
- Error logging added to simplify debugging, error messages improved.
- Fixed wrong year in this changelog (facepalm).
- Configuration management made slightly simple.
- Bandit linter removed as useless.
- More instructions for the package developers in the `README.md` added.

## [0.4.2] - 2019-08-23

- Example improved to show how to handle HTTP auth (#23).

## [0.4.1] - 2019-08-20

- Better error message when Channels channel layer is not available.

## [0.4.0] - 2019-08-20

- Context (`info.context` in resolvers) lifetime has changed. It is now
  an object-like wrapper around [Channels
  scope](https://channels.readthedocs.io/en/latest/topics/consumers.html#scope)
  typically available as `self.scope` in the Channels consumers. So you
  can access Channels scope as `info.context`. Modifications made in
  `info.context` are stored in the Channels scope, so they are persisted
  as long as WebSocket connection lives. You can work with
  `info.context` both as with `dict` or as with `SimpleNamespace`.

## [0.3.0] - 2019-08-17

- Added support for GraphQL middleware, look at the
  `GraphqlWsConsumer.middleware` setting.
- Example reworked to illustrate how to authenticate clients.
- Channels `scope` is now stored in `info.context.scope` as `dict`.
  (Previously `info.context` was a copy of `scope` wrapped into the
  `types.SimpleNamespace`). The thing is the GraphQL `info.context` and
  Channels `scope` are different things. The `info.context` is a storage
  for a single GraphQL operation, while `scope` is a storage for the
  whole WebSocket connection. For example now use
  `info.context.scope["user"]` to get access to the Django user model.

## [0.2.1] - 2019-08-14

- Changelog eventually added.
- `GraphqlWsClient` transport timeout increased 5->60 seconds.
- Dependency problem fixed, version numbers frozen in `poetry.lock` file
  for non-development dependencies.
- Tests which failed occasionally due to wrong DB misconfiguration.
