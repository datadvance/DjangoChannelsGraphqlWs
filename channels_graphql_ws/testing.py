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

"""GraphQL client transport for testing purposes."""

import asyncio
from typing import Optional

import channels.testing

from . import client, transport


class GraphqlWsClient(client.GraphqlWsClient):
    """Add functions useful for testing purposes."""

    # Time in seconds to wait to ensure the queue of messages is empty.
    RECEIVE_NOTHING_ATTEMPTS = 100
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
                if self._is_keep_alive_response(received):
                    continue
                assert False, (
                    f"{error_message}\n{received}"
                    if error_message is not None
                    else f"Message received when nothing expected!\n{received}"
                )


class GraphqlWsTransport(transport.GraphqlWsTransport):
    """Testing client transport to work without WebSocket connection.

    Client is implemented based on the Channels `WebsocketCommunicator`.
    """

    def __init__(self, application, path, communicator_kwds=None):
        """Constructor."""
        self._comm = channels.testing.WebsocketCommunicator(
            application=application,
            path=path,
            subprotocols=["graphql-ws"],
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

    async def wait_disconnect(self, timeout: Optional[float] = None) -> None:
        """Wait server to close the connection."""
        message = await self._comm.receive_output(timeout or self.TIMEOUT)
        assert message["type"] == "websocket.close", (
            "Message with a wrong type received while waiting server to close the"
            f" connection! Expected 'websocket.close' received '{message['type']}'!"
        )
        assert message["code"] == 4000, (
            "Message with a wrong code received while waiting server to close the"
            f" connection! Expected '4000' received '{message['code']}'!"
        )
        await self._comm.disconnect(timeout=timeout or self.TIMEOUT)
