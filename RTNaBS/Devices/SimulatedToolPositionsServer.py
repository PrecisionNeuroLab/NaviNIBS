import asyncio
import attrs
import logging
import numpy as np
import time
import typing as tp
from typing import ClassVar

from RTNaBS.Devices.ToolPositionsServer import ToolPositionsServer, TimestampedToolPosition
from RTNaBS.util.ZMQConnector import ZMQConnectorServer, ZMQConnectorClient


logger = logging.getLogger(__name__)


@attrs.define
class SimulatedToolPositionsServer(ToolPositionsServer):
    _type: ClassVar[str] = 'Simulated'

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    async def run(self):
        while True:
            await asyncio.sleep(10.)

    async def setNewPosition(self, key: str, transf: tp.Optional[tp.Union[list[list[float, ...], ...], np.ndarray]]):
        if isinstance(transf, list):
            transf = np.asarray(transf)
        if transf is not None:
            assert transf.shape == (4, 4)
        pos = TimestampedToolPosition(time=time.time(), transf=transf)
        logger.info(f'Setting new simulated position for {key}: {pos}')
        await self._recordNewPosition(key=key, position=pos)


if __name__ == '__main__':
    async def createAndRun_async():
        cls = SimulatedToolPositionsServer()
        await cls.run()

    from RTNaBS.util.Asyncio import asyncioRunAndHandleExceptions
    asyncioRunAndHandleExceptions(createAndRun_async)