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

"""Tests GraphQL over WebSockets with subscriptions.

Here we test `Subscription` and `GraphqlWsConsumer` classes.
"""

# NOTE: Tests use the GraphQL over WebSockets setup. All the necessary
#       items (schema, query, subscriptions, mutations, Channels
#       consumer & application) are defined at the end on this file with
#       the prefix "My".

import json
import textwrap
import uuid

import channels
import channels.testing as ch_testing
import django.urls
import graphene
import pytest

from .graphql_ws import GraphqlWsConsumer, Subscription


# Default timeout. Increased to avoid TimeoutErrors on slow machines.
TIMEOUT = 5


@pytest.mark.asyncio
async def test_main_usecase():
    """Test main use-case with the GraphQL over WebSocket."""

    # Channels communicator to test WebSocket consumers.
    comm = ch_testing.WebsocketCommunicator(application=my_app,
                                            path='graphql/',
                                            subprotocols='graphql-ws')

    print('Establish WebSocket connection and check a subprotocol.')
    connected, subprotocol = await comm.connect(timeout=TIMEOUT)
    assert connected, ('Could not connect to the GraphQL subscriptions '
                       'WebSocket!')
    assert subprotocol == 'graphql-ws', 'Wrong subprotocol received!'

    print('Initialize GraphQL connection.')
    await comm.send_json_to({'type': 'connection_init', 'payload': ''})
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp['type'] == 'connection_ack'

    print('Make simple GraphQL query and check the response.')
    uniq_id = str(uuid.uuid4().hex)
    await comm.send_json_to({
        'id': uniq_id,
        'type': 'start',
        'payload': {
            'query': textwrap.dedent('''
                query MyOperationName {
                    value
                }
            '''),
            'variables': {},
            'operationName': 'MyOperationName',
        }
    })
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp['id'] == uniq_id, 'Response id != request id!'
    assert resp['type'] == 'data', 'Type `data` expected!'
    assert 'errors' not in resp['payload']
    assert resp['payload']['data']['value'] == MyQuery.VALUE
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp['id'] == uniq_id, 'Response id != request id!'
    assert resp['type'] == 'complete', 'Type `complete` expected!'

    print('Subscribe to GraphQL subscription.')
    uniq_id = str(uuid.uuid4().hex)
    sub_id = uniq_id
    sub_param1 = str(uuid.uuid4().hex)
    sub_param2 = str(uuid.uuid4().hex)
    await comm.send_json_to({
        'id': uniq_id,
        'type': 'start',
        'payload': {
            'query': textwrap.dedent('''
                subscription MyOperationName($sub_param1: String,
                                                $sub_param2: String) {
                    my_sub(param1: $sub_param1, param2: $sub_param2) {
                        event
                    }
                }
            '''),
            'variables': {'sub_param1': sub_param1,
                            'sub_param2': sub_param2},
            'operationName': 'MyOperationName',
        }
    })
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp['id'] == uniq_id, 'Response id != request id!'
    assert resp['type'] == 'data', 'Type `data` expected!'
    assert 'errors' not in resp['payload']
    assert resp['payload']['data'] == {}

    print('Trigger the subscription by mutation to receive notification.')
    uniq_id = str(uuid.uuid4().hex)
    await comm.send_json_to({
        'id': uniq_id,
        'type': 'start',
        'payload': {
            'query': textwrap.dedent('''
                mutation MyOperationName($mut_param: String) {
                    my_mutation(param: $mut_param, cmd: BROADCAST) {
                        ok
                    }
                }
            '''),
            'variables': {'mut_param': sub_param1},
            'operationName': 'MyOperationName',
        }
    })

    # Mutation response.
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp['id'] == uniq_id, 'Response id != request id!'
    assert resp['type'] == 'data', 'Type `data` expected!'
    assert 'errors' not in resp['payload']
    assert resp['payload']['data'] == {'my_mutation': {'ok': True}}
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp['id'] == uniq_id, 'Response id != request id!'
    assert resp['type'] == 'complete', 'Type `complete` expected!'

    # Subscription notification.
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp['id'] == sub_id, ('Notification id != subscription id!')
    assert resp['type'] == 'data', 'Type `data` expected!'
    assert 'errors' not in resp['payload']
    assert json.loads(resp['payload']['data']['my_sub']['event']) == {
        'value': MySubscription.VALUE,
        'sub_param1': sub_param1,
        'sub_param2': sub_param2,
        'payload': MyMutation.PAYLOAD,
    }, 'Subscription notification contains wrong data!'

    print('Disconnect and wait the application to finish gracefully.')
    await comm.disconnect(timeout=TIMEOUT)
    await comm.wait(timeout=TIMEOUT)


