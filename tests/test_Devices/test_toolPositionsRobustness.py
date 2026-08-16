"""
Regression tests for robustness of the tool positions server/client pair:
- handling of None entries in the positions table (the publish format
  explicitly allows them);
- the client's receive loop surviving an exception raised by a subscriber
  to sigLatestPositionsChanged, instead of dying and freezing tracking.

The server runs in a separate thread with its own event loop because the
client makes synchronous ZMQ REQ calls (e.g. requestLatestPositions on
reconnect) that would deadlock if the server shared the test's event loop.
"""

import asyncio
import socket
import threading
import time

import numpy as np
import pytest

from NaviNIBS.Devices import TimestampedToolPosition
from NaviNIBS.Devices.ToolPositionsClient import ToolPositionsClient
from NaviNIBS.Devices.ToolPositionsServer import ToolPositionsServer


def _freePorts(n: int) -> list[int]:
    # hold all sockets open until every port is allocated, so the OS can't hand
    # a just-released port back for the next request in the same batch
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(('127.0.0.1', 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return ports


class _ServerThread:
    def __init__(self):
        self.pubPort, self.cmdPort = _freePorts(2)
        self.server: ToolPositionsServer | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        async def _main():
            self.server = ToolPositionsServer(
                hostname='127.0.0.1', pubPort=self.pubPort, cmdPort=self.cmdPort)
            self.loop = asyncio.get_running_loop()
            self._ready.set()
            while not self._stop.is_set():
                await asyncio.sleep(0.05)
        asyncio.run(_main())

    def start(self):
        self._thread.start()
        assert self._ready.wait(timeout=10), 'server thread failed to start'

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=10)

    def recordPosition(self, key: str, position: TimestampedToolPosition):
        fut = asyncio.run_coroutine_threadsafe(
            self.server.recordNewPosition(key, position), self.loop)
        fut.result(timeout=10)

    def injectNoneEntry(self, key: str):
        async def _inject():
            async with self.server._publishingLatestLock:
                self.server._latestPositions[key] = None
            self.server.publishLatestPositions()
        fut = asyncio.run_coroutine_threadsafe(_inject(), self.loop)
        fut.result(timeout=10)


async def _waitForTransf(client: ToolPositionsClient, key: str, predicate, timeoutSec: float = 5.):
    tEnd = time.time() + timeoutSec
    while time.time() < tEnd:
        transf = client.getLatestTransf(key, None)
        if predicate(transf):
            return transf
        await asyncio.sleep(0.1)
    return None


@pytest.mark.asyncio
async def test_serverRecordOverNoneEntry():
    pubPort, cmdPort = _freePorts(2)
    server = ToolPositionsServer(hostname='127.0.0.1',
                                 pubPort=pubPort, cmdPort=cmdPort)
    server._latestPositions['x'] = None

    await server.recordNewPosition(
        'x', TimestampedToolPosition(time=time.time(), transf=np.eye(4)))

    assert server._latestPositions['x'] is not None


@pytest.mark.asyncio
async def test_clientHandlesNoneEntries():
    st = _ServerThread()
    st.start()
    try:
        client = ToolPositionsClient(serverHostname='127.0.0.1',
                                     serverPubPort=st.pubPort, serverCmdPort=st.cmdPort)
        await asyncio.sleep(0.5)  # let SUB socket connect

        st.injectNoneEntry('tool1')
        await asyncio.sleep(0.5)
        assert 'tool1' in client.latestPositions, 'client never received the None entry'

        st.recordPosition('tool1', TimestampedToolPosition(time=time.time(), transf=np.eye(4)))
        transf = await _waitForTransf(client, 'tool1', lambda t: t is not None)
        assert transf is not None, \
            'client never saw the valid transform (receive loop likely died on None entry)'
        client.stopReceivingPositions()
    finally:
        st.stop()


@pytest.mark.asyncio
async def test_clientLoopSurvivesBadSubscriber():
    st = _ServerThread()
    st.start()
    try:
        client = ToolPositionsClient(serverHostname='127.0.0.1',
                                     serverPubPort=st.pubPort, serverCmdPort=st.cmdPort)
        await asyncio.sleep(0.5)

        raisedOnce = []

        def badSubscriber():
            if not raisedOnce:
                raisedOnce.append(True)
                raise RuntimeError('intentionally misbehaving subscriber')

        client.sigLatestPositionsChanged.connect(badSubscriber)

        transfA = np.eye(4)
        transfB = np.eye(4)
        transfB[0, 3] = 10.

        st.recordPosition('coil', TimestampedToolPosition(time=time.time(), transf=transfA))
        await asyncio.sleep(0.5)
        assert raisedOnce, 'bad subscriber was never invoked; test setup problem'

        st.recordPosition('coil', TimestampedToolPosition(time=time.time(), transf=transfB))
        transf = await _waitForTransf(
            client, 'coil', lambda t: t is not None and abs(t[0, 3] - 10.) < 1e-6)
        assert transf is not None, \
            'tracking frozen: receive loop died after subscriber exception'
        client.stopReceivingPositions()
    finally:
        st.stop()


@pytest.mark.asyncio
async def test_clientLoopsSurviveBadIsConnectedSubscriber():
    """
    sigIsConnectedChanged is emitted from inside both the receive loop and the
    monitor loop (via _updateIsConnected); a raising subscriber previously killed
    whichever loop performed the connected-state flip.
    """
    st = _ServerThread()
    st.start()
    try:
        client = ToolPositionsClient(serverHostname='127.0.0.1',
                                     serverPubPort=st.pubPort, serverCmdPort=st.cmdPort)

        raisedOnce = []

        def badSubscriber():
            if not raisedOnce:
                raisedOnce.append(True)
                raise RuntimeError('intentionally misbehaving isConnected subscriber')

        client.sigIsConnectedChanged.connect(badSubscriber)

        tEnd = time.time() + 10.
        while time.time() < tEnd and not client.isConnected:
            await asyncio.sleep(0.1)
        assert client.isConnected, 'client never connected; test setup problem'
        assert raisedOnce, 'bad subscriber was never invoked; test setup problem'

        await asyncio.sleep(0.5)
        assert not client._monitorTask.done(), \
            'monitor loop died after isConnected subscriber exception'
        assert not client._pollTask.done(), \
            'receive loop died after isConnected subscriber exception'

        st.recordPosition('coil', TimestampedToolPosition(time=time.time(), transf=np.eye(4)))
        transf = await _waitForTransf(client, 'coil', lambda t: t is not None)
        assert transf is not None, \
            'tracking frozen after isConnected subscriber exception'
        client.stopReceivingPositions()
    finally:
        st.stop()
