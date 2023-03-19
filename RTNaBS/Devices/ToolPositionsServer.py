import asyncio
import attrs
import logging
import numpy as np
import typing as tp
from typing import ClassVar
import zmq
import zmq.asyncio as azmq

from RTNaBS.Devices import positionsServerHostname, positionsServerPubPort, positionsServerCmdPort, TimestampedToolPosition
from RTNaBS.util import ZMQAsyncioFix
from RTNaBS.util.ZMQConnector import ZMQConnectorServer, logger as logger_ZMQConnector

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger_ZMQConnector.setLevel(logging.INFO)


@attrs.define
class ToolPositionsServer:
    """
    Base class for any tool positions server (to provide wrapper that is agnostic of connection type)
    """
    _type: ClassVar[str] = 'Generic'  # should be set by subclasses to be more informative

    _hostname: str = positionsServerHostname
    _pubPort: int = positionsServerPubPort
    _cmdPort: int = positionsServerCmdPort

    _latestPositions: tp.Dict[str, tp.Optional[TimestampedToolPosition]] = attrs.field(init=False, factory=dict)
    _publishingLatestLock: asyncio.Condition = attrs.field(init=False, factory=asyncio.Condition)
    _publishPending: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    _pubSocket: azmq.Socket = attrs.field(init=False)
    _connector: ZMQConnectorServer = attrs.field(init=False)

    def __attrs_post_init__(self):
        ctx = azmq.Context()
        self._pubSocket = ctx.socket(zmq.PUB)
        logger.debug('Binding {}:{} for pub socket'.format(self._hostname, self._pubPort))
        self._pubSocket.bind('tcp://{}:{}'.format(self._hostname, self._pubPort))
        self._pubSocket.linger = 0  # TODO: determine if necessary

        self._connector = ZMQConnectorServer(
            obj=self,
            reqRepPort=self._cmdPort,
            bindAddr=self._hostname
        )

        asyncio.create_task(self._publishLatestPositionsLoop())

    @property
    def type(self):
        return self._type

    async def run(self):
        raise NotImplementedError()  # should be implemented by subclass

    async def _publishLatestPositionsLoop(self):
        while True:
            await self._publishPending.wait()
            await asyncio.sleep(0.1)  # rate limit
            async with self._publishingLatestLock:
                logger.debug('Publishing latest positions')
                self._pubSocket.send_json({key: (val.asDict() if val is not None else None) for key, val in self._latestPositions.items()})
                self._publishPending.clear()

    async def recordNewPosition(self, key: str, position: TimestampedToolPosition | dict):
        if isinstance(position, dict):
            position = TimestampedToolPosition.fromDict(position)
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