@pytest.mark.asyncio
async def test_error_cases():
    """Test that server responds correctly when errors happen.

    Check that server responds with message of type `error` when there
    is a syntax error in the request, but `data` with `errors` field
    responds to the exception in a resolver.
    """

    # Channels communicator to test WebSocket consumers.
    comm = ch_testing.WebsocketCommunicator(application=my_app,
                                            path='graphql/',
                                            subprotocols='graphql-ws')

    print('Establish & initialize the connection.')
    await comm.connect(timeout=TIMEOUT)
    await comm.send_json_to({'type': 'connection_init', 'payload': ''})
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp['type'] == 'connection_ack'

    print('Check that query syntax error leads to the `error` response.')
    uniq_id = str(uuid.uuid4().hex)
    await comm.send_json_to({
        'id': uniq_id,
        'type': 'start',
        'payload': {
            'query': textwrap.dedent('''
                This list produces a syntax error!
            '''),
            'variables': {},
            'operationName': 'MyOperationName',
        }
    })
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp['id'] == uniq_id, 'Response id != request id!'
    assert resp['type'] == 'error', 'Type `error` expected!'
    payload = resp['payload']
    assert ('message' in payload and
            'locations' in payload), 'Response missing mandatory fields!'
    assert len(payload) == 2, 'Extra fields detected in the response!'

    print('Check that resolver error leads to the `data` message.')
    uniq_id = str(uuid.uuid4().hex)
    await comm.send_json_to({
        'id': uniq_id,
        'type': 'start',
        'payload': {
            'query': textwrap.dedent('''
                query MyOperationName {
                    value(issue_error: true)
                }
            '''),
            'variables': {},
            'operationName': 'MyOperationName',
        }
    })
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp['id'] == uniq_id, 'Response id != request id!'
    assert resp['type'] == 'data', 'Type `data` expected!'
    payload = resp['payload']
    assert payload['data']['value'] is None
    errors = payload['errors']
    assert len(errors) == 1, 'Single error expected!'
    assert errors[0]['message'] == MyQuery.VALUE
    assert 'locations' in errors[0]

    print('Disconnect and wait the application to finish gracefully.')
    await comm.disconnect(timeout=TIMEOUT)
    await comm.wait(timeout=TIMEOUT)

@pytest.mark.asyncio
async def test_subscribe_unsubscribe():
    # TODO: WRITE TEST HERE!
    # 1. subscribe -> unsubscribe from the client
    # 2. subscribe -> unsubscribe from the server
    pass


@pytest.mark.asyncio
async def test_groups():
    # TODO: WRITE TEST HERE!
    # 1. subscribe to group1, trigger group1
    # 2. subscribe to group2, trigger group2
    pass

@pytest.mark.asyncio
async def test_keepalive():
    """Test that server sends keepalive messages."""

    # Channels communicator to test WebSocket consumers.
    comm = ch_testing.WebsocketCommunicator(application=my_app,
                                            path='graphql-keepalive/',
                                            subprotocols='graphql-ws')

    print('Establish & initialize the connection.')
    await comm.connect(timeout=TIMEOUT)
    await comm.send_json_to({'type': 'connection_init', 'payload': ''})
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp['type'] == 'connection_ack'
    resp = await comm.receive_json_from(timeout=TIMEOUT)
    assert resp['type'] == 'ka', (
        'Keepalive message expected right after `connection_ack`!')

    print('Receive several keepalive messages.')
    pings = []
    for _ in range(3):
        pings.append(await comm.receive_json_from(timeout=TIMEOUT))
    assert all([ping['type'] == 'ka' for ping in pings])

    print('Send connection termination message.')
    await comm.send_json_to({'type': 'connection_terminate'})

    print('Disconnect and wait the application to finish gracefully.')
    await comm.disconnect(timeout=TIMEOUT)
    await comm.wait(timeout=TIMEOUT)

