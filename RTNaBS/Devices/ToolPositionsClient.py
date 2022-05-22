import asyncio
import attrs
import logging
import numpy as np
import typing as tp
import zmq
import zmq.asyncio as azmq

from RTNaBS.Devices import positionsServerHostname, positionsServerPort, TimestampedToolPosition
from RTNaBS.util import ZMQAsyncioFix
from RTNaBS.util.Signaler import Signal


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@attrs.define
class ToolPositionsClient:
    _serverHostname: str = positionsServerHostname
    _serverPort: int = positionsServerPort

    _latestPositions: tp.Dict[str, tp.Optional[TimestampedToolPosition]] = attrs.field(init=False, factory=dict)

    _subSocket: azmq.Socket = attrs.field(init=False)

    sigLatestPositionsChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        ctx = azmq.Context()
        self._subSocket = ctx.socket(zmq.SUB)
        logger.debug('Connecting {}:{}'.format(self._serverHostname, self._serverPort))
        self._subSocket.connect('tcp://{}:{}'.format(self._serverHostname, self._serverPort))
        self._subSocket.setsockopt(zmq.SUBSCRIBE, b'')

        asyncio.create_task(self._receiveLatestPositionsLoop())

    @property
    def latestPositions(self):
        return self._latestPositions

    async def _receiveLatestPositionsLoop(self):
        poller = azmq.Poller()
        poller.register(self._subSocket, zmq.POLLIN)
        while True:
            socks = dict(await poller.poll())
            if self._subSocket in socks:
                hasPendingMessages = True
                while hasPendingMessages:
                    msg = await self._subSocket.recv_json()
                    logger.debug('Received published message: {}'.format(msg))
                    self._latestPositions = {key: (TimestampedToolPosition.fromDict(val) if val is not None else None) for key, val in msg.items()}
                    hasPendingMessages = (await self._subSocket.poll(timeout=0.)) > 0
                logger.debug('Signaling change in latest positions')
                self.sigLatestPositionsChanged.emit()  # only emit for latest in series of updates to avoid falling behind

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
