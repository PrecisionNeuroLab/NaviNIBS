import asyncio
import attrs
import logging
import numpy as np
import typing as tp
import zmq
import zmq.asyncio as azmq

from RTNaBS.Devices.ToolPositionsClient import ToolPositionsClient


logger = logging.getLogger(__name__)


@attrs.define
class SimulatedToolPositionsClient(ToolPositionsClient):
    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        serverType = self._connector.get('type')
        if serverType != 'Simulated':
            raise RuntimeError('Tried to use SimulatedToolPositionsClient to connect to non-simulated ToolPositionsServer')

    async def setNewPosition_async(self, key: str, transf: tp.Optional[np.ndarray]):
        return await self._connector.callAsync_async('setNewPosition', key=key, transf=transf.tolist() if transf is not None else None)

    def setNewPosition(self, key: str, transf: tp.Optional[np.ndarray]):
        return self._connector.callAsync('setNewPosition', key=key, transf=transf.tolist() if transf is not None else None)