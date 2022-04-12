"""Just `OperationContext` class."""

from channels_graphql_ws.scope_as_context import ScopeAsContext


class OperationContext(ScopeAsContext):
    """
    The context intended to use in methods of Graphene classes as `info.context`.

    This class provides two public properties:
    1. `scope` - per-connection context. This is the `scope` of Django Channels.
    2. `operation_context` - per-operation context. Empty. Fill free to store your's
       data here.

    For backward compatibility:
    - Method `_asdict` returns the `scope`.
    - Other attributes are routed to the `scope`.
    """

    def __init__(self, scope: dict):
        """Nothing interesting here."""
        super().__init__(scope)
        self._operation_context: dict = {}

    @property
    def scope(self) -> dict:
        """Return the scope."""
        return self._scope

    @property
    def operation_context(self) -> dict:
        """Return the per-operation context."""
        return self._operation_context
