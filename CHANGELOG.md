
# Changelog

## [0.4.2] - 2019-08-23

### Added

- Example improved to show how to handle HTTP auth (#23).

## [0.4.1] - 2019-08-20

### Changed

- Better error message when Channels channel layer is not available.

## [0.4.0] - 2019-08-20

### Changed

- Context (`info.context` in resolvers) lifetime has changed. It is now
  an object-like wrapper around [Channels
  scope](https://channels.readthedocs.io/en/latest/topics/consumers.html#scope)
  typically available as `self.scope` in the Channels consumers. So you
  can access Channels scope as `info.context`. Modifications made in
  `info.context` are stored in the Channels scope, so they are persisted
  as long as WebSocket connection lives. You can work with
  `info.context` both as with `dict` or as with `SimpleNamespace`.

## [0.3.0] - 2019-08-17

### Added

- Support for GraphQL middleware, look at the
  `GraphqlWsConsumer.middleware` setting.
- Example reworked to illustrate how to authenticate clients.

### Changed

- Channels `scope` is now stored in `info.context.scope` as `dict`.
  (Previously `info.context` was a copy of `scope` wrapped into the
  `types.SimpleNamespace`). The thing is the GraphQL `info.context` and
  Channels `scope` are different things. The `info.context` is a storage
  for a single GraphQL operation, while `scope` is a storage for the
  whole WebSocket connection. For example now use
  `info.context.scope["user"]` to get access to the Django user model.

## [0.2.1] - 2019-08-14

### Added

- Changelog eventually added.

### Changed

- `GraphqlWsClient` transport timeout increased 5->60 seconds.

### Fixed

- Dependency problem fixed, version numbers frozen in `poetry.lock` file
  for non-development dependencies.
- Tests which failed occasionally due to wrong DB misconfiguration.
