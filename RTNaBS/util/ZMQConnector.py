import attr
import asyncio
import json
import logging
from math import ceil
import socket
import typing as tp
import unittest
import zmq
import zmq.asyncio as azmq

from . import exceptionToStr
from . import ZMQAsyncioFix

logger = logging.getLogger(__name__)

class InvalidMessageError(Exception):
    pass


def _msgSizeInGB(msg: tp.List[bytes]) -> float:
    return sum(len(item) for item in msg) / 1.e9


@attr.s(auto_attribs=True)
class ZMQConnectorServer:
    _obj: tp.Any
    _reqRepPort: int
    _pubSubPort: tp.Optional[int] = None  # if not specified, will not be able to publish
    _bindAddr: str = '127.0.0.1'  # set to '*' to bind all interfaces
    _ctx: tp.Optional[azmq.Context] = attr.ib(factory=azmq.Context)

    _onOtherRequestReceived: tp.Optional[tp.Callable[[tp.List[bytes]], tp.List[bytes]]] = None

    _repSocket: tp.Optional[azmq.Socket] = attr.ib(init=False, default=None)
    _pubSocket: tp.Optional[azmq.Socket] = attr.ib(init=False, default=None)
    _asyncPollingTask: asyncio.Task = attr.ib(init=False)

    def __attrs_post_init__(self):
        logger.debug('Binding rep socket on port %d' % (self._reqRepPort,))
        self._repSocket = self._ctx.socket(zmq.REP)
        self._repSocket.bind('tcp://%s:%d' % (self._bindAddr, self._reqRepPort))

        if self._pubSubPort is not None:
            logger.debug('Binding pub socket on port %d' % (self._pubSubPort,))
            self._pubSocket = self._ctx.socket(zmq.PUB)
            self._pubSocket.linger = 0
            self._pubSocket.bind('tcp://%s:%d' % (self._bindAddr, self._pubSubPort))

        self._asyncPollingTask = asyncio.create_task(self._asyncPoll())

    def __del__(self):
        logger.debug("Deleting ZMQConnectorServer")
        self.close()

    def close(self):
        if self._repSocket is not None:
            self._repSocket.linger = 0
            self._repSocket.close(0)
            self._repSocket = None

        if self._pubSocket is not None:
            self._asyncPollingTask.cancel()
            self._pubSocket.linger = 0
            self._pubSocket.close(0)
            self._pubSocket = None

        if self._ctx is not None:
            self._ctx.term()
            self._ctx = None

    async def _processRequest(self, req: tp.List[bytes]) -> tp.List[bytes]:

        msgType = req[0]

        def encodeResponse(resp: tp.Any) -> bytes:
            try:
                return [json.dumps(resp).encode('utf-8')]
            except TypeError as e:
                logger.error(f'Problem serializing request response: {resp}')
                raise e

        logger.debug("Processing request of type %s" % msgType)
        try:
            if msgType == b'ping':
                if len(req) == 1:
                    logger.debug('Responding to ping with pong')
                    return [b'pong']
                else:
                    raise InvalidMessageError()

            elif msgType in (b'get', b'set', b'call', b'asyncCall'):
                assert len(req) == 2
                req = json.loads(req[1].decode('utf-8'))

                if not isinstance(req, list):
                    raise InvalidMessageError()

                if msgType == b'get':
                    whatToGet = req[0]
                    if not isinstance(whatToGet, list):
                        raise InvalidMessageError()
                    resp = []
                    for what in whatToGet:
                        val = getattr(self._obj, what)
                        logger.debug("Got %s = %s" % (what, val))
                        resp.append(val)
                    return encodeResponse(resp)

                elif msgType == b'set':
                    whatToSet = req[0]
                    if not isinstance(whatToSet,dict):
                        raise InvalidMessageError()
                    for what, val in whatToSet.items():
                        if not hasattr(self._obj, what):
                            logger.warning("ZMQConnector setting new attribute via request: %s = %s" % (what, val))
                        try:
                            setattr(self._obj, what, val)
                        except Exception as e:
                            logger.error('Failed to set %s to %s: %s' % (what, val, exceptionToStr(e)))
                            return encodeResponse(['__Error__', 'SetFailedError', exceptionToStr(e)])

                        logger.debug("Set %s = %s" % (what, val))
                    return encodeResponse('success')

                elif msgType in (b'call', b'asyncCall'):
                    whatToCall = req[0]
                    if not isinstance(whatToCall, dict):
                        raise InvalidMessageError()

                    try:
                        method = whatToCall['method']
                    except KeyError as e:
                        raise InvalidMessageError()

                    args = whatToCall.get('args', list())
                    kwargs = whatToCall.get('kwargs', dict())

                    if not isinstance(args, list):
                        raise InvalidMessageError()

                    if not isinstance(kwargs, dict):
                        raise InvalidMessageError()

                    toCall = getattr(self._obj, method)

                    if msgType == b'call':
                        ret = toCall(*args, **kwargs)
                    else:
                        ret = await toCall(*args, **kwargs)

                    return encodeResponse(['success', ret])
                else:
                    raise NotImplementedError()

            elif msgType in (b'getB', b'bSet'):
                if msgType == b'getB':
                    assert len(req) == 2
                    req = json.loads(req[1].decode('utf-8'))

                    if not isinstance(req, list):
                        raise InvalidMessageError()
                    whatToGet = req[0]
                    if not isinstance(whatToGet, list):
                        raise InvalidMessageError()
                    resp = []
                    assert len(whatToGet) == 1
                    val = getattr(self._obj, whatToGet[0])
                    assert isinstance(val, list)
                    for subval in val:
                        assert isinstance(subval, bytes)
                    logger.debug("Got %s = <list of bytes, size = %.3f GB>" % (whatToGet[0], _msgSizeInGB(val)))
                    return val
                elif msgType == b'bSet':
                    assert len(req) > 2
                    what = req[1].decode('utf-8')
                    val = req[2:]
                    try:
                        setattr(self._obj, what, val)
                    except Exception as e:
                        logger.error('Failed to set %s: %s' % (what, exceptionToStr(e)))
                        return encodeResponse(['__Error__', 'SetFailedError', exceptionToStr(e)])
                    logger.debug('Set %s = <list of bytes, size = %.3f GB>' % (what, _msgSizeInGB(val)))
                    return encodeResponse('success')
                else:
                    raise NotImplementedError()

            else:
                if self._onOtherRequestReceived is not None:
                    # allow custom message handlers
                    return self._onOtherRequestReceived(req)
                else:
                    raise InvalidMessageError()

        except InvalidMessageError as e:
            return encodeResponse(['__Error__', 'InvalidMessageError', exceptionToStr(e)])
        except AttributeError as e:
            return encodeResponse(['__Error__', 'AttributeError', exceptionToStr(e)])

    def publish(self, msg: tp.List[bytes]):
        assert self._pubSocket is not None
        self._pubSocket.send_multipart(msg)

    async def _asyncPoll(self):
        poller = azmq.Poller()
        poller.register(self._repSocket, zmq.POLLIN)
        while True:
            socks = dict(await poller.poll())
            if self._repSocket in socks:
                req = await self._repSocket.recv_multipart()
                try:
                    #logger.debug('Processing request')
                    resp = await self._processRequest(req)
                    #logger.debug('Done processing request')

                    await self._repSocket.send_multipart(resp)
                except Exception as e:
                    logger.error("Unhandled exception in ZMQConnectorServer:\n %s" % (exceptionToStr(e),))
                    await self._repSocket.send_multipart([json.dumps(
                        ['__Error__', 'CriticalFailure', exceptionToStr(e)]).encode('utf-8')])
                    #raise e


