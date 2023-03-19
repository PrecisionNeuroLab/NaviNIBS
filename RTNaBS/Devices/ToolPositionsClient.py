import asyncio
import attrs
import logging
import numpy as np
import time
import typing as tp
import zmq
import zmq.asyncio as azmq

from RTNaBS.Devices import positionsServerHostname, positionsServerPubPort, positionsServerCmdPort, TimestampedToolPosition
from RTNaBS.util import ZMQAsyncioFix
from RTNaBS.util.ZMQConnector import ZMQConnectorClient, logger as logger_ZMQConnector
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import concatenateTransforms
from RTNaBS.util import exceptionToStr


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger_ZMQConnector.setLevel(logging.INFO)


_novalue = object()


@attrs.define
class ToolPositionsClient:
    _serverHostname: str = positionsServerHostname
    _serverPubPort: int = positionsServerPubPort
    _serverCmdPort: int = positionsServerCmdPort

    _latestPositions: tp.Dict[str, tp.Optional[TimestampedToolPosition]] = attrs.field(init=False, factory=dict)

    _subSocket: azmq.Socket = attrs.field(init=False)
    _connector: ZMQConnectorClient = attrs.field(init=False)

    sigLatestPositionsChanged: Signal = attrs.field(init=False, factory=Signal)

    _timeLastHeardFromServer: tp.Optional[float] = None
    _isConnected: bool = False
    _serverStatusTimeout: float = 10.  # consider server offline if we haven't heard from it for this long

    _pollTask: tp.Optional[asyncio.Task] = attrs.field(init=False)
    _monitorTask: tp.Optional[asyncio.Task] = attrs.field(init=False)

    sigIsConnectedChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        ctx = azmq.Context()
        self._subSocket = ctx.socket(zmq.SUB)
        logger.debug('Connecting {}:{}'.format(self._serverHostname, self._serverPubPort))
        self._subSocket.setsockopt(zmq.CONFLATE, 1)
        self._subSocket.connect('tcp://{}:{}'.format(self._serverHostname, self._serverPubPort))
        self._subSocket.setsockopt(zmq.SUBSCRIBE, b'')

        self._connector = ZMQConnectorClient(reqRepPort=self._serverCmdPort,
                                             connAddr=self._serverHostname,
                                             allowAsyncCalls=True)

        self._pollTask = asyncio.create_task(self._receiveLatestPositionsLoop())

        self._monitorTask = asyncio.create_task(self._monitorServerStatus())

    @property
    def latestPositions(self):
        """
        Note that returned positions may be absolute (rel to world) or relative, based on pos.relativeTo
        """
        return self._latestPositions

    def getServerType(self) -> str:
        return self._connector.get('type')

    def stopReceivingPositions(self):
        if self._pollTask is not None:
            self._pollTask.cancel()
            self._pollTask = None

    def getLatestTransf(self, key: str, default: tp.Any = _novalue) -> tp.Optional[np.ndarray]:
        """
        Note that returned transf is always absolute, even if the underlying latest position was relative
        """
        tsPos = self.latestPositions.get(key, None)
        if tsPos is None or tsPos.transf is None:
            if default is _novalue:
                raise KeyError('No matching, valid transf found')
            else:
                return default
        if tsPos.relativeTo != 'world':
            # convert relative transform to world transform
            otherTransf = self.getLatestTransf(key=tsPos.relativeTo)
            if otherTransf is _novalue:
                return default
            else:
                return concatenateTransforms((tsPos.transf, otherTransf))
        return tsPos.transf

    async def recordNewPosition(self, key: str, position: TimestampedToolPosition):
        """
        This should only be used to record positions of tools that are not tracked by the camera
        (e.g. when a position is reported by some other external system)
        """
        await self._connector.callAsync_async('recordNewPosition',
                                              key=key,
                                              position=position.asDict())

    async def _receiveLatestPositionsLoop(self):
        poller = azmq.Poller()
        poller.register(self._subSocket, zmq.POLLIN)
        while True:
            socks = dict(await poller.poll())
            if self._subSocket in socks:
                hasPendingMessages = True
                msg = None
                while hasPendingMessages:
                    msg = await self._subSocket.recv_json()
                    logger.debug('Received published message')
                    #hasPendingMessages = (await self._subSocket.poll(timeout=0.)) > 0
                    hasPendingMessages = False

                self._timeLastHeardFromServer = time.time()
                self._updateIsConnected()

                self._latestPositions = {key: (TimestampedToolPosition.fromDict(val) if val is not None else None) for key, val in msg.items()}
                logger.debug('Signaling change in latest positions')
                try:
                    self.sigLatestPositionsChanged.emit()  # only emit for latest in series of updates to avoid falling behind
                except Exception as e:
                    logger.error('Exception during position update:\n {}'.format(exceptionToStr(e)))
                    raise e

    async def _monitorServerStatus(self):
        while True:
            if self._timeLastHeardFromServer is None or (time.time() - self._timeLastHeardFromServer) > self._serverStatusTimeout / 2:
                self._timeLastHeardFromServer = None
                try:
                    await self._connector.ping_async(timeout=self._serverStatusTimeout * 1000. / 2)
                except TimeoutError:
                     logger.debug('Ping to tool positions server timed out.')
                else:
                    self._timeLastHeardFromServer = time.time()
                self._updateIsConnected()
            await asyncio.sleep(self._serverStatusTimeout / 4)

    def _updateIsConnected(self):
        isConnected = self._timeLastHeardFromServer is not None
        if isConnected != self._isConnected:
            self._isConnected = isConnected
            self.sigIsConnectedChanged.emit()

    @property
    def isConnected(self):
        return self._timeLastHeardFromServer is not None

    @classmethod
    async def createAndRun_async(cls, *args, **kwargs):
        server = cls(*args, **kwargs)
        while True:
            await asyncio.sleep(1.)

    @classmethod
    def createAndRun(cls, *args, **kwargs):
        from RTNaBS.util.Asyncio import asyncioRunAndHandleExceptions
        asyncioRunAndHandleExceptions(cls.createAndRun_async, *args, **kwargs)


if __name__ == '__main__':
    ToolPositionsClient.createAndRun()
