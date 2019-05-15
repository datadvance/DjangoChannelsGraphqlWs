#
# coding: utf-8
# Copyright (c) 2019 DATADVANCE
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


"""GraphQL WebSocket client transports."""

import asyncio
import json
import time

import aiohttp

from . import graphql_ws


class GraphqlWsTransport:
    """Transport interface for the `GraphqlWsClient`."""

    # Default timeout for the WebSocket messages.
    TIMEOUT = 60
    # Timeout in seconds to wait to ensure the queue of messages
    # is empty.
    RECEIVE_NOTHING_TIMEOUT = 1
    # Number of seconds to wait for another check for new events.
    RECEIVE_NOTHING_INTERVAL = 0.01

    async def connect(self, timeout=TIMEOUT):
        """Connect to the server."""
        raise NotImplementedError()

    async def send(self, request):
        """Send request data."""
        raise NotImplementedError()

    async def receive(self, timeout=TIMEOUT):
        """Receive server response."""
        raise NotImplementedError()

    async def receive_nothing(
        self, timeout=RECEIVE_NOTHING_TIMEOUT, interval=RECEIVE_NOTHING_INTERVAL
    ):
        """Check that there is no messages left."""
        raise NotImplementedError()

    async def shutdown(self, timeout=TIMEOUT):
        """Disconnect from the server."""
        raise NotImplementedError()


class GraphqlWsTransportAiohttp(GraphqlWsTransport):
    """Transport based on AIOHTTP WebSocket client.

    Args:
        url: WebSocket GraphQL endpoint.
        cookies: HTTP request cookies.
        headers: HTTP request headers.
    """

    def __init__(self, url, cookies=None, headers=None):
        # Server URL.
        self._url = url
        # HTTP cookies.
        self._cookies = cookies
        # HTTP headers.
        self._headers = headers
        # AIOHTTP connection.
        self._connection = None
        # A task which processes incoming messages.
        self._message_processor = None
        # A queue for incoming messages.
        self._incoming_messages = asyncio.Queue()

    async def connect(self, timeout=GraphqlWsTransport.TIMEOUT):
        """Establish a connection with the WebSocket server.

        Returns:
            `(True, <chosen-subprotocol>)` if connection accepted.
            `(False, None)` if connection rejected.
        """

        connected = asyncio.Event()
        self._message_processor = asyncio.create_task(
            self._process_messages(connected, timeout)
        )
        await asyncio.wait(
            [connected.wait(), self._message_processor],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if self._message_processor.done():
            # Make sure to raise an exception from the task.
            self._message_processor.result()
            raise RuntimeError(f"Failed to connect to the server: {self._url}!")

    async def send(self, request):
        """Send given `request` after encoding it to the JSON."""
        assert self._connection is not None, "Connect must be called first!"
        await self._connection.send_str(json.dumps(request))

    async def receive(self, timeout=GraphqlWsTransport.TIMEOUT):
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
            payload = await asyncio.wait_for(self._incoming_messages.get(), timeout)
            assert isinstance(payload, str), "Non-string data received!"
            return json.loads(payload)
        except asyncio.TimeoutError as e:
            # See if we have another error to raise inside.
            if self._message_processor.done():
                self._message_processor.result()
            raise e

    async def receive_nothing(
        self,
        timeout=GraphqlWsTransport.RECEIVE_NOTHING_TIMEOUT,
        interval=GraphqlWsTransport.RECEIVE_NOTHING_INTERVAL,
    ):
        """Check that there is no messages left."""
        # The `interval` has precedence over the `timeout`.
        start = time.monotonic()
        while time.monotonic() < start + timeout:
            if self._incoming_messages.empty():
                return True
            await asyncio.sleep(interval)
        return self._incoming_messages.empty()

    async def shutdown(self, timeout=GraphqlWsTransport.TIMEOUT):
        """Close the connection gracefully."""
        await self._connection.close(code=1000)
        try:
            await asyncio.wait_for(asyncio.shield(self._message_processor), timeout)
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

    async def _process_messages(self, connected, timeout):
        """A task to process messages coming from the connection.

        Args:
            connected: Event for reporting that connection established.
            timeout: Connection timeout in seconds.
        """

        session = aiohttp.ClientSession(cookies=self._cookies, headers=self._headers)
        async with session as session:
            connection = session.ws_connect(
                self._url,
                protocols=[graphql_ws.GRAPHQL_WS_SUBPROTOCOL],
                timeout=timeout,
            )
            async with connection as self._connection:
                if self._connection.protocol != graphql_ws.GRAPHQL_WS_SUBPROTOCOL:
                    raise RuntimeError(
                        f"Server uses wrong subprotocol: {self._connection.protocol}!"
                    )
                connected.set()
                async for msg in self._connection:
                    await self._incoming_messages.put(msg.data)
                    if msg.type == aiohttp.WSMsgType.CLOSED:
                        break
