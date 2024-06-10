import attrs
import numpy as np
import typing as tp

from NaviNIBS.util.numpy import attrsWithNumpyAsDict, attrsWithNumpyFromDict

positionsServerHostname = '127.0.0.1'
positionsServerPubPort = 18950
positionsServerCmdPort = 18951


@attrs.define
class TimestampedToolPosition:
    time: float
    transf: tp.Optional[np.ndarray]  # None indicates invalid / not tracked
    relativeTo: str = 'world'

    def asDict(self) -> tp.Dict[str, tp.Any]:
        d = attrsWithNumpyAsDict(self, npFields=('transf',))
        return d

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any]):
        return attrsWithNumpyFromDict(cls, d, npFields=('transf',))

