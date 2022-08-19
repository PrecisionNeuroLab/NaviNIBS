import attrs
import numpy as np
import typing as tp

positionsServerHostname = '127.0.0.1'
positionsServerPubPort = 18950
positionsServerCmdPort = 18951

@attrs.define
class TimestampedToolPosition:
    time: float
    transf: tp.Optional[np.ndarray]  # None indicates invalid / not tracked

    def asDict(self) -> tp.Dict[str, tp.Any]:
        return dict(time=self.time, transf=self.transf.tolist() if self.transf is not None else None)

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any]):
        if d['transf'] is not None:
            d['transf'] = np.asarray(d['transf'])
        return cls(**d)

