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


"""GraphQL WebSocket client transport."""

import asyncio
import json
from typing import Optional

import aiohttp


class GraphqlWsTransport:
    """Transport interface for the `GraphqlWsClient`."""

    # Default timeout for the WebSocket messages.
    TIMEOUT: float = 60.0

    async def connect(self, timeout: Optional[float] = None) -> None:
        """Connect to the server."""
        raise NotImplementedError()

    async def send(self, message: dict) -> None:
        """Send message."""
        raise NotImplementedError()

    async def receive(self, timeout: Optional[float] = None) -> dict:
        """Receive message."""
        raise NotImplementedError()

    async def disconnect(self, timeout: Optional[float] = None) -> None:
        """Disconnect from the server."""
        raise NotImplementedError()

    async def wait_disconnect(self, timeout: Optional[float] = None) -> dict:
        """Wait server to close the connection."""
        raise NotImplementedError()


class GraphqlWsTransportAiohttp(GraphqlWsTransport):
    """Transport based on AIOHTTP WebSocket client.

    Args:
        url: WebSocket GraphQL endpoint.
        cookies: HTTP request cookies.
        headers: HTTP request headers.

    """

    def __init__(self, url, cookies=None, headers=None):
        """Constructor. See class description for details."""
        # Server URL.
        self._url = url
        # HTTP cookies.
        self._cookies = cookies
        # HTTP headers.
        self._headers = headers
        # AIOHTTP connection.
        self._connection: aiohttp.ClientWebSocketResponse
        # A task which processes incoming messages.
        self._message_processor: asyncio.Task
        # A queue for incoming messages.
        self._incoming_messages: asyncio.Queue = asyncio.Queue()

    async def connect(
        self, timeout: Optional[float] = None, subprotocol="graphql-transport-ws"
    ) -> None:
        """Establish a connection with the WebSocket server.

        Args:
            timeout: Connection timeout in seconds.
            subprotocol: WebSocket subprotocol to use by the Transport.
                Can have a value of "graphql-transport-ws" or
                "graphql-ws". By default set to "graphql-transport-ws".

        """
        assert subprotocol in (
            "graphql-transport-ws",
            "graphql-ws",
        ), "Transport only supports graphql-transport-ws and graphql-ws subprotocols!"
        connected = asyncio.Event()
        self._message_processor = asyncio.create_task(
            self._process_messages(connected, timeout or self.TIMEOUT, subprotocol)
        )
        await asyncio.wait(
            [connected.wait(), self._message_processor],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if self._message_processor.done():
            # Make sure to raise an exception from the task.
            self._message_processor.result()
            raise RuntimeError(f"Failed to connect to the server: {self._url}!")

    async def send(self, message: dict) -> None:
        """Send message."""
        assert self._connection is not None, "Client is not connected!"
        await self._connection.send_str(json.dumps(message))

    async def receive(self, timeout: Optional[float] = None) -> dict:
        """Wait and receive a message from the WebSocket connection.

        Method fails if the connection closes.

        Returns:
            The message received as a `dict`.

        """
        # Make sure there's no an exception to raise from the task.
        if self._message_processor.done():
            self._message_processor.result()

        # Wait and receive the message.
        try:
            payload = await asyncio.wait_for(
                self._incoming_messages.get(), timeout or self.TIMEOUT
            )
            assert isinstance(payload, str), "Non-string data received!"
            return dict(json.loads(payload))
        except asyncio.TimeoutError as ex:
            # See if we have another error to raise inside.
            if self._message_processor.done():
                self._message_processor.result()
            raise ex

    async def disconnect(self, timeout: Optional[float] = None) -> None:
        """Close the connection gracefully."""
        await self._connection.close(code=1000)
        try:
            await asyncio.wait_for(
                asyncio.shield(self._message_processor), timeout or self.TIMEOUT
            )
            self._message_processor.result()
        except asyncio.TimeoutError:
            pass
        finally:
            if not self._message_processor.done():
                self._message_processor.cancel()
                try:
                    await self._message_processor
                except asyncio.CancelledError:
                    pass

    async def wait_disconnect(self, timeout: Optional[float] = None) -> dict:
        """Wait server to close the connection."""
        raise NotImplementedError()

    async def _process_messages(self, connected, timeout, subprotocol):
        """Process messages coming from the connection.

        Args:
            connected: Event for reporting that connection established.
            timeout: Connection timeout in seconds.

        """
        session = aiohttp.ClientSession(cookies=self._cookies, headers=self._headers)
        async with session as session:
            connection = session.ws_connect(
                self._url,
                protocols=[subprotocol],
                timeout=timeout,
            )
            async with connection as self._connection:
                if self._connection.protocol != subprotocol:
                    raise RuntimeError(
                        f"Server uses wrong subprotocol: {self._connection.protocol}!"
                    )
                connected.set()
                async for msg in self._connection:
                    await self._incoming_messages.put(msg.data)
                    if msg.type == aiohttp.WSMsgType.CLOSED:
                        break
