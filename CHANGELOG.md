<!--
Copyright (C) DATADVANCE, 2010-2022

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

## [1.0.0rc1] - 2022-11-16

- Optional log messages to find slow operations and resolvers added.
- Support for async resolvers and middlewares.
- On exceptions a response now contains an "extensions.code" field with
  exception class name inside.
- The implementation now relies on the
  `channels.db.database_sync_to_async` function and on the thread pool
  from the `asgiref` library.
- Dependencies updated:
  - Django 4.x
  - channels 3.x
  - graphene 3.x

NOTE: The DjangoChannelsGraphqlWs library itself does not introduce any
      backward incompatible changes. But it's dependencies have
      incompatible changes. This includes: Django, Graphene and
      GraphQL-core libraries. Read the migration docs of those libraries
      during upgrade.

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
