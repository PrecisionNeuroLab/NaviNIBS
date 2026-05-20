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
import pyvista as pv
from scipy.spatial import cKDTree

from NaviNIBS.Navigator.Model.CoordinateSystems.CoordinateSystem import CoordinateSystem
from NaviNIBS.util.pyvista.dataset import find_closest_cell

logger = logging.getLogger(__name__)


T = tp.TypeVar('T')


@attrs.define(kw_only=True)
class SBMTransformedCoordinateSystem(CoordinateSystem):

    _isVisible: bool = False  # slow on first access (~1.5GB fsaverage download); user opts in via the visibility dialog
    _nativePial_filepaths: tuple[str, str]
    _nativeSphere_filepaths: tuple[str, str]

    _warnIfNearestPialDistanceExceeds_mm: float = 5.0
    _interpolationMethod: tp.Literal['knn', 'barycentric'] = 'barycentric'
    _smoothingK: int = 3  # only used when _interpolationMethod == 'knn'

    _cachedValues: dict[bytes, tp.Any] = attrs.field(init=False, factory=dict, repr=False)
    _atlasPialPolysCache: dict[str, pv.PolyData] | None = attrs.field(init=False, default=None, repr=False)
    _atlasSpherePolysCache: dict[str, pv.PolyData] | None = attrs.field(init=False, default=None, repr=False)
    _nativePialPolysCache: dict[str, pv.PolyData] | None = attrs.field(init=False, default=None, repr=False)
    _nativeSpherePolysCache: dict[str, pv.PolyData] | None = attrs.field(init=False, default=None, repr=False)

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
        coords, faces = nib.freesurfer.read_geometry(os.path.join(fsAverageDir, 'surf', f'{lr}h.sphere'))
        return np.asarray(coords, dtype=np.float64), np.asarray(faces, dtype=np.int64)

    @staticmethod
    def _loadNativeSurface(filepath: str) -> tuple[np.ndarray, np.ndarray]:
        if filepath.endswith('.gii'):
            surf = nib.load(filepath)
            coords = np.asarray(surf.darrays[0].data, dtype=np.float64)
            faces = np.asarray(surf.darrays[1].data, dtype=np.int64)
            return coords, faces
        coords, faces = nib.freesurfer.read_geometry(filepath)
        return np.asarray(coords, dtype=np.float64), np.asarray(faces, dtype=np.int64)

    @staticmethod
    @functools.cache
    def _getNativePialSurf(filepath: str) -> tuple[np.ndarray, np.ndarray]:
        logger.info(f'Loading native pial surface from {filepath}')
        if filepath.endswith('.gii'):
            surf = nib.load(filepath)
            coords = np.asarray(surf.darrays[0].data, dtype=np.float64)
            faces = np.asarray(surf.darrays[1].data, dtype=np.int64)
            return coords, faces
        coords, faces, info = nib.freesurfer.read_geometry(filepath, read_metadata=True)
        # convert FreeSurfer tkr-RAS to scanner-RAS by adding cras translation
        cras = info.get('cras')
        if cras is not None:
            coords = coords + np.asarray(cras, dtype=coords.dtype)
        return np.asarray(coords, dtype=np.float64), np.asarray(faces, dtype=np.int64)

    @staticmethod
    @functools.cache
    def _getNativeSphereSurfScaled(filepath: str, lr: tp.Literal['l', 'r']) -> tuple[np.ndarray, np.ndarray]:
        logger.info(f'Loading native sphere surface from {filepath}')
        coords, faces = SBMTransformedCoordinateSystem._loadNativeSurface(filepath)
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
        return coords * scale, faces

    @staticmethod
    @functools.cache
    def _getNativeSphereTree(filepath: str, lr: tp.Literal['l', 'r']) -> cKDTree:
        return cKDTree(SBMTransformedCoordinateSystem._getNativeSphereSurfScaled(filepath, lr)[0])

    @classmethod
    @functools.cache
    def _getAtlasSphereTree(cls, lr: tp.Literal['l', 'r']) -> cKDTree:
        return cKDTree(cls._getAtlasSphereSurf(lr)[0])

    @staticmethod
    @functools.cache
    def _getCombinedNativePialTree(lhFilepath: str, rhFilepath: str) -> tuple[cKDTree, int]:
        lh, _ = SBMTransformedCoordinateSystem._getNativePialSurf(lhFilepath)
        rh, _ = SBMTransformedCoordinateSystem._getNativePialSurf(rhFilepath)
        return cKDTree(np.vstack([lh, rh])), len(lh)

    @classmethod
    @functools.cache
    def _getCombinedAtlasPialTree(cls) -> tuple[cKDTree, int]:
        lh = cls._getAtlasPialSurf('l')[0]
        rh = cls._getAtlasPialSurf('r')[0]
        return cKDTree(np.vstack([lh, rh])), len(lh)

    @staticmethod
    def _facesToPyvistaFaces(faces: np.ndarray) -> np.ndarray:
        n = len(faces)
        return np.hstack([np.full((n, 1), 3, dtype=np.int64), faces.astype(np.int64)]).ravel()

    @property
    def _atlasPialPolys(self) -> dict[str, pv.PolyData]:
        if self._atlasPialPolysCache is None:
            self._atlasPialPolysCache = {
                lr: pv.PolyData(c, self._facesToPyvistaFaces(f))
                for lr in ('l', 'r')
                for c, f in [self._getAtlasPialSurf(lr)]
            }
        return self._atlasPialPolysCache

    @property
    def _atlasSpherePolys(self) -> dict[str, pv.PolyData]:
        if self._atlasSpherePolysCache is None:
            self._atlasSpherePolysCache = {
                lr: pv.PolyData(c, self._facesToPyvistaFaces(f))
                for lr in ('l', 'r')
                for c, f in [self._getAtlasSphereSurf(lr)]
            }
        return self._atlasSpherePolysCache

    @property
    def _nativePialPolys(self) -> dict[str, pv.PolyData]:
        if self._nativePialPolysCache is None:
            lhPath, rhPath = self._nativePial_filepaths
            self._nativePialPolysCache = {
                lr: pv.PolyData(c, self._facesToPyvistaFaces(f))
                for lr, path in (('l', lhPath), ('r', rhPath))
                for c, f in [self._getNativePialSurf(path)]
            }
        return self._nativePialPolysCache

    @property
    def _nativeSpherePolys(self) -> dict[str, pv.PolyData]:
        if self._nativeSpherePolysCache is None:
            lhPath, rhPath = self._nativeSphere_filepaths
            self._nativeSpherePolysCache = {
                lr: pv.PolyData(c, self._facesToPyvistaFaces(f))
                for lr, path in (('l', lhPath), ('r', rhPath))
                for c, f in [self._getNativeSphereSurfScaled(path, lr)]
            }
        return self._nativeSpherePolysCache

    def _cacheWrap(self, fn: tp.Callable[..., T], **kwargs) -> T:
        key = pickle.dumps((fn.__name__, kwargs))
        if key in self._cachedValues:
            return self._cachedValues[key]
        val = fn(doUseCache=False, **kwargs)
        self._cachedValues[key] = val
        return val

    def clearCache(self):
        self._cachedValues = dict()
        self._atlasPialPolysCache = None
        self._atlasSpherePolysCache = None
        self._nativePialPolysCache = None
        self._nativeSpherePolysCache = None

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

    @staticmethod
    def _computeBarycentric(p: np.ndarray, v0: np.ndarray, v1: np.ndarray, v2: np.ndarray
                            ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Barycentric coords (u, v, w) of point p (projected to triangle plane) in triangle (v0, v1, v2).
        Returns u, v, w such that p ≈ u*v0 + v*v1 + w*v2. Broadcasts over the leading axis."""
        e1 = v1 - v0
        e2 = v2 - v0
        d = p - v0
        d00 = (e1 * e1).sum(-1)
        d01 = (e1 * e2).sum(-1)
        d11 = (e2 * e2).sum(-1)
        d20 = (d * e1).sum(-1)
        d21 = (d * e2).sum(-1)
        denom = d00 * d11 - d01 * d01
        v = (d11 * d20 - d01 * d21) / denom
        w = (d00 * d21 - d01 * d20) / denom
        u = 1.0 - v - w
        return u, v, w

    def _mapViaBarycentric(self,
                            coords: np.ndarray,
                            *,
                            sourcePialPolys: dict[str, pv.PolyData],
                            sourcePialFaces: dict[str, np.ndarray],
                            sourceSphereCoords: dict[str, np.ndarray],
                            destSpherePolys: dict[str, pv.PolyData],
                            destSphereFaces: dict[str, np.ndarray],
                            destPialCoords: dict[str, np.ndarray],
                            direction: str) -> np.ndarray:
        # Step 1: query each hemisphere's source pial for closest cell + closest point
        cells = {}
        closestPts = {}
        dists = {}
        for lr in ('l', 'r'):
            cId, cPt = find_closest_cell(sourcePialPolys[lr], coords, return_closest_point=True)
            cells[lr] = np.atleast_1d(cId)
            closestPts[lr] = np.atleast_2d(cPt)
            dists[lr] = np.linalg.norm(closestPts[lr] - coords, axis=-1)

        # Step 2: pick hemisphere by min distance
        lhMask = dists['l'] <= dists['r']

        # Warn if any point is too far from either pial surface
        minDists = np.minimum(dists['l'], dists['r'])
        self._warnIfFarFromPial(minDists, direction)

        out = np.empty((coords.shape[0], 3), dtype=np.float64)
        for lr, mask in (('l', lhMask), ('r', ~lhMask)):
            if not mask.any():
                continue

            # Step 3: barycentric coords in source pial triangle
            srcFaces = sourcePialFaces[lr]  # (N, 3)
            srcSphere = sourceSphereCoords[lr]
            destFaces = destSphereFaces[lr]  # (M, 3)
            destPial = destPialCoords[lr]

            subCells = cells[lr][mask]
            subClosest = closestPts[lr][mask]
            faceIdx = srcFaces[subCells]  # (n, 3)
            srcPialPts = sourcePialPolys[lr].points
            us, vs, ws = self._computeBarycentric(
                subClosest,
                srcPialPts[faceIdx[:, 0]],
                srcPialPts[faceIdx[:, 1]],
                srcPialPts[faceIdx[:, 2]],
            )

            # Step 4: transfer barycentric to source sphere via shared connectivity
            srcSpherePts = (us[:, None] * srcSphere[faceIdx[:, 0]]
                            + vs[:, None] * srcSphere[faceIdx[:, 1]]
                            + ws[:, None] * srcSphere[faceIdx[:, 2]])

            # Step 5: find closest cell on dest sphere
            dstCellIds, dstClosestPts = find_closest_cell(
                destSpherePolys[lr], srcSpherePts, return_closest_point=True)
            dstCellIds = np.atleast_1d(dstCellIds)
            dstClosestPts = np.atleast_2d(dstClosestPts)
            dstFaceIdx = destFaces[dstCellIds]  # (n, 3)
            dstSpherePts = destSpherePolys[lr].points

            # Step 6: barycentric on dest sphere triangle
            ud, vd, wd = self._computeBarycentric(
                dstClosestPts,
                dstSpherePts[dstFaceIdx[:, 0]],
                dstSpherePts[dstFaceIdx[:, 1]],
                dstSpherePts[dstFaceIdx[:, 2]],
            )

            # Step 7: apply to dest pial vertices of same face
            out[mask] = (ud[:, None] * destPial[dstFaceIdx[:, 0]]
                         + vd[:, None] * destPial[dstFaceIdx[:, 1]]
                         + wd[:, None] * destPial[dstFaceIdx[:, 2]])

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

        if self._interpolationMethod == 'knn':
            nativePialTree, nLhNativePial = self._getCombinedNativePialTree(lhPialPath, rhPialPath)
            res = self._mapViaSurface(
                coords=coords,
                sourcePialTree=nativePialTree,
                nLhSourcePial=nLhNativePial,
                lhSourceSphere=self._getNativeSphereSurfScaled(lhSpherePath, 'l')[0],
                rhSourceSphere=self._getNativeSphereSurfScaled(rhSpherePath, 'r')[0],
                lhDestSphereTree=self._getAtlasSphereTree('l'),
                rhDestSphereTree=self._getAtlasSphereTree('r'),
                lhDestPial=self._getAtlasPialSurf('l')[0],
                rhDestPial=self._getAtlasPialSurf('r')[0],
                direction='worldToThis',
            )
        elif self._interpolationMethod == 'barycentric':
            res = self._mapViaBarycentric(
                coords=coords,
                sourcePialPolys=self._nativePialPolys,
                sourcePialFaces={lr: self._getNativePialSurf(p)[1]
                                 for lr, p in (('l', lhPialPath), ('r', rhPialPath))},
                sourceSphereCoords={lr: self._getNativeSphereSurfScaled(p, lr)[0]
                                    for lr, p in (('l', lhSpherePath), ('r', rhSpherePath))},
                destSpherePolys=self._atlasSpherePolys,
                destSphereFaces={lr: self._getAtlasSphereSurf(lr)[1] for lr in ('l', 'r')},
                destPialCoords={lr: self._getAtlasPialSurf(lr)[0] for lr in ('l', 'r')},
                direction='worldToThis',
            )
        else:
            raise NotImplementedError(f'Unsupported interpolation method: {self._interpolationMethod!r}')

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

        if self._interpolationMethod == 'knn':
            atlasPialTree, nLhAtlasPial = self._getCombinedAtlasPialTree()
            res = self._mapViaSurface(
                coords=coords,
                sourcePialTree=atlasPialTree,
                nLhSourcePial=nLhAtlasPial,
                lhSourceSphere=self._getAtlasSphereSurf('l')[0],
                rhSourceSphere=self._getAtlasSphereSurf('r')[0],
                lhDestSphereTree=self._getNativeSphereTree(lhSpherePath, 'l'),
                rhDestSphereTree=self._getNativeSphereTree(rhSpherePath, 'r'),
                lhDestPial=self._getNativePialSurf(lhPialPath)[0],
                rhDestPial=self._getNativePialSurf(rhPialPath)[0],
                direction='thisToWorld',
            )
        elif self._interpolationMethod == 'barycentric':
            res = self._mapViaBarycentric(
                coords=coords,
                sourcePialPolys=self._atlasPialPolys,
                sourcePialFaces={lr: self._getAtlasPialSurf(lr)[1] for lr in ('l', 'r')},
                sourceSphereCoords={lr: self._getAtlasSphereSurf(lr)[0] for lr in ('l', 'r')},
                destSpherePolys=self._nativeSpherePolys,
                destSphereFaces={lr: self._getNativeSphereSurfScaled(p, lr)[1]
                                 for lr, p in (('l', lhSpherePath), ('r', rhSpherePath))},
                destPialCoords={lr: self._getNativePialSurf(p)[0]
                                for lr, p in (('l', lhPialPath), ('r', rhPialPath))},
                direction='thisToWorld',
            )
        else:
            raise NotImplementedError(f'Unsupported interpolation method: {self._interpolationMethod!r}')

        if shouldCollapse:
            res = res.flatten()
        return res
