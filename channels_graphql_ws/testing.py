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

"""GraphQL client and transport for testing purposes.

Transport class for the tests must implement `receive_nothing` method.
"""

import channels.testing

from .transport import GraphqlWsTransport


class GraphqlWsTransportChannels(
    GraphqlWsTransport, channels.testing.WebsocketCommunicator
):
    """Testing client transport to work without WebSocket connection."""

    def __init__(self, *args, **kwds):
        """Constructor, see `channels.testing.WebsocketCommunicator`."""
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

    async def receive_nothing(
        self,
        timeout=GraphqlWsTransport.RECEIVE_NOTHING_TIMEOUT,
        interval=GraphqlWsTransport.RECEIVE_NOTHING_INTERVAL,
    ):
        """Check that there is nothing more to receive."""
        implementation = channels.testing.WebsocketCommunicator.receive_nothing
        return await implementation(self, timeout, interval)

    async def shutdown(self, timeout=GraphqlWsTransport.TIMEOUT):
        """Disconnect from the server."""
        await self.disconnect(timeout=timeout)
        await self.wait(timeout=timeout)
