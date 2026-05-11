from __future__ import annotations
import attrs
import logging
import numpy as np
import typing as tp

from NaviNIBS.Navigator.Model.CoordinateSystems.CoordinateSystem import CoordinateSystem
from NaviNIBS.util.Transforms import applyTransform, invertTransform

logger = logging.getLogger(__name__)


@attrs.define(kw_only=True)
class AffineTransformedCoordinateSystem(CoordinateSystem):
    _transfThisToWorld: tp.Optional[np.ndarray] = None
    _transfWorldToThis: tp.Optional[np.ndarray] = None

    __transfWorldToThis: tp.Optional[np.ndarray] = attrs.field(init=False, default=None)
    __transfThisToWorld: tp.Optional[np.ndarray] = attrs.field(init=False, default=None)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        self.sigItemChanged.connect(lambda *args, **kwargs: self.clearCache())

        assert (self._transfThisToWorld is None) != (self._transfWorldToThis is None),\
            'Must specify transfThisToWorld or transfWorldToThis'

    @property
    def transfThisToWorld(self):
        if self._transfThisToWorld is not None:
            return self._transfThisToWorld
        else:
            if self.__transfThisToWorld is None:
                self.__transfThisToWorld = invertTransform(self._transfWorldToThis)
            return self.__transfThisToWorld

    @property
    def transfWorldToThis(self):
        if self._transfWorldToThis is not None:
            return self._transfWorldToThis
        else:
            if self.__transfWorldToThis is None:
                self.__transfWorldToThis = invertTransform(self._transfThisToWorld)
            return self.__transfWorldToThis

    def clearCache(self):
        self.__transfThisToWorld = None
        self.__transfWorldToThis = None

    def transformFromWorldToThis(self, coords: np.ndarray) -> np.ndarray:
        return applyTransform(self.transfWorldToThis, coords, doCheck=False)

    def transformFromThisToWorld(self, coords: np.ndarray) -> np.ndarray:
        return applyTransform(self.transfThisToWorld, coords, doCheck=False)