class RemoteError(Exception):
    pass


@attr.s(auto_attribs=True, cmp=False)
class ZMQConnectorClient:
    _reqRepPort: int
    _pubSubPort: tp.Optional[int] = None  # if not specified, will not be able to subscribe
    _connAddr: str = '127.0.0.1'
    _ctx: tp.Optional[zmq.Context] = attr.ib(factory=zmq.Context)
    _actx: tp.Optional[azmq.Context] = attr.ib(factory=azmq.Context)
    onMessagePublished: tp.Optional[tp.Callable[[tp.List[bytes]], None]] = None

    _reqSocket: zmq.Socket = attr.ib(init=False)
    _areqSocket: azmq.Socket = attr.ib(init=False)
    _areqLock: asyncio.Lock = attr.ib(init=False)
    _subSocket: tp.Optional[azmq.Socket] = attr.ib(init=False, default=None)

    _allowAsyncCalls: bool = False
    _allowSyncCalls: bool = True

    def __attrs_post_init__(self):
        self._connect()

    def __del__(self):
        logger.debug('Deleting ZMQConnectorClient')
        self.close()

    @property
    def connAddr(self):
        return self._connAddr

    def _connect(self):
        if self._allowSyncCalls:
            logger.debug('Initializing req socket')
            self._reqSocket = self._ctx.socket(zmq.REQ)
            logger.debug('Connecting req socket')
            self._reqSocket.connect('tcp://%s:%d' % (self._connAddr, self._reqRepPort))
        if self._allowAsyncCalls:
            logger.debug('Connecting async req socket')
            self._areqSocket = self._actx.socket(zmq.REQ)
            self._areqSocket.connect('tcp://%s:%d' % (self._connAddr, self._reqRepPort))
            self._areqLock = asyncio.Lock()

        if self._pubSubPort is not None:
            logger.debug('Setting up subscribe')
            logger.debug('Connecting sub socket')
            self._subSocket = self._actx.socket(zmq.SUB)
            self._subSocket.connect('tcp://%s:%d' % (self._connAddr, self._pubSubPort))
            self._subSocket.setsockopt(zmq.SUBSCRIBE, b'')

            asyncio.create_task(self._asyncPoll())  # only need to poll if checking for published updates
            logger.debug('Finished setting up subscribe')

    def close(self, linger=0):
        if self._allowSyncCalls and self._reqSocket is not None:
            self._reqSocket.close(linger)
            self._reqSocket = None
        if self._allowAsyncCalls and self._areqSocket is not None:
            self._areqSocket.close(linger)
            self._areqSocket = None
        if self._subSocket is not None:
            self._subSocket.close(linger)
            self._subSocket = None
        if self._ctx is not None:
            self._ctx.term()
            self._ctx = None
        if self._actx is not None:
            self._actx.term()
            self._actx = None

    async def _asyncPoll(self):
        poller = azmq.Poller()
        poller.register(self._subSocket, zmq.POLLIN)

        while True:
            #logger.debug('Polling for updates')
            socks = dict(await poller.poll())
            if self._subSocket in socks:
                rawMsg = await self._subSocket.recv_multipart()
                assert self.onMessagePublished is not None
                self.onMessagePublished(rawMsg)

    async def send_async(self, msg: tp.List[bytes]) -> tp.List[bytes]:
        assert self._allowAsyncCalls

        logger.debug('About to wait for areqLock')
        async with self._areqLock:
            logger.debug('Acquired areqLock')
            await self._areqSocket.send_multipart(msg)
            logger.debug('Waiting for areq response')
            resp = await self._areqSocket.recv_multipart()
            logger.debug('Received response')

        return resp

    def send(self, msg: tp.List[bytes]) -> tp.List[bytes]:
        # assume this message doesn't conflict with format of built-in messages and just send directly
        logger.debug('Sending msg')
        self._reqSocket.send_multipart(msg)
        resp = self._reqSocket.recv_multipart()
        logger.debug('Received response')
        return resp

    async def _sendTypePlusBody_receive_async(self, msgType: str, obj: tp.Any,
                                              doEncode: bool = True, doDecodeResponse: bool = True) -> tp.Any:
        # (normally could have been handled by socket.send_json and socket.recv_json
        #  but with some protocols using send_multipart and recv_multipart this
        #  is acting as wrapper)
        if doEncode:
            resp = await self.send_async([msgType.encode('utf-8'),
                                          json.dumps(obj).encode('utf-8')])
        else:
            assert isinstance(obj, list)
            for part in obj:
                assert isinstance(part, bytes)

            resp = await self.send_async([msgType.encode('utf-8')] + obj)

        if doDecodeResponse:
            if len(resp) != 1:
                raise InvalidMessageError('Unexpected message format received from remote')

            resp = json.loads(resp[0].decode('utf-8'))

        return resp

    def _sendTypePlusBody_receive(self, msgType: str, obj: tp.Any,
                                  doEncode: bool = True, doDecodeResponse: bool = True) -> tp.Any:
        # (normally could have been handled by socket.send_json and socket.recv_json
        #  but with some protocols using send_multipart and recv_multipart this
        #  is acting as wrapper)
        if doEncode:
            resp = self.send([msgType.encode('utf-8'),
                                            json.dumps(obj).encode('utf-8')])
        else:
            assert isinstance(obj, list)
            for part in obj:
                assert isinstance(part, bytes)

            resp = self.send([msgType.encode('utf-8')] + obj)

        if doDecodeResponse:
            if len(resp) != 1:
                raise InvalidMessageError('Unexpected message format received from remote')

            resp = json.loads(resp[0].decode('utf-8'))

        return resp

    async def get_async(self, item: str, raw: bool = False) -> tp.Any:
        assert isinstance(item, str)

        logger.debug('Sending get request for %s' % item)

        resp = await self._sendTypePlusBody_receive_async('get' if not raw else 'getB', [[item]], doDecodeResponse=not raw)

        logger.debug('Received response')

        if not raw:
            if isinstance(resp, list):
                if resp[0] == '__Error__':
                    if resp[1] == 'AttributeError':
                        raise AttributeError("Server could not get attribute '%s'" % item)
                    else:
                        raise RemoteError("Unhandled exception '%s' trying to get attribute '%s' from server.\n%s" % (
                            resp[1], item, resp[2]))

            if len(resp) != 1:
                logger.debug('problem')  # TODO: debug, delete
                raise InvalidMessageError()

            resp = resp[0]

            logger.debug("Got %s = %s" % (item, resp))
        else:
            logger.debug("Got %s = <list of bytes, size = %.2f GB>" % (item, _msgSizeInGB(resp)))

        return resp

    def get(self, item: str, raw: bool = False) -> tp.Any:
        assert isinstance(item, str)

        logger.debug('Sending get request for %s' % item)

        resp = self._sendTypePlusBody_receive('get' if not raw else 'getB', [[item]], doDecodeResponse=not raw)

        if not raw:
            if isinstance(resp, list):
                if resp[0] == '__Error__':
                    if resp[1] == 'AttributeError':
                        raise AttributeError("Server could not get attribute '%s'" % item)
                    else:
                        raise RemoteError("Unhandled exception '%s' trying to get attribute '%s' from server.\n%s" % (
                            resp[1], item, resp[2]))

            if len(resp) != 1:
                logger.debug('problem')  # TODO: debug, delete
                raise InvalidMessageError()

            resp = resp[0]

            logger.debug("Got %s = %s" % (item, resp))
        else:
            logger.debug('Got %s = <list of bytes with %d items>' % (item, len(resp)))

        return resp

    async def set_async(self, key, value, raw: bool = False):
        if not raw:
            resp = await self._sendTypePlusBody_receive_async('set', [{key: value}])
        else:
            assert isinstance(value, list)
            for subvalue in value:
                assert isinstance(subvalue, bytes)
            resp = await self._sendTypePlusBody_receive_async('bSet', [key.encode('utf-8')] + value, doEncode=False)

        if isinstance(resp, list):
            if resp[0] == '__Error__':
                raise RemoteError("Unhandled exception '%s' trying to set attribute '%s' on server" % (resp[1], key))

        if not isinstance(resp, str) or resp != 'success':
            raise RemoteError("Failed to set '%s' on server" % key)

        if not raw:
            logger.debug("Set %s = %s" % (key, value))
        else:
            logger.debug('Set %s = <list of bytes, size = %.3f GB' % (key, _msgSizeInGB(value)))

    def set(self, key, value, raw: bool = False):
        if not raw:
            resp = self._sendTypePlusBody_receive('set', [{key: value}])
        else:
            assert isinstance(value, list)
            for subvalue in value:
                assert isinstance(subvalue, bytes)
            resp = self._sendTypePlusBody_receive('bSet', [key.encode('utf-8')] + value, doEncode=False)

        if isinstance(resp, list):
            if resp[0] == '__Error__':
                raise RemoteError("Unhandled exception '%s' trying to set attribute '%s' on server:\n %s" % (resp[1], key, resp[2]))

        if not isinstance(resp, str) or resp != 'success':
            raise RemoteError("Failed to set '%s' on server" % key)

        if not raw:
            logger.debug("Set %s = %s" % (key, value))
        else:
            logger.debug('Set %s = <list of bytes, size = %.3f GB' % (key, _msgSizeInGB(value)))

    async def _call_async(self, method: str, doAsync: bool, *args, **kwargs):
        resp = await self._sendTypePlusBody_receive_async('asyncCall' if doAsync else 'call', [dict(
            method=method,
            args=args,
            kwargs=kwargs
        )])

        assert isinstance(resp, list)

        if resp[0] == '__Error__':
            raise RemoteError(
                "Unhandled exception '%s' trying to call method '%s' on server:\n%s" % (resp[1], method, resp[2]))

        if resp[0] != 'success':
            raise RemoteError("Failed to call '%s' on server" % method)

        return resp[1]

    def _call(self, method: str, doAsync: bool, *args, **kwargs):
        resp = self._sendTypePlusBody_receive('asyncCall' if doAsync else 'call', [dict(
            method=method,
            args=args,
            kwargs=kwargs
        )])

        assert isinstance(resp, list)

        if resp[0] == '__Error__':
            raise RemoteError(
                "Unhandled exception '%s' trying to call method '%s' on server:\n%s" % (resp[1], method, resp[2]))

        if resp[0] != 'success':
            raise RemoteError("Failed to call '%s' on server" % method)

        return resp[1]

    async def call_async(self, method: str, *args, **kwargs):
        """
        Asynchronously on the client, make a synchronous call on the server
        """
        return await self._call_async(method=method, doAsync=False, *args, **kwargs)

    def call(self, method: str, *args, **kwargs):
        return self._call(method=method, doAsync=False, *args, **kwargs)

    async def callAsync_async(self, method: str, *args, **kwargs):
        """"
        Asynchronously on the client, make an asynchronous call on the server
        """
        return await self._call_async(method=method, doAsync=True, *args, **kwargs)

    def callAsync(self, method: str, *args, **kwargs):
        return self._call(method=method, doAsync=True, *args, **kwargs)

    async def ping_async(self, timeout=100, numTries=2):
        """
        :param timeout: time in ms
        :param numTries:
        :return: None

        Raises TimeoutError if ping fails
        """
        for i in range(numTries):
            async with self._areqLock:
                await self._areqSocket.send_multipart([b'ping'])
                logger.debug("Sent ping")

                poll = azmq.Poller()
                poll.register(self._areqSocket, zmq.POLLIN)
                socks = dict(await poll.poll(timeout / numTries))
                if socks.get(self._areqSocket) == zmq.POLLIN:
                    resp = await self._areqSocket.recv_multipart()
                    assert len(resp) == 1 and resp[0] == b'pong'
                    logger.debug("Received pong")
                    return
                else:
                    logger.debug('Ping timed out, closing connection')
                    self._areqSocket.setsockopt(zmq.LINGER, 0)
                    self._areqSocket.close()
                    poll.unregister(self._areqSocket)

                    if self._allowSyncCalls:
                        self._reqSocket.setsockopt(zmq.LINGER, 0)
                        self._reqSocket.close()

                    if self._subSocket is not None:
                        self._subSocket.setsockopt(zmq.LINGER, 0)
                        self._subSocket.close()

                    self._connect()

        raise TimeoutError()

    def ping(self, timeout=100, numTries=2):
        for i in range(numTries):
            self._reqSocket.send_multipart([b'ping'])
            logger.debug("Sent ping")

            poll = zmq.Poller()
            poll.register(self._reqSocket, zmq.POLLIN)
            socks = dict(poll.poll(timeout / numTries))
            if socks.get(self._reqSocket) == zmq.POLLIN:
                resp = self._reqSocket.recv_multipart()
                assert len(resp) == 1 and resp[0] == b'pong'
                logger.debug("Received pong")
                return
            else:
                logger.debug('Ping timed out, closing connection')
                self._reqSocket.setsockopt(zmq.LINGER, 0)
                self._reqSocket.close()
                poll.unregister(self._reqSocket)

                if self._allowAsyncCalls:
                    self._areqSocket.setsockopt(zmq.LINGER, 0)
                    self._areqSocket.close()

                if self._subSocket is not None:
                    self._subSocket.setsockopt(zmq.LINGER, 0)
                    self._subSocket.close()

                self._connect()

        raise TimeoutError()


