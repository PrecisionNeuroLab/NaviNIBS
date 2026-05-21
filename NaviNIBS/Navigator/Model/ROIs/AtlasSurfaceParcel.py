from __future__ import annotations

import functools
import logging
import os
import typing as tp
from typing import ClassVar

import attrs
import numpy as np

from NaviNIBS.Navigator.Model.GenericCollection import collectionDictItemAttrSetter
from NaviNIBS.Navigator.Model.ROIs import SurfaceMeshROI, ROIs
if tp.TYPE_CHECKING:
    from NaviNIBS.Navigator.Model.Session import Session


logger = logging.getLogger(__name__)


@attrs.define(eq=False, kw_only=True)
class AtlasSurfaceParcel(SurfaceMeshROI):
    type: ClassVar[str] = 'AtlasSurfaceParcel'
    _atlasKey: str
    _hemisphere: tp.Literal['l', 'r'] | None = None
    _parcelKey: str | None = None  # if not specified, self.key will be used instead
    _warpSource: tp.Literal['simnibs', 'freesurfer'] = 'simnibs'

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @property
    def meshVertexIndices(self):
        if self._meshVertexIndices is None:
            self.reload()
        return super().meshVertexIndices

    @meshVertexIndices.setter
    def meshVertexIndices(self, value):
        SurfaceMeshROI.meshVertexIndices.fset(self, value)

    @property
    def atlasKey(self):
        return self._atlasKey

    @atlasKey.setter
    @collectionDictItemAttrSetter(extraAttrsToSignalOnChange=['meshVertexIndices'])
    def atlasKey(self, value: str):
        self._meshVertexIndices = None

    @property
    def hemisphere(self):
        return self._hemisphere

    @hemisphere.setter
    @collectionDictItemAttrSetter(extraAttrsToSignalOnChange=['meshVertexIndices'])
    def hemisphere(self, value: tp.Literal['l', 'r'] | None):
        self._meshVertexIndices = None

    @property
    def parcelKey(self):
        return self._parcelKey

    @parcelKey.setter
    @collectionDictItemAttrSetter(extraAttrsToSignalOnChange=['meshVertexIndices'])
    def parcelKey(self, value: str | None):
        self._meshVertexIndices = None

    @property
    def warpSource(self):
        return self._warpSource

    @warpSource.setter
    @collectionDictItemAttrSetter(extraAttrsToSignalOnChange=['meshVertexIndices'])
    def warpSource(self, value: tp.Literal['simnibs', 'freesurfer']):
        self._meshVertexIndices = None

    @staticmethod
    @functools.cache
    def _getSubSphereRegCoords(filepath: str) -> np.ndarray:
        """Load sphere-registration vertex coords, rescaled to fsaverage radius (~100mm).

        SimNIBS sphere.reg.gii uses unit-sphere coords (-1..1); FreeSurfer sphere.reg binary
        is already at fsaverage scale. Auto-detect from radius and scale up if needed.
        """
        import nibabel as nib
        if filepath.endswith('.gii'):
            coords = nib.load(filepath).darrays[0].data
        else:
            coords, _ = nib.freesurfer.read_geometry(filepath)
        coords = np.asarray(coords, dtype=np.float64)
        nativeR = float(np.linalg.norm(coords[0]))
        if nativeR < 10:  # unit-sphere coords from SimNIBS .gii
            coords = coords * 100.0
        return coords

    @staticmethod
    @functools.cache
    def _getPialCoords(filepath: str) -> np.ndarray:
        import nibabel as nib
        if filepath.endswith('.gii'):
            return np.asarray(nib.load(filepath).darrays[0].data, dtype=np.float64)
        # FreeSurfer binary: apply CRAS translation to bring tkr-RAS into scanner-RAS
        coords, _, info = nib.freesurfer.read_geometry(filepath, read_metadata=True)
        coords = np.asarray(coords, dtype=np.float64)
        cras = info.get('cras')
        if cras is not None:
            coords = coords + np.asarray(cras, dtype=coords.dtype)
        return coords

    @staticmethod
    @functools.cache
    def _getNearestFsIndices(atlasKey: str, hemisphere: str, sphereFilepath: str) -> np.ndarray:
        """
        For each subject sphere.reg vertex, return the index of its nearest fsaverage
        sphere vertex. Cached per atlas+sphere so the expensive KDTree query is shared
        across all parcels loaded from the same atlas for the same subject.
        """
        from scipy.spatial import cKDTree
        fsSphere, _ = AtlasSurfaceParcel._prepareAtlas(atlasKey=atlasKey, hemisphere=hemisphere)
        subSphereRegCoords = AtlasSurfaceParcel._getSubSphereRegCoords(filepath=sphereFilepath)
        fsSphereCoordsTree = cKDTree(fsSphere[0])
        _, nearestFsIndices = fsSphereCoordsTree.query(subSphereRegCoords, workers=-1)
        return nearestFsIndices

    def _resolveSurfPaths(self, lr: str) -> tuple[str, str]:
        """Return (spherePath, pialPath) for the given hemisphere, per current warpSource."""
        hm = self.session.headModel
        if self._warpSource == 'simnibs':
            return (os.path.join(hm.m2mDir, 'surfaces', f'{lr}h.sphere.reg.gii'),
                    os.path.join(hm.m2mDir, 'surfaces', f'{lr}h.pial.gii'))
        assert hm.freesurferFilepath is not None, \
            'warpSource="freesurfer" requires HeadModel.freesurferFilepath to be set'
        spherePath = hm.getFreesurferSubfilePath(os.path.join('surf', f'{lr}h.sphere.reg'))
        pialPath = hm.getFreesurferSubfilePath(os.path.join('surf', f'{lr}h.pial.T1'))
        assert spherePath is not None and os.path.exists(spherePath), f'Missing FreeSurfer sphere.reg for {lr}h'
        assert pialPath is not None and os.path.exists(pialPath), f'Missing FreeSurfer pial.T1 for {lr}h'
        return spherePath, pialPath

    def reload(self):
        logger.info(f'Reloading AtlasSurfaceParcel {self._parcelKey} from {self._hemisphere}h.{self._atlasKey}')
        from scipy.spatial import cKDTree

        from NaviNIBS.Navigator.Model.HeadModel import MshVersion

        assert len(self._atlasKey) > 0

        assert self._hemisphere is not None, 'Support for not specifying hemisphere not yet implemented'

        _, fsParcels = self._prepareAtlas(atlasKey=self._atlasKey, hemisphere=self._hemisphere)

        if self._warpSource == 'simnibs':
            assert self.session.headModel.mshVersion == MshVersion.CHARM, \
                'SimNIBS sphere registration currently only supported with CHARM head models'

        for parcelKeySuffix in ['', '_ROI']:
            try:
                parcelIndex = fsParcels[2].index((self._parcelKey + parcelKeySuffix).encode())
            except ValueError:
                # no match found
                parcelIndex = None
            else:
                # match found
                break

        if parcelIndex is None:
            raise KeyError(f'No parcel found with label {self._parcelKey} in {self._hemisphere} hemisphere of {self._atlasKey}')

        spherePath, pialPath = self._resolveSurfPaths(self._hemisphere)
        otherHemisphere = 'lr'['rl'.index(self._hemisphere)]
        _, otherPialPath = self._resolveSurfPaths(otherHemisphere)

        # For each sphere.reg vertex, find its nearest fsaverage sphere vertex and check
        # whether that vertex carries the target parcel label. Reversing the search direction
        # (pial→atlas rather than atlas→pial) ensures every pial vertex is unambiguously
        # assigned to a parcel with no interior gaps.
        # _getNearestFsIndices is cached per atlas+sphere, so this is only computed once
        # when loading multiple parcels from the same atlas.
        logger.debug('Getting nearest fsaverage indices for registered sphere vertices')
        nearestFsIndices = self._getNearestFsIndices(
            atlasKey=self._atlasKey, hemisphere=self._hemisphere,
            sphereFilepath=spherePath)
        logger.debug('Finding pial vertices belonging to target parcel')
        pialVertexIndices = np.where(fsParcels[0][nearestFsIndices] == parcelIndex)[0]

        # Validate mesh key is a GM surface (compatible with the pial-based registration)
        assert self.meshKey is not None and self.meshKey.startswith('gm'), \
            f'AtlasSurfaceParcel requires a GM mesh key (e.g. gmSurf, gmSimpleSurf), got {self.meshKey!r}'

        # Map head model mesh vertices → parcel membership via the pial surface.
        # sphere.reg and pial share the same vertex topology, so pialVertexIndices
        # index into pial coordinates directly.
        # We reverse the search direction (head mesh → pial) to correctly handle cases
        # where the head mesh has lower resolution than the pial surface.
        pialCoords = self._getPialCoords(filepath=pialPath)
        otherPialCoords = self._getPialCoords(filepath=otherPialPath)
        allPialCoords = np.vstack((pialCoords, otherPialCoords))
        pialTree = cKDTree(allPialCoords)

        logger.debug('Querying nearest pial vertices for head mesh vertices')
        headMesh = getattr(self.session.headModel, self.meshKey)
        maxDistanceSeparation = 5  # in mm
        _, nearestPialIndices = pialTree.query(headMesh.points,
                                               workers=-1,
                                               distance_upper_bound=maxDistanceSeparation)

        logger.debug('Finding head mesh vertices whose nearest pial vertex belongs to target parcel')
        parcelPialMask = np.zeros(len(allPialCoords)+1, dtype=bool)
        parcelPialMask[pialVertexIndices] = True
        headMeshVertexIndices = np.where(parcelPialMask[nearestPialIndices])[0]

        logger.debug('Setting mesh vertex indices')
        self.meshVertexIndices = headMeshVertexIndices

        # Set seedCoord to the vertex within the ROI closest to the ROI centroid
        if len(headMeshVertexIndices) > 0:
            roiVertexCoords = headMesh.points[headMeshVertexIndices]
            centroid = roiVertexCoords.mean(axis=0)
            distances = np.linalg.norm(roiVertexCoords - centroid, axis=1)
            closestIdx = np.argmin(distances)
            self.seedCoord = tuple(float(v) for v in roiVertexCoords[closestIdx])

        logger.debug('done')

    def asDict(self) -> dict[str, tp.Any]:
        d = super().asDict()
        if 'meshVertexIndices' in d:
            # don't save full vertex list and instead reload from original
            # atlas (to avoid very large file sizes in json session configs)
            d.pop('meshVertexIndices')
        return d

    @staticmethod
    @functools.cache
    def _prepareAtlas(atlasKey: str, hemisphere: tp.Literal['l', 'r']) -> \
            tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray, list[bytes]]]:
        import platformdirs
        mneSubjectsDir = os.path.join(platformdirs.user_data_dir(appname='NaviNIBS', appauthor=False), 'Parcellations')
        match atlasKey:
            case 'HCPMMP1' | 'HCPMMP1_combined':
                parcellationPaths = [os.path.join(mneSubjectsDir, 'fsaverage', 'label',
                                                  lrh + '.HCPMMP1.annot') for lrh in ('lh', 'rh')]

                if any(not os.path.exists(p) for p in parcellationPaths):
                    logger.info(f'Downloading HCP MMP parcellation')
                    if not os.path.exists(mneSubjectsDir):
                        os.makedirs(mneSubjectsDir)
                    from mne.datasets import fetch_fsaverage, fetch_hcp_mmp_parcellation
                    fetch_fsaverage(
                        subjects_dir=mneSubjectsDir,
                        verbose=False)
                    fetch_hcp_mmp_parcellation(
                        subjects_dir=mneSubjectsDir,
                        combine=True,
                    )

            case _:
                raise NotImplementedError

        import nibabel as nib

        fsSphere: tuple[np.ndarray, np.ndarray] = nib.freesurfer.read_geometry(os.path.join(
            mneSubjectsDir, 'fsaverage', 'surf', hemisphere + 'h.sphere'))

        fsParcels: tuple[np.ndarray, np.ndarray, list[bytes]] = nib.freesurfer.read_annot(os.path.join(
            mneSubjectsDir, 'fsaverage', 'label', f'{hemisphere}h.{atlasKey}.annot'))

        return fsSphere, fsParcels

    @staticmethod
    def _decodeParcelLabels(parcelLabels: list[bytes], hemisphere: str) -> tuple[list[str], list[str]]:
        parcelKeys = []
        niceParcelLabels = []
        for iL, byteLabel in enumerate(parcelLabels):
            parcelLabel = byteLabel.decode()
            parcelKeys.append(parcelLabel)
            if parcelLabel.endswith('_ROI'):
                parcelLabel = parcelLabel[:-len('_ROI')]
            if not parcelLabel.lower().startswith(hemisphere):
                parcelLabel = f'{hemisphere.upper()} {parcelLabel}'
            niceParcelLabels.append(parcelLabel)

        return parcelKeys, niceParcelLabels

    @classmethod
    def listParcelsInAtlas(cls, session: Session, atlasKey: str) -> tuple[list[str], list[str]]:
        if atlasKey[0:3] in ('lh.', 'rh.'):
            # allow specifying one hemisphere by prefixing atlasKey with 'lh.' or 'rh.'
            lr, atlasKey = atlasKey[0], atlasKey[3:]
            lrs = (lr,)
        else:
            lrs = ('l', 'r')

        allParcelKeys = []
        allParcelLabels = []
        for lr in lrs:
            assert lr in ('l', 'r')
            fsSphere, fsParcels = cls._prepareAtlas(atlasKey=atlasKey, hemisphere=lr)

            parcelKeys, parcelLabels = cls._decodeParcelLabels(fsParcels[2], lr)

            allParcelKeys.extend(parcelKeys)
            allParcelLabels.extend(parcelLabels)

        return allParcelKeys, allParcelLabels

    @classmethod
    def loadROIsFromAtlas(cls, session: Session, atlasKey: str,
                          parcelKeys: list[str] | None = None,
                          warpSource: tp.Literal['simnibs', 'freesurfer'] = 'simnibs',
                          meshKey: str | None = None) -> ROIs:

        if atlasKey[0:3] in ('lh.', 'rh.'):
            # allow specifying one hemisphere by prefixing atlasKey with 'lh.' or 'rh.'
            lr, atlasKey = atlasKey[0], atlasKey[3:]
            lrs = (lr,)
        else:
            lrs = ('l', 'r')

        rois = ROIs(session=session, )

        if parcelKeys is not None:
            parcelKeys = parcelKeys.copy()  # prepare for modification below

        if meshKey is None:
            meshKey = 'gmFSSurf' if warpSource == 'freesurfer' else 'gmSurf'

        for lr in lrs:
            assert lr in ('l', 'r')
            fsSphere, fsParcels = cls._prepareAtlas(atlasKey=atlasKey, hemisphere=lr)

            parcelKeys_lr, parcelLabels = cls._decodeParcelLabels(fsParcels[2], lr)

            for iL, (parcelKey, parcelLabel) in enumerate(zip(parcelKeys_lr, parcelLabels)):
                if parcelKeys is not None:
                    # only keep subset of parcelKeys_lr that are in parcelKeys, and corresponding parcelLabels
                    if parcelKey not in parcelKeys:
                        continue
                    else:
                        parcelKeys.remove(parcelKey)  # mark as consumed

                color = fsParcels[1][iL, :][0:4]
                # convert from RGBT 0-255 format to RGBA 0-1 format
                color = (*(val / 255 for val in color[0:3]), 1 - color[3] / 255)
                roi = cls(
                    key=parcelLabel.replace(' ', ''),  # spaces cause issues with pyvista actor keys, could fix this elsewhere if needed
                    meshKey=meshKey,
                    parcelKey=parcelKey,
                    atlasKey=atlasKey,
                    hemisphere=lr,
                    warpSource=warpSource,
                    color=color,
                    session=session,
                )
                rois.addItem(roi)

            if parcelKeys is not None:
                if len(parcelKeys) > 0:
                    logger.warning(f'Unmatched parcel keys: {parcelKeys}')

        return rois
