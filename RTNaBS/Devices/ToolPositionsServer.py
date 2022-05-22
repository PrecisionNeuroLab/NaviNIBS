import asyncio
import attrs
import logging
import numpy as np
import typing as tp
import zmq
import zmq.asyncio as azmq

from RTNaBS.Devices import positionsServerHostname, positionsServerPort, TimestampedToolPosition
from RTNaBS.util import ZMQAsyncioFix

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@attrs.define
class ToolPositionsServer:
    """
    Base class for any tool positions server (to provide wrapper that is agnostic of connection type)
    """
    _hostname: str = positionsServerHostname
    _port: int = positionsServerPort

    _latestPositions: tp.Dict[str, tp.Optional[TimestampedToolPosition]] = attrs.field(init=False, factory=dict)
    _publishingLatestLock: asyncio.Condition = attrs.field(init=False, factory=asyncio.Condition)
    _publishPending: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    _pubSocket: azmq.Socket = attrs.field(init=False)

    def __attrs_post_init__(self):
        ctx = azmq.Context()
        self._pubSocket = ctx.socket(zmq.PUB)
        logger.debug('Binding {}:{} for pub socket'.format(self._hostname, self._port))
        self._pubSocket.bind('tcp://{}:{}'.format(self._hostname, self._port))
        self._pubSocket.linger = 0  # TODO: determine if necessary

        asyncio.create_task(self._publishLatestPositionsLoop())

    async def run(self):
        raise NotImplementedError()  # should be implemented by subclass

    async def _publishLatestPositionsLoop(self):
        while True:
            await self._publishPending.wait()
            async with self._publishingLatestLock:
                logger.debug('Publishing latest positions')
                self._pubSocket.send_json({key: (val.asDict() if val is not None else None) for key, val in self._latestPositions.items()})
                self._publishPending.clear()

    async def _recordNewPosition(self, key: str, position: TimestampedToolPosition):
        if key in self._latestPositions and self._latestPositions[key].time > position.time:
            logger.warning('New position appears to have been received out of order. Discarding.')
            return
        logger.debug('Received new position for {}: {}'.format(key, position))
        self._latestPositions[key] = position
        self._publishPending.set()

    @classmethod
    async def createAndRun_async(cls, *args, **kwargs):
        server = cls(*args, **kwargs)
        await server.run()

    @classmethod
    def createAndRun(cls, *args, **kwargs):
        from RTNaBS.util.Asyncio import asyncioRunAndHandleExceptions
        asyncioRunAndHandleExceptions(cls.createAndRun_async, *args, **kwargs)