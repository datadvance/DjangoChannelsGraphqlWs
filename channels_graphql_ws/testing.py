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

"""GraphQL client transport for testing purposes."""

import asyncio
from typing import Optional

import channels.testing

import channels_graphql_ws.client
import channels_graphql_ws.transport


class GraphqlWsClient(channels_graphql_ws.client.GraphqlWsClient):
    """Add functions useful for testing purposes."""

    # Time in seconds to wait to ensure the queue of messages is empty.
    RECEIVE_NOTHING_ATTEMPTS = 10
    # Number of seconds to wait for another check for new events.
    RECEIVE_NOTHING_INTERVAL = 0.1

    async def assert_no_messages(
        self,
        error_message=None,
        attempts=RECEIVE_NOTHING_ATTEMPTS,
        interval=RECEIVE_NOTHING_INTERVAL,
    ):
        """Ensure no data message received."""
        approx_execution_time = attempts * interval
        for _ in range(attempts):
            try:
                # Be sure internal timeout in `receive` will not expire,
                # if it is expired then transport stops working by some
                # reason and only raises CancellerError messages.
                received = await asyncio.wait_for(
                    self.transport.receive(timeout=10 * approx_execution_time),
                    timeout=interval,
                )
            except asyncio.TimeoutError:
                continue
            else:
                if self._is_ping_pong_message(received):
                    continue
                assert False, (
                    f"{error_message}\n{received}"
                    if error_message is not None
                    else f"Message received when nothing expected!\n{received}"
                )

    async def connect_and_init(self, connect_only: bool = False) -> None:
        """Establish and initialize WebSocket GraphQL connection.

        1. Establish WebSocket connection.
        2. Initialize GraphQL connection. Skipped if connect_only=True.
        """
        if connect_only:
            await self._transport.connect()
            self._is_connected = True
        else:
            await super().connect_and_init()

    async def send_raw_message(self, message):
        """Send a raw message.

        This can be useful for testing, for example, to check that the
        server responds appropriately to malformed messages.
        """
        await self._transport.send(message)

    async def wait_disconnect(self, timeout=None, assert_code=None):
        """Wait server to close the connection.

        Used in tests to check that WebSocket closed with expected code.

        Args:
            timeout: Seconds to wait the connection to close.
            assert_code: The code with which the connection should
                be closed.

        Raises:
            `asyncio.TimeoutError` when timeout is reached.

        """
        message = await self._transport.wait_disconnect(timeout)
        if assert_code is not None:
            assert message["code"] == assert_code, (
                "The connection was closed with the wrong code!"
                f" Expected '{assert_code}' received '{message['code']}'!"
            )
        self._is_connected = False

    async def receive_error(self, msg_id):
        """Receive GraphQL `error` or `next` message with errors.

        Args:
            msg_id: Subscribe message ID, in response
                to which the `next` or `error` message should be sent.
        Returns:
            `errors` and `data` values from response.

        """
        if self._subprotocol == "graphql-transport-ws":
            try:
                await self.receive(wait_id=msg_id, assert_type="error")
            except channels_graphql_ws.GraphqlWsResponseError as ex:
                return ex.response["payload"], None
        else:
            try:
                await self.receive_next(msg_id)
            except channels_graphql_ws.GraphqlWsResponseError as ex:
                return (
                    ex.response["payload"]["errors"],
                    ex.response["payload"]["data"],
                )


class GraphqlWsTransport(channels_graphql_ws.transport.GraphqlWsTransport):
    """Testing client transport to work without WebSocket connection.

    Client is implemented based on the Channels `WebsocketCommunicator`.
    """

    # Slightly reduce timeout in testing.
    TIMEOUT: float = 30

    def __init__(
        self,
        application,
        path,
        communicator_kwds=None,
        subprotocol="graphql-transport-ws",
    ):
        """Constructor."""
        self._comm = channels.testing.WebsocketCommunicator(
            application=application,
            path=path,
            subprotocols=[subprotocol],
            **(communicator_kwds or {}),
        )

    async def connect(self, timeout: Optional[float] = None) -> None:
        """Connect to the server."""
        ok, code = await self._comm.connect(timeout or self.TIMEOUT)
        if not ok:
            raise RuntimeError(
                f"Failed to establish fake connection! WebSocket close code={code}!"
            )

    async def send(self, message: dict) -> None:
        """Send message."""
        await self._comm.send_json_to(message)

    async def receive(self, timeout: Optional[float] = None) -> dict:
        """Receive message."""
        return dict(await self._comm.receive_json_from(timeout or self.TIMEOUT))

    async def disconnect(self, timeout: Optional[float] = None) -> None:
        """Disconnect from the server."""
        await self._comm.disconnect(timeout=timeout or self.TIMEOUT)

    async def wait_disconnect(self, timeout: Optional[float] = None) -> dict:
        """Wait server to close the connection."""
        message = await self._comm.receive_output(timeout or self.TIMEOUT)
        assert message["type"] == "websocket.close", (
            "Message with a wrong type received while waiting server to close the"
            f" connection! Expected 'websocket.close' received '{message['type']}'!"
        )
        await self._comm.disconnect(timeout=timeout or self.TIMEOUT)
        return dict(message)
