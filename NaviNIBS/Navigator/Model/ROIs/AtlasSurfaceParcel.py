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

    @staticmethod
    @functools.cache
    def _getSubSphereRegCoords(m2mDir: str, hemisphere: str) -> np.ndarray:
        import nibabel as nib
        subSphereReg = nib.load(os.path.join(
            m2mDir, 'surfaces', hemisphere + 'h.sphere.reg.gii'))

        subSphereRegCoords = subSphereReg.darrays[0].data
        # subSphereRegFaces = subSphereReg.darrays[1].data

        return subSphereRegCoords

    @staticmethod
    @functools.cache
    def _getPialCoords(m2mDir: str, hemisphere: str) -> np.ndarray:
        import nibabel as nib
        pial = nib.load(os.path.join(
            m2mDir, 'surfaces', hemisphere + 'h.pial.gii'))
        pialCoords = pial.darrays[0].data
        return pialCoords

    @staticmethod
    @functools.cache
    def _getNearestFsIndices(atlasKey: str, lr: str, m2mDir: str, hemisphere: str) -> np.ndarray:
        """
        For each sphere.reg.gii vertex, return the index of its nearest fsaverage sphere vertex.
        Cached per atlas+subject so the expensive KDTree query is shared across all parcels
        loaded from the same atlas for the same subject.
        """
        from scipy.spatial import cKDTree
        fsSphere, _ = AtlasSurfaceParcel._prepareAtlas(atlasKey=atlasKey, lr=lr)
        subSphereRegCoords = AtlasSurfaceParcel._getSubSphereRegCoords(m2mDir=m2mDir, hemisphere=hemisphere)
        fsSphereCoordsTree = cKDTree(fsSphere[0])
        if True:
            # convert from (-1,1) scale to (-100, 100)
            subSphereRegCoords *= 100
        _, nearestFsIndices = fsSphereCoordsTree.query(subSphereRegCoords, workers=-1)
        return nearestFsIndices

    def reload(self):
        logger.info(f'Reloading AtlasSurfaceParcel {self._parcelKey} from {self._hemisphere}h.{self._atlasKey}')
        from scipy.spatial import cKDTree

        from NaviNIBS.Navigator.Model.HeadModel import MshVersion

        assert len(self._atlasKey) > 0

        assert self._hemisphere is not None, 'Support for not specifying hemisphere not yet implemented'

        _, fsParcels = self._prepareAtlas(atlasKey=self._atlasKey, lr=self._hemisphere)

        assert self.session.headModel.mshVersion == MshVersion.CHARM, 'Freesurfer registration currently only supported with CHARM head models'

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

        # For each sphere.reg.gii vertex, find its nearest fsaverage sphere vertex and check
        # whether that vertex carries the target parcel label. Reversing the search direction
        # (pial→atlas rather than atlas→pial) ensures every pial vertex is unambiguously
        # assigned to a parcel with no interior gaps.
        # _getNearestFsIndices is cached per atlas+subject, so this is only computed once
        # when loading multiple parcels from the same atlas.
        logger.debug('Getting nearest fsaverage indices for registered sphere vertices')
        nearestFsIndices = self._getNearestFsIndices(
            atlasKey=self._atlasKey, lr=self._hemisphere,
            m2mDir=self.session.headModel.m2mDir, hemisphere=self._hemisphere)
        logger.debug('Finding pial vertices belonging to target parcel')
        pialVertexIndices = np.where(fsParcels[0][nearestFsIndices] == parcelIndex)[0]

        # Validate mesh key is a GM surface (compatible with the pial-based registration)
        assert self.meshKey is not None and self.meshKey.startswith('gm'), \
            f'AtlasSurfaceParcel requires a GM mesh key (e.g. gmSurf, gmSimpleSurf), got {self.meshKey!r}'

        # Map head model mesh vertices → parcel membership via pial.gii.
        # sphere.reg.gii and pial.gii share the same vertex topology, so pialVertexIndices
        # index into pial.gii coordinates directly.
        # We reverse the search direction (head mesh → pial) to correctly handle cases
        # where the head mesh has lower resolution than the pial surface.
        pialCoords = self._getPialCoords(m2mDir=self.session.headModel.m2mDir, hemisphere=self._hemisphere)
        otherHemisphere = 'lr'['rl'.index(self._hemisphere)]
        otherPialCoords = self._getPialCoords(m2mDir=self.session.headModel.m2mDir, hemisphere=otherHemisphere)
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
    def _prepareAtlas(atlasKey: str, lr: tp.Literal['l', 'r']) -> \
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
            mneSubjectsDir, 'fsaverage', 'surf', lr + 'h.sphere'))

        fsParcels: tuple[np.ndarray, np.ndarray, list[bytes]] = nib.freesurfer.read_annot(os.path.join(
            mneSubjectsDir, 'fsaverage', 'label', f'{lr}h.{atlasKey}.annot'))

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
            fsSphere, fsParcels = cls._prepareAtlas(atlasKey=atlasKey, lr=lr)

            parcelKeys, parcelLabels = cls._decodeParcelLabels(fsParcels[2], lr)

            allParcelKeys.extend(parcelKeys)
            allParcelLabels.extend(parcelLabels)

        return allParcelKeys, allParcelLabels

    @classmethod
    def loadROIsFromAtlas(cls, session: Session, atlasKey: str, parcelKeys: list[str] | None = None) -> ROIs:

        if atlasKey[0:3] in ('lh.', 'rh.'):
            # allow specifying one hemisphere by prefixing atlasKey with 'lh.' or 'rh.'
            lr, atlasKey = atlasKey[0], atlasKey[3:]
            lrs = (lr,)
        else:
            lrs = ('l', 'r')

        rois = ROIs(session=session, )

        if parcelKeys is not None:
            parcelKeys = parcelKeys.copy()  # prepare for modification below

        for lr in lrs:
            assert lr in ('l', 'r')
            fsSphere, fsParcels = cls._prepareAtlas(atlasKey=atlasKey, lr=lr)

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
                    meshKey='gmSurf',
                    parcelKey=parcelKey,
                    atlasKey=atlasKey,
                    hemisphere=lr,
                    color=color,
                    session=session,
                )
                rois.addItem(roi)

            if parcelKeys is not None:
                if len(parcelKeys) > 0:
                    logger.warning(f'Unmatched parcel keys: {parcelKeys}')

        return rois
