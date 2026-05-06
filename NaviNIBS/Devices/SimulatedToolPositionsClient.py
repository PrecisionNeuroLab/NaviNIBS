import attrs
import logging
import typing as tp

from NaviNIBS.Devices import TimestampedToolPosition
from NaviNIBS.Devices.ToolPositionsClient import ToolPositionsClientBase


logger = logging.getLogger(__name__)


@attrs.define
class SimulatedToolPositionsClient(ToolPositionsClientBase):

    @property
    def isConnected(self) -> bool:
        return True

    async def recordNewPosition_async(self, key: str, position: TimestampedToolPosition):
        logger.debug(f'recordNewPosition {key} {position}')
        self._setPositionLocally(key, position)

    def recordNewPosition_sync(self, key: str, position: TimestampedToolPosition):
        logger.debug(f'recordNewPosition {key} {position}')
        self._setPositionLocally(key, position)

    def _setPositionLocally(self, key: str, position: tp.Optional[TimestampedToolPosition]):
        if self._latestPositions is None:
            self._latestPositions = {}
        self._latestPositions[key] = position
        self.sigLatestPositionsChanged.emit()
