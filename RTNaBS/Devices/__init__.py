import attrs
import numpy as np
import typing as tp

positionsServerHostname = '127.0.0.1'
positionsServerPort = 18950


@attrs.define
class TimestampedToolPosition:
    time: float
    transf: np.ndarray

    def asDict(self) -> tp.Dict[str, tp.Any]:
        return dict(time=self.time, transf=self.transf.tolist())

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any]):
        d['transf'] = np.asarray(d['transf'])
        return cls(**d)

