from __future__ import annotations

import functools
import logging
import os
import pickle
import typing as tp

import attrs
import nibabel as nib
import nitransforms as nit
import numpy as np
from scipy.spatial import cKDTree

from NaviNIBS.Navigator.Model.CoordinateSystems.CoordinateSystem import CoordinateSystem

logger = logging.getLogger(__name__)


T = tp.TypeVar('T')


@attrs.define(kw_only=True)
class SBMTransformedCoordinateSystem(CoordinateSystem):

    _isVisible: bool = False  # slow on first access (~1.5GB fsaverage download); user opts in via the visibility dialog
    _nativePial_filepaths: tuple[str, str]
    _nativeSphere_filepaths: tuple[str, str]

    _warnIfNearestPialDistanceExceeds_mm: float = 5.0
    _smoothingK: int = 3

    _cachedValues: dict[bytes, tp.Any] = attrs.field(init=False, factory=dict, repr=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        self.sigItemChanged.connect(lambda *args, **kwargs: self.clearCache())

    @staticmethod
    @functools.cache
    def _getFSAverageDir() -> str:
        import platformdirs
        mneSubjectsDir = os.path.join(platformdirs.user_data_dir(appname='NaviNIBS', appauthor=False), 'Parcellations')
        fsAverageDir = os.path.join(mneSubjectsDir, 'fsaverage')
        if not os.path.exists(fsAverageDir):
            logger.info('Downloading FSAverage data')
            if not os.path.exists(mneSubjectsDir):
                os.makedirs(mneSubjectsDir)
            from mne.datasets import fetch_fsaverage
            fetch_fsaverage(
                subjects_dir=mneSubjectsDir,
                verbose=False
            )
            assert os.path.exists(fsAverageDir)

        return fsAverageDir

    @classmethod
    @functools.cache
    def _getAtlasPialSurf(cls, lr: tp.Literal['l', 'r']) -> tuple[np.ndarray, np.ndarray]:
        fsAverageDir = cls._getFSAverageDir()
        coords, faces, info = nib.freesurfer.read_geometry(
            os.path.join(fsAverageDir, 'surf', f'{lr}h.pial'), read_metadata=True)
        # convert FreeSurfer tkr-RAS to scanner-RAS by adding cras translation
        cras = info.get('cras')
        if cras is not None:
            coords = coords + np.asarray(cras, dtype=coords.dtype)
        return coords, faces

    @classmethod
    @functools.cache
    def _getAtlasSphereSurf(cls, lr: tp.Literal['l', 'r']) -> tuple[np.ndarray, np.ndarray]:
        fsAverageDir = cls._getFSAverageDir()
        fsSphere = nib.freesurfer.read_geometry(os.path.join(fsAverageDir, 'surf', f'{lr}h.sphere'))
        return fsSphere

    @staticmethod
    def _loadNativeSurfaceCoords(filepath: str) -> np.ndarray:
        if filepath.endswith('.gii'):
            surf = nib.load(filepath)
            return np.asarray(surf.darrays[0].data, dtype=np.float64)
        coords, _ = nib.freesurfer.read_geometry(filepath)
        return np.asarray(coords, dtype=np.float64)

    @staticmethod
    @functools.cache
    def _getNativePialCoords(filepath: str) -> np.ndarray:
        logger.info(f'Loading native pial surface from {filepath}')
        if filepath.endswith('.gii'):
            surf = nib.load(filepath)
            return np.asarray(surf.darrays[0].data, dtype=np.float64)
        coords, _, info = nib.freesurfer.read_geometry(filepath, read_metadata=True)
        # convert FreeSurfer tkr-RAS to scanner-RAS by adding cras translation
        cras = info.get('cras')
        if cras is not None:
            coords = coords + np.asarray(cras, dtype=coords.dtype)
        return np.asarray(coords, dtype=np.float64)

    @staticmethod
    @functools.cache
    def _getNativeSphereCoordsScaled(filepath: str, lr: tp.Literal['l', 'r']) -> np.ndarray:
        logger.info(f'Loading native sphere surface from {filepath}')
        coords = SBMTransformedCoordinateSystem._loadNativeSurfaceCoords(filepath)
        atlasCoords, _ = SBMTransformedCoordinateSystem._getAtlasSphereSurf(lr)
        nativeR = float(np.linalg.norm(coords[0]))
        atlasR = float(np.linalg.norm(atlasCoords[0]))
        scale = atlasR / nativeR
        if not (abs(scale - 1.0) < 1e-3 or abs(scale - 100.0) < 1.0):
            logger.warning(
                f'Unexpected native-sphere scale factor {scale:.4f} '
                f'(nativeR={nativeR:.4f}, atlasR={atlasR:.4f}); '
                f'expected ~1 (FreeSurfer-binary) or ~100 (CHARM .gii).'
            )
        return coords * scale

    @staticmethod
    @functools.cache
    def _getNativeSphereTree(filepath: str, lr: tp.Literal['l', 'r']) -> cKDTree:
        return cKDTree(SBMTransformedCoordinateSystem._getNativeSphereCoordsScaled(filepath, lr))

    @classmethod
    @functools.cache
    def _getAtlasSphereTree(cls, lr: tp.Literal['l', 'r']) -> cKDTree:
        return cKDTree(cls._getAtlasSphereSurf(lr)[0])

    @staticmethod
    @functools.cache
    def _getCombinedNativePialTree(lhFilepath: str, rhFilepath: str) -> tuple[cKDTree, int]:
        lh = SBMTransformedCoordinateSystem._getNativePialCoords(lhFilepath)
        rh = SBMTransformedCoordinateSystem._getNativePialCoords(rhFilepath)
        return cKDTree(np.vstack([lh, rh])), len(lh)

    @classmethod
    @functools.cache
    def _getCombinedAtlasPialTree(cls) -> tuple[cKDTree, int]:
        lh = cls._getAtlasPialSurf('l')[0]
        rh = cls._getAtlasPialSurf('r')[0]
        return cKDTree(np.vstack([lh, rh])), len(lh)

    def _cacheWrap(self, fn: tp.Callable[..., T], **kwargs) -> T:
        key = pickle.dumps((fn.__name__, kwargs))
        if key in self._cachedValues:
            return self._cachedValues[key]
        val = fn(doUseCache=False, **kwargs)
        self._cachedValues[key] = val
        return val

    def clearCache(self):
        self._cachedValues = dict()

    def _warnIfFarFromPial(self, dists: np.ndarray, direction: str) -> None:
        threshold = self._warnIfNearestPialDistanceExceeds_mm
        nearestDists = dists if dists.ndim == 1 else dists[:, 0]
        exceededMask = nearestDists > threshold
        numExceeded = int(np.count_nonzero(exceededMask))
        if numExceeded > 0:
            maxDist = float(np.max(nearestDists))
            logger.warning(
                f'SBM {direction}: {numExceeded}/{len(nearestDists)} input '
                f'point(s) >{threshold} mm from nearest pial vertex '
                f'(max={maxDist:.2f} mm).'
            )

    def _mapViaSurface(self,
                       coords: np.ndarray,
                       *,
                       sourcePialTree: cKDTree,
                       nLhSourcePial: int,
                       lhSourceSphere: np.ndarray,
                       rhSourceSphere: np.ndarray,
                       lhDestSphereTree: cKDTree,
                       rhDestSphereTree: cKDTree,
                       lhDestPial: np.ndarray,
                       rhDestPial: np.ndarray,
                       direction: str) -> np.ndarray:
        K = self._smoothingK
        dists1, idx1 = sourcePialTree.query(coords, k=K, workers=-1)
        self._warnIfFarFromPial(dists1, direction)

        if K == 1:
            lhMask = idx1 < nLhSourcePial
        else:
            lhMask = (idx1 < nLhSourcePial).sum(axis=1) > (K // 2)

        out = np.empty((coords.shape[0], 3), dtype=np.float64)
        for isLh, mask in ((True, lhMask), (False, ~lhMask)):
            if not mask.any():
                continue
            srcSphere = lhSourceSphere if isLh else rhSourceSphere
            destTree = lhDestSphereTree if isLh else rhDestSphereTree
            destPial = lhDestPial if isLh else rhDestPial

            sub_idx1 = idx1[mask]
            sub_d1 = dists1[mask]
            local_idx1 = sub_idx1 if isLh else (sub_idx1 - nLhSourcePial)

            if K > 1:
                valid = (sub_idx1 < nLhSourcePial) if isLh else (sub_idx1 >= nLhSourcePial)
                sub_d1 = np.where(valid, sub_d1, np.inf)
                local_idx1 = np.where(valid, local_idx1, 0)

            if K == 1:
                spherePts = srcSphere[local_idx1]
                _, idx2 = destTree.query(spherePts, workers=-1)
                out[mask] = destPial[idx2]
            else:
                w1 = 1.0 / (sub_d1 + 1e-9)
                w1 /= w1.sum(axis=1, keepdims=True)
                spherePts = (w1[..., None] * srcSphere[local_idx1]).sum(axis=1)
                d2, idx2 = destTree.query(spherePts, k=K, workers=-1)
                w2 = 1.0 / (d2 + 1e-9)
                w2 /= w2.sum(axis=1, keepdims=True)
                out[mask] = (w2[..., None] * destPial[idx2]).sum(axis=1)

        return out

    def transformFromWorldToThis(self, coords: np.ndarray, doUseCache: bool = True) -> np.ndarray:
        if doUseCache:
            return self._cacheWrap(self.transformFromWorldToThis, coords=coords)

        if coords.ndim == 1:
            coords = coords[np.newaxis, :]
            shouldCollapse = True
        else:
            shouldCollapse = False

        lhPialPath, rhPialPath = self._nativePial_filepaths
        lhSpherePath, rhSpherePath = self._nativeSphere_filepaths
        nativePialTree, nLhNativePial = self._getCombinedNativePialTree(lhPialPath, rhPialPath)

        res = self._mapViaSurface(
            coords=coords,
            sourcePialTree=nativePialTree,
            nLhSourcePial=nLhNativePial,
            lhSourceSphere=self._getNativeSphereCoordsScaled(lhSpherePath, 'l'),
            rhSourceSphere=self._getNativeSphereCoordsScaled(rhSpherePath, 'r'),
            lhDestSphereTree=self._getAtlasSphereTree('l'),
            rhDestSphereTree=self._getAtlasSphereTree('r'),
            lhDestPial=self._getAtlasPialSurf('l')[0],
            rhDestPial=self._getAtlasPialSurf('r')[0],
            direction='worldToThis',
        )

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

        lhPialPath, rhPialPath = self._nativePial_filepaths
        lhSpherePath, rhSpherePath = self._nativeSphere_filepaths
        atlasPialTree, nLhAtlasPial = self._getCombinedAtlasPialTree()

        res = self._mapViaSurface(
            coords=coords,
            sourcePialTree=atlasPialTree,
            nLhSourcePial=nLhAtlasPial,
            lhSourceSphere=self._getAtlasSphereSurf('l')[0],
            rhSourceSphere=self._getAtlasSphereSurf('r')[0],
            lhDestSphereTree=self._getNativeSphereTree(lhSpherePath, 'l'),
            rhDestSphereTree=self._getNativeSphereTree(rhSpherePath, 'r'),
            lhDestPial=self._getNativePialCoords(lhPialPath),
            rhDestPial=self._getNativePialCoords(rhPialPath),
            direction='thisToWorld',
        )

        if shouldCollapse:
            res = res.flatten()
        return res
