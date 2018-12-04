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

"""GraphQL client and transport for testing purposes.

Transport class for the tests must implement `receive_nothing` method.
"""

import asyncio
import time

import channels.testing

from .client import GraphqlWsClient
from .transport import GraphqlWsTransport, GraphqlWsTransportAiohttp


class GraphqlWsTransportAiohttpTesting(GraphqlWsTransportAiohttp):
    """Aiohttp websockets transport for testing client."""

    async def receive_nothing(self, timeout=GraphqlWsTransport.TIMEOUT, interval=0.01):
        """Check that there is no messages left."""
        # `interval` has precedence over `timeout`.
        start = time.monotonic()
        while time.monotonic() < start + timeout:
            if self._output_queue.empty():
                return True
            await asyncio.sleep(interval)
        return self._output_queue.empty()


class GraphqlWsTransportChannelsTesting(
    GraphqlWsTransport, channels.testing.WebsocketCommunicator
):
    """Testing client transport which uses channels protocol instead of
    real websocket connection.
    """

    def __init__(self, *args, **kwds):
        kwds.setdefault("subprotocols", ["graphql-ws"])
        kwds.setdefault("path", "graphql/")
        channels.testing.WebsocketCommunicator.__init__(self, *args, **kwds)

    async def receive_json_from(self, timeout=None):
        """Overwrite to tune the `timeout` argument."""
        if timeout is None:
            timeout = self.TIMEOUT
        implementation = channels.testing.WebsocketCommunicator.receive_json_from
        return await implementation(self, timeout)

    async def connect(self, timeout=GraphqlWsTransport.TIMEOUT):
        """Emulate connect to the server."""
        await channels.testing.WebsocketCommunicator.connect(self, timeout)

    async def send(self, request):
        """Send request as json to the channels."""
        await channels.testing.WebsocketCommunicator.send_json_to(self, request)

    async def receive(self, timeout=GraphqlWsTransport.TIMEOUT):
        """Get next response from channels."""
        return await self.receive_json_from(timeout=timeout)

    async def receive_nothing(self, timeout=GraphqlWsTransport.TIMEOUT, interval=0.01):
        """Check that there is nothing more to receive."""
        implementation = channels.testing.WebsocketCommunicator.receive_nothing
        return await implementation(self, timeout, interval)

    async def shutdown(self, timeout=GraphqlWsTransport.TIMEOUT):
        """Disconnect from the server."""
        await self.disconnect(timeout=timeout)
        await self.wait(timeout=timeout)


class GraphqlWsClientTesting(GraphqlWsClient):
    """GraphQL client with additional methods for tests.

    By default uses `GraphqlWsTransportChannelsTesting` as transport.
    Different transport may be passed via keyword argument `transport`.
    """

    def __init__(self, *args, **kwds):
        transport = kwds.get("transport")
        if transport is None:
            transport = GraphqlWsTransportChannelsTesting(*args, **kwds)
        super().__init__(transport)

        self._assert_type = None
        self._assert_id = None

    @property
    def transport(self):
        """Get access to the transport for tests."""
        return self._transport

    async def receive_assert(self, wait_id=None, assert_id=None, assert_type=None):
        """Check type and id of the next data response."""
        response = await self._next_response(wait_id=wait_id)

        if assert_type is not None:
            assert response["type"] == assert_type, (
                f"Type `{assert_type}` expected, but `{response['type']}` received!"
                f" Response: {response}."
            )
        if assert_id is not None:
            assert response["id"] == assert_id, "Response id != expected id!"

        return self._response_payload(response)

    async def assert_no_messages(self, message=None):
        """Assure no data response received."""

        while True:
            if await self._transport.receive_nothing():
                return
            response = await self._transport.receive()
            assert self._is_keep_alive_response(response), (
                f"{message}\n{response}"
                if message is not None
                else f"Message received when nothing expected!\n{response}"
            )