# ------------------------------------------------ GRAPHQL OVER WEBSOCKET SETUP


class MySubscription(Subscription):
    """Test GraphQL subscription."""

    VALUE = str(uuid.uuid4().hex)
    event = graphene.JSONString()

    class Arguments:
        """That is how subscription arguments are defined."""
        param1 = graphene.String()
        param2 = graphene.String()

    def subscribe(self, info, param1, param2):  # pylint: disable=unused-argument,arguments-differ
        """Specify subscription groups when client subscribes."""
        assert self is None, 'Root `self` expected to be `None`!'
        return [f'{param1}']

    def publish(self, info, param1, param2):  # pylint: disable=unused-argument
        """Publish query result to the subscribers."""

        event = {
            'value': MySubscription.VALUE,
            'sub_param1': param1,
            'sub_param2': param2,
            'payload': self,
        }

        return MySubscription(event=event)

    @classmethod
    def broadcast(cls, param, payload):  # pylint: disable=arguments-differ
        """Example of the `broadcast` classmethod usage."""
        super().broadcast(group=f'{param}', payload=payload)


class MySubscriptions(graphene.ObjectType):
    """GraphQL subscriptions."""
    my_sub = MySubscription.Field()


class Command(graphene.Enum):
    """Command to the mutation what to do."""
    BROADCAST = 0
    UNSUBSCRIBE = 1


class MyMutation(graphene.Mutation):
    """Test GraphQL mutation."""

    PAYLOAD = str(uuid.uuid4().hex)

    class Output(graphene.ObjectType):
        """Mutation result."""
        ok = graphene.Boolean()

    class Arguments:
        """That is how mutation arguments are defined."""
        cmd = Command()
        param = graphene.String()

    def mutate(self, info, cmd, param):  # pylint: disable=unused-argument
        """Do some operation on the subscription."""
        assert self is None, 'Root `self` expected to be `None`!'
        if Command.get(cmd) == Command.BROADCAST:
            MySubscription.broadcast(param=param, payload=MyMutation.PAYLOAD)
        elif Command.get(cmd) == Command.UNSUBSCRIBE:
            MySubscription.unsubscribe()

        return MyMutation.Output(ok=True)


class MyMutations(graphene.ObjectType):
    """GraphQL mutations."""
    my_mutation = MyMutation.Field()


class MyQuery(graphene.ObjectType):
    """Root GraphQL query."""

    VALUE = str(uuid.uuid4().hex)
    value = graphene.String(
        args={'issue_error': graphene.Boolean(default_value=False)}
    )

    def resolve_value(self, info, issue_error):  # pylint: disable=unused-argument
        """Resolver to return predefined value which can be tested."""
        assert self is None, 'Root `self` expected to be `None`!'
        if issue_error:
            raise RuntimeError(MyQuery.VALUE)
        return MyQuery.VALUE


my_schema = graphene.Schema(query=MyQuery, subscription=MySubscriptions,
                            mutation=MyMutations, auto_camelcase=False)


class MyGraphqlWsConsumer(GraphqlWsConsumer):
    """Channels WebSocket consumer which provides GraphQL API."""
    schema = my_schema


class MyGraphqlWsConsumerKeepalive(GraphqlWsConsumer):
    """Channels WebSocket consumer which provides GraphQL API."""
    schema = my_schema
    # Period to send keepalive mesages. Just some reasonable number.
    send_keepalive_every = 0.05


my_app = channels.routing.ProtocolTypeRouter({
    'websocket': channels.routing.URLRouter([
        django.urls.path('graphql/', MyGraphqlWsConsumer),
        django.urls.path('graphql-keepalive/', MyGraphqlWsConsumerKeepalive),
    ])
})