def checkIfPortAvailable(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        logger.debug('Created test socket on port %d' % (port,))  # TODO: debug, delete
        s.settimeout(0.01)
        return s.connect_ex(('localhost', port)) != 0


usedPorts = set()

def getNewPort(minAssignedPort: int = 5000) -> int:
    global usedPorts
    port = minAssignedPort

    # Note: this only checks that localhost can bind to the port. If wanting to get a port
    #  for a remote machine (i.e. not localhost) to bind to, this does not ensure availability.
    logger.debug('Checking to make sure port is available')
    while True:
        if port in usedPorts or not checkIfPortAvailable(port):
            # mark port as in use so we don't keep trying it in the future
            logger.debug('Port %d is already in use. Skipping.' % (port,))
            usedPorts.add(port)
            port += 1
            continue
        else:
            # found available port
            logger.debug('Found available port: %d' % (port,))
            # assume it will be used
            usedPorts.add(port)
            return port


class Test_ZMQConnector(unittest.TestCase):
    class state:
        a: int
        b: str
        c: tp.List[bytes]

        async def respondAfterDelay(self, delay: float = 1., toReturn=True):
            await asyncio.sleep(delay)
            return toReturn

    def _test_basic_sync(self, client):
        clientState = Test_ZMQConnector.state()
        clientState.a = client.get('a')
        clientState.b = client.get('b')
        clientState.c = 3
        client.set('c', clientState.c)
        return clientState

    async def _test_basic(self, doAsync):

        reqRepPort = 7897

        serverState = Test_ZMQConnector.state()
        serverState.a = 1
        serverState.b = 'two'
        addrKey = 'testZMQConnector'
        server = ZMQConnectorServer(obj=serverState, reqRepPort=reqRepPort)
        await asyncio.sleep(1)
        logger.debug("Finished initializing server")
        client = ZMQConnectorClient(reqRepPort=reqRepPort, allowAsyncCalls=doAsync)

        await asyncio.sleep(1)

        logger.debug("Getting client state")
        if doAsync:
            clientState = Test_ZMQConnector.state()
            clientState.a = await client.get_async('a')
            clientState.b = await client.get_async('b')
            clientState.c = 3
            await client.set_async('c', clientState.c)
        else:
            # for testing, wrap blocking non-async call in async call here so that server keeps running at the same time
            # (in practice, the server would already be running in a separate thread)
            loop = asyncio.get_running_loop()
            clientState = await loop.run_in_executor(None, lambda: self._test_basic_sync(client))

        await asyncio.sleep(1)

        self.assertEqual(serverState.a, clientState.a)
        self.assertEqual(serverState.b, clientState.b)
        self.assertEqual(serverState.c, clientState.c)

        server.close() # needed for successive tests
        del server
        await asyncio.sleep(1)

    async def _test_overlapping(self):
        """
        When client allows async, there's a chance we would send overlapping requests, with
          the second request starting before the first response is received. Make sure this
          works as expected.
        """

        reqRepPort = 7898

        serverState = Test_ZMQConnector.state()
        serverState.a = 1
        serverState.b = 'two'
        addrKey = 'testZMQConnector'
        server = ZMQConnectorServer(obj=serverState, reqRepPort=reqRepPort)
        await asyncio.sleep(1)
        logger.debug("Finished initializing server")
        client = ZMQConnectorClient(reqRepPort=reqRepPort, allowAsyncCalls=True)

        await asyncio.sleep(1)

        clientState = Test_ZMQConnector.state()
        clientState.c = 3

        futures: tp.List[asyncio.Future] = list()
        futures.append(client.callAsync_async('respondAfterDelay', delay=5., toReturn='call1'))
        futures.append(client.callAsync_async('respondAfterDelay', delay=2., toReturn='call2'))
        futures.append(client.get_async('a'))
        futures.append(client.get_async('b'))
        futures.append(client.set_async('c', clientState.c))

        logger.debug('Awaiting results from overlapping calls')
        (resp1, resp2, clientState.a, clientState.b, shouldBeNone) = await asyncio.gather(*futures)
        logger.debug('Results received')

        self.assertEqual(resp1, 'call1')
        self.assertEqual(resp2, 'call2')
        self.assertEqual(serverState.a, clientState.a)
        self.assertEqual(serverState.b, clientState.b)
        self.assertEqual(serverState.c, clientState.c)

        server.close()  # needed for successive tests
        del server
        await asyncio.sleep(1)

    def _test_large_sync(self, client, clientState):
        logger.debug('Client getting large state from server')
        clientState.c = client.get('c', raw=True)
        logger.debug('Done getting large state')
        client.set('d', clientState.d, raw=True)
        return clientState

    async def _test_large(self, doAsync):
        import os
        reqRepPort = 7899
        logger.debug('Initializing server state')
        serverState = Test_ZMQConnector.state()
        serverState.c = [os.urandom(2**27)]

        logger.debug('Initializing server connector')
        server = ZMQConnectorServer(obj=serverState, reqRepPort=reqRepPort)
        await asyncio.sleep(1)

        logger.debug('Initializing client connector')
        client = ZMQConnectorClient(reqRepPort=reqRepPort, allowAsyncCalls=doAsync)
        await asyncio.sleep(1)

        clientState = Test_ZMQConnector.state()
        clientState.d = [os.urandom(2 ** 27)]

        if doAsync:
            logger.debug('Client getting large state from server')
            clientState.c = await client.get_async('c', raw=True)
            logger.debug('Done getting large state')
            await client.set_async('d', clientState.d, raw=True)
        else:
            loop = asyncio.get_running_loop()
            clientState = await loop.run_in_executor(None, lambda: self._test_large_sync(client, clientState))

        self.assertEqual(serverState.c, clientState.c)

        self.assertEqual(serverState.d[0], clientState.d[0])

        server.close()
        client.close()

    def test_large_async(self):
        asyncio.get_event_loop().run_until_complete(self._test_large(doAsync=True))

    def test_large_sync(self):
        asyncio.get_event_loop().run_until_complete(self._test_large(doAsync=False))

    def test_basic_async(self):
        asyncio.get_event_loop().run_until_complete(self._test_basic(doAsync=True))

    def test_basic_sync(self):
        asyncio.get_event_loop().run_until_complete(self._test_basic(doAsync=False))

    def test_overlapping_async(self):
        asyncio.get_event_loop().run_until_complete(self._test_overlapping())



