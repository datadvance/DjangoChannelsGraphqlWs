#
# coding: utf-8
# Copyright (c) 2018 DATADVANCE
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


"""GraphQL client websockets transport interface and implementation."""

import asyncio
import json

import aiohttp


class GraphqlWsTransport:
    """Base transport interface for GraphQL server client."""

    # Default timeout for websocket messages.
    TIMEOUT = 5
    # Subprotocol used by server.
    SUBPROTOCOL = "graphql-ws"

    async def connect(self, timeout=TIMEOUT):
        """Connect to the server."""
        raise NotImplementedError()

    async def send(self, request):
        """Send request data."""
        raise NotImplementedError()

    async def receive(self, timeout=TIMEOUT):
        """Receive server response."""
        raise NotImplementedError()

    async def shutdown(self, timeout=TIMEOUT):
        """Disconnect from the server."""
        raise NotImplementedError()


class GraphqlWsTransportAiohttp(GraphqlWsTransport):
    """Transport based on aiohttp websockets client.

    Args:
        url: Websocket GraphQL endpoint.
        cookies: User session cookies.
        headers: Connection request headers.
    """

    def __init__(self, url, cookies=None, headers=None):
        self._url = url
        self._cookies = cookies
        self._headers = headers
        self._connection = None
        self._task = None
        self._output_queue = asyncio.Queue()

    async def connect(self, timeout=GraphqlWsTransport.TIMEOUT):
        """Establish connection with websocket server.

        Returns:
            `(True, <chosen-subprotocol>)` if connection accepted.
            `(False, None)` if connection rejected.
        """

        connected = asyncio.Event()
        self._task = asyncio.create_task(self._messages_loop(connected, timeout))
        await asyncio.wait(
            [connected.wait(), self._task], return_when=asyncio.FIRST_COMPLETED
        )
        if self._task.done():
            # Make sure to raise an exception from the task.
            self._task.result()
            raise RuntimeError(f"Failed to connect to the server: {self._url}!")

    async def send(self, request):
        """Sends JSON data to the backend as text frame."""
        assert self._connection is not None, "Connect must be called first!"
        await self._connection.send_str(json.dumps(request))

    async def receive(self, timeout=GraphqlWsTransport.TIMEOUT):
        """Receive a data frame from websocket. Will fail if the
        connection closes instead.

        Returns: either a bytestring or a unicode string depending on
            what sort of frame received.
        """

        # Make sure there's not an exception to raise from the task.
        if self._task.done():
            self._task.result()
        # Wait and receive the message.
        try:
            payload = await asyncio.wait_for(self._output_queue.get(), timeout)
            assert isinstance(payload, str), "Server must send text frames only!"
            return json.loads(payload)
        except asyncio.TimeoutError as e:
            # See if we have another error to raise inside.
            if self._task.done():
                self._task.result()
            raise e

    async def shutdown(self, timeout=GraphqlWsTransport.TIMEOUT):
        """Close connection."""
        await self._connection.close(code=1000)
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout)
            self._task.result()
        except asyncio.TimeoutError:
            pass
        finally:
            if not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

    async def _messages_loop(self, connected, timeout):
        """Task for processing messages incoming over websocket.

        Args:
            connected: Event for reporting that connection established.
            timeout: Connection timeout in seconds.
        """

        session = aiohttp.ClientSession(cookies=self._cookies, headers=self._headers)
        async with session as session:
            connection = session.ws_connect(
                self._url, protocols=[self.SUBPROTOCOL], timeout=timeout
            )
            async with connection as self._connection:
                if self._connection.protocol != self.SUBPROTOCOL:
                    raise RuntimeError(
                        f"Server uses wrong subprotocol: {self._connection.protocol}!"
                    )
                connected.set()
                async for msg in self._connection:
                    await self._output_queue.put(msg.data)
                    if msg.type == aiohttp.WSMsgType.CLOSED:
                        break
