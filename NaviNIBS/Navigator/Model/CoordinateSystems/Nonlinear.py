from __future__ import annotations
import attrs
import logging
import nibabel as nib
import nitransforms as nit
import numpy as np
import pickle
import typing as tp

from NaviNIBS.Navigator.Model.CoordinateSystems.CoordinateSystem import CoordinateSystem

logger = logging.getLogger(__name__)




T = tp.TypeVar('T')


# define an empty placeholder
class Invalid:
    pass

invalid = Invalid()


@attrs.define(kw_only=True)
class NonlinearTransformedCoordinateSystem(CoordinateSystem):
    _deformationFieldThisToWorld_filepath: str = None
    _deformationFieldWorldToThis_filepath: str = None
    _isDeltas: bool = False

    _deformationFieldThisToWorld: nib.Nifti1Image | Invalid | None = attrs.field(init=False, default=None)
    _deformationFieldWorldToThis: nib.Nifti1Image | Invalid | None = attrs.field(init=False, default=None)

    _transfThisToWorld: nit.nonlinear.DenseFieldTransform | Invalid | None = attrs.field(init=False, default=None)
    _transfWorldToThis: nit.nonlinear.DenseFieldTransform | Invalid | None = attrs.field(init=False, default=None)

    _cachedValues: dict[bytes, tp.Any] = attrs.field(init=False, factory=dict, repr=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        self.sigItemChanged.connect(lambda *args, **kwargs: self.clearCache())

    def _maybeFillNaNs(self, img: nib.Nifti1Image, maxPercent: float = 5, fillValue: float = 0.) -> nib.Nifti1Image | Invalid:
        data = img.get_fdata()
        numNaNs = np.sum(np.isnan(data))
        totalVoxels = np.prod(data.shape)
        percentNaNs = (numNaNs / totalVoxels) * 100.

        if percentNaNs > 0:
            if percentNaNs > maxPercent:
                # too many NaNs, don't fill
                logger.info(f'{percentNaNs:.2f}% values in deformation field are NaN, not filling.')
                return invalid
            else:
                logger.info(f'Only {percentNaNs:.2f}% values in deformation field are NaN, filling with {fillValue}.')
                # fill inline
                newData = data.copy()
                newData[np.isnan(newData)] = fillValue
                newImg = nib.Nifti1Image(newData, img.affine, img.header)
                return newImg

        return img

    @property
    def deformationFieldThisToWorld(self):
        if self._deformationFieldThisToWorld is None:
            logger.info(f'Loading deformation field from {self._deformationFieldThisToWorld_filepath}')
            self._deformationFieldThisToWorld = nib.load(self._deformationFieldThisToWorld_filepath)
            if np.any(np.isnan(self._deformationFieldThisToWorld.get_fdata())):
                logger.warning(f'Deformation field loaded from {self._deformationFieldThisToWorld_filepath} contains NaN values, which will will break nonlinear transform map.')
                self._deformationFieldThisToWorld = self._maybeFillNaNs(self._deformationFieldThisToWorld)
        return self._deformationFieldThisToWorld

    @property
    def deformationFieldWorldToThis(self):
        if self._deformationFieldWorldToThis is None:
            logger.info(f'Loading deformation field from {self._deformationFieldWorldToThis_filepath}')
            self._deformationFieldWorldToThis = nib.load(self._deformationFieldWorldToThis_filepath)
            if np.any(np.isnan(self._deformationFieldWorldToThis.get_fdata())):
                logger.warning(f'Deformation field loaded from {self._deformationFieldWorldToThis_filepath} contains NaN values, which will break nonlinear transform map.')
                self._deformationFieldWorldToThis = self._maybeFillNaNs(self._deformationFieldWorldToThis)

        return self._deformationFieldWorldToThis

    @property
    def transfThisToWorld(self):
        if self._transfThisToWorld is None:
            if self.deformationFieldThisToWorld is invalid:
                self._transfThisToWorld = invalid
            else:
                self._transfThisToWorld = nit.nonlinear.DenseFieldTransform(
                    field=self.deformationFieldThisToWorld,
                    is_deltas=self._isDeltas)  # TODO: determine whether also need to specify reference arg
        return self._transfThisToWorld

    @property
    def transfWorldToThis(self):
        if self._transfWorldToThis is None:
            if self.deformationFieldWorldToThis is invalid:
                self._transfWorldToThis = invalid
            else:
                self._transfWorldToThis = nit.nonlinear.DenseFieldTransform(
                    field=self.deformationFieldWorldToThis,
                    is_deltas=self._isDeltas,
                )  # TODO: determine whether also need to specify reference arg
        return self._transfWorldToThis

    def _cacheWrap(self, fn: tp.Callable[..., T], **kwargs) -> T:
        # noinspection PyUnresolvedReferences
        key = pickle.dumps((fn.__name__, kwargs))

        if key in self._cachedValues:
            return self._cachedValues[key]
        else:
            val = fn(doUseCache=False, **kwargs)
            self._cachedValues[key] = val
            return val

    def clearCache(self):
        self._deformationFieldThisToWorld = None
        self._deformationFieldWorldToThis = None
        self._transfThisToWorld = None
        self._transfWorldToThis = None
        self._cachedValues = dict()

    def transformFromWorldToThis(self, coords: np.ndarray, doUseCache: bool = True) -> np.ndarray:
        if doUseCache:
            return self._cacheWrap(self.transformFromWorldToThis, coords=coords)

        if coords.ndim == 1:
            coords = coords[np.newaxis, :]
            shouldCollapse = True
        else:
            shouldCollapse = False

        if self.transfWorldToThis is not invalid:
            res = self.transfWorldToThis.map(coords)
        else:
            # original field had some NaNs. If we try to transform, nitransforms will silently pass through untransformed coords
            # instead, return NaNs here
            res = np.full(coords.shape, np.nan, dtype=np.float64)

        if shouldCollapse:
            res = res.flatten()

        return res

    def transformFromThisToWorld(self, coords: np.ndarray, doUseCache: bool = True) -> np.ndarray:
        if doUseCache:
            return self._cacheWrap(self.transformFromThisToWorld, coords=coords)

        if coords.ndim == 1:
            coords = coords[np.newaxis, :]
            shouldCollapse = True
        else:
            shouldCollapse = False

        if self.transfThisToWorld is not invalid:
            res = self.transfThisToWorld.map(coords)
        else:
            # original field had some NaNs. If we try to transform, nitransforms will silently pass through untransformed coords
            # instead, return NaNs here
            res = np.full(coords.shape, np.nan, dtype=np.float64)

        if shouldCollapse:
            res = res.flatten()

        return res


