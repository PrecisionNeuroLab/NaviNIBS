from __future__ import annotations

from abc import ABC
import enum
import logging
import functools
import os
import typing as tp
from typing import TYPE_CHECKING, ClassVar

import attrs
import numpy as np

if TYPE_CHECKING:
    from NaviNIBS.Navigator.Model.Session import Session
    import numpy.typing as npt

from NaviNIBS.Navigator.Model.GenericCollection import GenericCollection, GenericCollectionDictItem, GenericListItem, GenericList, collectionDictItemAttrSetter
from NaviNIBS.util.attrs import attrsAsDict
from NaviNIBS.util.numpy import attrsWithNumpyAsDict, array_equalish

logger = logging.getLogger(__name__)


@attrs.define(eq=False)
class ROIStage(GenericListItem, ABC):
    """
    Base class for stages in an ROI assembly pipeline.
    """
    type: ClassVar[str]
    _label: str | None = None

    _session: Session | None = attrs.field(repr=False, default=None)

    def __attrs_post_init__(self):
        pass

    @property
    def label(self):
        return self._label if self._label is not None else self.type

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newSession: Session | None):
        if self._session is newSession:
            return
        self.sigItemAboutToChange.emit(self, ['session'])
        self._session = newSession
        self.sigItemChanged.emit(self, ['session'])

    def _process(self, roiKey: str, inputROI: ROI | None) -> ROI:
        raise NotImplementedError('_process must be implemented in subclasses')

    def process(self, roiKey: str, inputROI: ROI | None) -> ROI | None:
        logger.debug(f'Starting ROIStage processing: {self}')
        ROI = self._process(roiKey=roiKey, inputROI=inputROI)
        logger.debug(f'Finished ROIStage processing: {self}')
        return ROI

    def asDict(self) -> dict[str, tp.Any]:
        d = attrsAsDict(self, exclude=('session',))
        d['type'] = self.type
        return d


@attrs.define(eq=False)
class PassthroughStage(ROIStage):
    type: ClassVar[str] = 'Passthrough'

    def _process(self, roiKey: str, inputROI: ROI | None) -> ROI | None:
        return inputROI


@attrs.define(eq=False)
class SelectSurfaceMesh(ROIStage):
    type: ClassVar[str] = 'SelectSurfaceMesh'
    _meshKey: str | None = None

    @property
    def meshKey(self):
        return self._meshKey

    @meshKey.setter
    def meshKey(self, newMeshKey: str | None):
        if self._meshKey == newMeshKey:
            return
        logger.info(f'Setting SelectSurfaceMesh meshKey to {newMeshKey}')
        self.sigItemAboutToChange.emit(self, ['meshKey'])
        self._meshKey = newMeshKey
        self.sigItemChanged.emit(self, ['meshKey'])

    def _process(self, roiKey: str, inputROI: ROI | None) -> SurfaceMeshROI:
        assert inputROI is None

        # create ROI with empty indices
        return SurfaceMeshROI(
            key=roiKey,
            meshKey=self._meshKey
        )


@attrs.define(eq=False)
class AddFromSeedPoint(ROIStage):
    type: ClassVar[str] = 'AddFromSeedPoint'
    _seedPoint: tuple[float, float, float] | None = attrs.field(default=None,
                                                                converter=attrs.converters.optional(tuple))
    """
    Seed point in 3D space from which to generate the ROI.
    """
    @_seedPoint.validator
    def _check_seedPoint(self, attribute, value):
        assert value is None or len(value) == 3

    _radius: float | None = None
    """
    Radius around the seed point to include in the ROI.
    """
    _distanceMetric: str = 'euclidean'
    """
    euclidean or geodesic
    """

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @property
    def seedPoint(self):
        return self._seedPoint

    @seedPoint.setter
    def seedPoint(self, newSeedPoint: tuple[float, float, float] | None):
        if newSeedPoint is not None:
            if isinstance(newSeedPoint, np.ndarray):
                newSeedPoint = newSeedPoint.tolist()
            if not isinstance(newSeedPoint, tuple):
                newSeedPoint = tuple(newSeedPoint)

        if self._seedPoint == newSeedPoint:
            return

        logger.info(f'Setting AddFromSeedPoint seedPoint to {newSeedPoint}')
        self.sigItemAboutToChange.emit(self, ['seedPoint'])
        self._seedPoint = newSeedPoint
        self.sigItemChanged.emit(self, ['seedPoint'])

    @property
    def radius(self):
        return self._radius

    @radius.setter
    def radius(self, newRadius: float | None):
        if self._radius == newRadius:
            return

        logger.info(f'Setting AddFromSeedPoint radius to {newRadius}')
        self.sigItemAboutToChange.emit(self, ['radius'])
        self._radius = newRadius
        self.sigItemChanged.emit(self, ['radius'])

    @property
    def distanceMetric(self):
        return self._distanceMetric

    def _process(self, roiKey: str, inputROI: ROI | None) -> SurfaceMeshROI:
        # Placeholder for actual ROI generation logic
        logger.debug(f'Generating ROI from seed point: {self._seedPoint} with radius {self._radius} using {self._distanceMetric} metric')
        if self._seedPoint is None:
            logger.warning('No seed point specified, returning input ROI unchanged')
            return inputROI
        if self._radius is None:
            logger.warning('No radius specified, returning input ROI unchanged')
            return inputROI
        assert isinstance(inputROI, SurfaceMeshROI)
        assert inputROI.meshKey is not None
        if self._session is None:
            logger.warning('No session available, returning input ROI unchanged')
            return inputROI
        mesh = getattr(self._session.headModel, inputROI.meshKey)

        outputROI = inputROI.copy()
        outputROI.session = self._session
        if outputROI.seedCoord is None:
            outputROI.seedCoord = self._seedPoint

        match self._distanceMetric:
            case 'euclidean':
                # find vertex indices within radius of seed point using euclidean distance
                dists = np.linalg.norm(mesh.points - np.asarray(self._seedPoint), axis=1)
                newVertexIndices = np.where(dists <= self._radius)[0]
                if inputROI.meshVertexIndices is not None:
                    newVertexIndices = np.union1d(inputROI.meshVertexIndices, newVertexIndices)
                if len(newVertexIndices) == 0:
                    newVertexIndices = None
                outputROI.meshVertexIndices = newVertexIndices

            case _:
                raise NotImplementedError(f'Distance metric {self._distanceMetric} not implemented')

        return outputROI


@attrs.define(eq=False)
class AddFromSeedLine(ROIStage):
    type: ClassVar[str] = 'AddFromSeedLines'
    _seedLine: list[tuple[float, float, float]] = attrs.field(factory=list)
    """
    List of seed line segments points, with at least 2 points to define a line.
    3 or more points will define multiple connected line segments.
    """
    _radius: float | None = None
    """
    Radius around the seed lines to include in the ROI.
    """
    _distanceMetric: str = 'euclidean'
    """
    euclidean or geodesic
    """

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def _process(self, roiKey: str, inputROI: ROI | None) -> SurfaceMeshROI:
        logger.debug(f'Generating ROI from seed line with {len(self._seedLine)} points, radius {self._radius} using {self._distanceMetric} metric')
        assert isinstance(inputROI, SurfaceMeshROI)
        assert inputROI.meshKey is not None
        mesh = getattr(self._session.headModel, inputROI.meshKey)

        raise NotImplementedError  # TODO



@attrs.define(kw_only=True)
class ROI(GenericCollectionDictItem[str], ABC):
    """
    Base class for regions of interest (ROIs).
    """
    type: ClassVar[str]
    _color: tuple[float, float, float] | tuple[float, float, float, float] | None = \
        attrs.field(default=None, converter=attrs.converters.optional(tuple))
    """
    RGB or RGBA color for displaying the ROI, with values in range [0, 1]
    """
    _autoColor: tuple[float, float, float] | None = attrs.field(init=False, default=None, repr=False)
    """
    If no color is specified, this field will be populated with an automatically chosen color.
    """
    _isVisible: bool = True

    _session: Session | None = attrs.field(repr=False, default=None)

    def __attrs_post_init__(self):
        pass

    @property
    def isVisible(self):
        return self._isVisible

    @isVisible.setter
    def isVisible(self, isVisible: bool):
        if self._isVisible == isVisible:
            return
        self.sigItemAboutToChange.emit(self._key, ['isVisible'])
        self._isVisible = isVisible
        self.sigItemChanged.emit(self._key, ['isVisible'])

    @property
    def color(self):
        return self._color

    @color.setter
    def color(self, newColor: tuple[float, float, float] | tuple[float, float, float, float] | None):
        if newColor is not None:
            if len(newColor) not in (3, 4):
                raise ValueError('Color must be a tuple of 3 (RGB) or 4 (RGBA) floats')
            for c in newColor:
                if not (0.0 <= c <= 1.0):
                    raise ValueError('Color values must be in range [0, 1]')

        if self._color == newColor:
            return

        logger.info(f'Setting ROI color to {newColor}')
        self.sigItemAboutToChange.emit(self._key, ['color'])
        self._color = newColor
        self.sigItemChanged.emit(self._key, ['color'])

    @property
    def autoColor(self):
        return self._autoColor

    @autoColor.setter
    def autoColor(self, newAutoColor: tuple[float, float, float] | None):
        if newAutoColor is not None:
            if len(newAutoColor) != 3:
                raise ValueError('Auto color must be a tuple of 3 (RGB) floats')
            for c in newAutoColor:
                if not (0.0 <= c <= 1.0):
                    raise ValueError('Auto color values must be in range [0, 1]')

        if self._autoColor == newAutoColor:
            return

        logger.info(f'Setting ROI autoColor to {newAutoColor}')
        self.sigItemAboutToChange.emit(self._key, ['autoColor'])
        self._autoColor = newAutoColor
        self.sigItemChanged.emit(self._key, ['autoColor'])

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, session: Session):
        if self._session is not session:
            self.sigItemAboutToChange.emit(self._key, ['session'])
            self._session = session
            self.sigItemChanged.emit(self._key, ['session'])

    def copy(self):
        d = self.asDict()
        d.pop('type')
        d['session'] = self._session
        return type(self).fromDict(d)

    def asDict(self) -> dict[str, tp.Any]:
        d = attrsAsDict(self, exclude=['session'])
        d['type'] = self.type
        return d


@attrs.define(kw_only=True)
class SurfaceMeshROI(ROI):
    type: ClassVar[str] = 'SurfaceMeshROI'
    _meshKey: str | None = None
    """
    Key identifying surface mesh in the session's head model on which this ROI is defined.
    """
    _meshVertexIndices: npt.NDArray[np.int64] | None = attrs.field(
        default=None,
        converter=lambda v: np.asarray(v, dtype=np.int64) if v is not None else v
    )
    """
    Indices of the vertices in the mesh identified by ``meshKey`` that belong to this ROI.
    """
    _seedCoord: tuple[float, float, float] | None = attrs.field(
        default=None,
        converter=attrs.converters.optional(tuple)
    )
    """
    Optional coordinate, typically representing approximate center or meaningfully representative 
    point of the ROI. For now, assumed to be defined in native (MRI) space.
    """

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @property
    def meshKey(self):
        return self._meshKey

    @meshKey.setter
    def meshKey(self, newMeshKey: str | None):
        if self._meshKey == newMeshKey:
            return

        logger.info(f'Setting SurfaceMeshROI meshKey to {newMeshKey}')
        self.sigItemAboutToChange.emit(self.key, ['meshKey'])
        self._meshKey = newMeshKey
        self.sigItemChanged.emit(self.key, ['meshKey'])

    @property
    def meshVertexIndices(self) -> npt.NDArray[np.int64] | None:
        return self._meshVertexIndices

    @meshVertexIndices.setter
    def meshVertexIndices(self, newMeshVertexIndices: npt.NDArray[np.int64] | None):
        if newMeshVertexIndices is not None:
            if len(newMeshVertexIndices) == 0:
                newMeshVertexIndices = None
            else:
                newMeshVertexIndices = np.asarray(newMeshVertexIndices, dtype=np.int64)

        if array_equalish(self._meshVertexIndices, newMeshVertexIndices):
            return

        logger.info(f'Setting SurfaceMeshROI meshVertexIndices to len({len(newMeshVertexIndices) if newMeshVertexIndices is not None else 0})')
        self.sigItemAboutToChange.emit(self.key, ['meshVertexIndices'])
        self._meshVertexIndices = newMeshVertexIndices
        self.sigItemChanged.emit(self.key, ['meshVertexIndices'])

    @property
    def seedCoord(self):
        return self._seedCoord

    @seedCoord.setter
    def seedCoord(self, newSeedCoord: tuple[float, float, float] | None):
        if self._seedCoord == newSeedCoord:
            return

        logger.info(f'Setting SurfaceMeshROI seedCoord to {newSeedCoord}')
        self.sigItemAboutToChange.emit(self.key, ['seedCoord'])
        self._seedCoord = newSeedCoord
        self.sigItemChanged.emit(self.key, ['seedCoord'])

    def asDict(self) -> dict[str, tp.Any]:
        d = attrsWithNumpyAsDict(self, npFields=['meshVertexIndices'], exclude=['session'])
        d['type'] = self.type
        return d


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
        from NaviNIBS.util.Transforms import composeTransform, applyTransform
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
        _, nearestPialIndices = pialTree.query(headMesh.points, workers=-1)

        logger.debug('Finding head mesh vertices whose nearest pial vertex belongs to target parcel')
        parcelPialMask = np.zeros(len(allPialCoords), dtype=bool)
        parcelPialMask[pialVertexIndices] = True
        headMeshVertexIndices = np.where(parcelPialMask[nearestPialIndices])[0]

        logger.debug('Setting mesh vertex indices')
        self.meshVertexIndices = headMeshVertexIndices
        logger.debug('done')

    def asDict(self) -> dict[str, tp.Any]:
        d = super().asDict()
        if 'meshVertexIndices' in d and len(d['meshVertexIndices']) > 1e3:
            # for large ROIs, don't save full vertex list and instead reload from original
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
            lr, atlasKey = atlasKey[0:2], atlasKey[3:]
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
            lr, atlasKey = atlasKey[0:2], atlasKey[3:]
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
                    #color=color,
                    session=session,
                )
                rois.addItem(roi)

            if parcelKeys is not None:
                if len(parcelKeys) > 0:
                    logger.warning(f'Unmatched parcel keys: {parcelKeys}')

        return rois


class _EmptyCache:
    pass

_emptyCache = _EmptyCache()


@attrs.define(kw_only=True)
class PipelineROI(ROI):

    @attrs.define
    class PipelineStages(GenericList[ROIStage]):
        _stageLibrary: dict[str, type[ROIStage]] = attrs.field(init=False, factory=dict)

        _session: Session | None = attrs.field(default=None, repr=False)

        def __attrs_post_init__(self):
            super().__attrs_post_init__()
            self.sigItemsChanged.connect(self._setSessionOnItemsChanged)

            for cls in (
                    PassthroughStage,
                    SelectSurfaceMesh,
                    AddFromSeedPoint,
                    AddFromSeedLine,
            ):
                self._stageLibrary[cls.type] = cls

        @property
        def stageLibrary(self):
            return self._stageLibrary

        @property
        def session(self):
            return self._session

        @session.setter
        def session(self, session: Session):
            if self._session is not session:
                self._session = session
                self._setSessionOnItemsChanged(self._items)

        def _setSessionOnItemsChanged(self, items: list[ROIStage], attribNames: list[str] | None = None):
            logger.debug(f'_setSessionOnItemsChanged called with items: {items}, attribNames: {attribNames}')
            if attribNames is None:
                for item in items:
                    if item in self:
                        item.session = self._session

        @classmethod
        def fromList(cls, itemList: list[dict[str, tp.Any]], session: Session | None = None) -> PipelineROI.PipelineStages:
            items = []
            for itemDict in itemList:
                try:
                    stageType = itemDict.pop('type')
                except KeyError:
                    raise ValueError('ROIStage dict missing "type" field')
                if stageType not in cls().stageLibrary:
                    raise ValueError(f'Unknown ROIStage type: {stageType}')
                StageCls = cls().stageLibrary[stageType]
                assert issubclass(StageCls, ROIStage)
                itemDict['session'] = session
                items.append(StageCls.fromDict(itemDict))

            return cls(items=items, session=session)

    type: ClassVar[str] = 'PipelineROI'

    _cachedOutput: ROI | _EmptyCache = attrs.field(init=False, default=_emptyCache)

    _stages: PipelineStages = attrs.field(factory=PipelineStages)

    _session: Session | None = attrs.field(repr=False, default=None)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._stages.sigItemsAboutToChange.connect(lambda stageKeys, whichAttrs=None: \
                self.sigItemAboutToChange.emit(self.key, ['stages']))

        self._stages.sigItemsChanged.connect(lambda stageKeys, whichAttrs=None: \
                self.sigItemChanged.emit(self.key, ['stages']))

        self._stages.sigItemsChanged.connect(lambda *args: self.clearCache(), priority=1)

        self.sigItemChanged.connect(self._onSelfChanged, priority=2)

    @property
    def stages(self):
        return self._stages

    @property
    def stageLibrary(self):
        return self._stages.stageLibrary

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newSession: Session | None):
        if self._session is newSession:
            return
        self.sigItemAboutToChange.emit(self.key, ['session'])
        self._session = newSession
        self._stages.session = newSession
        self.sigItemChanged.emit(self.key, ['session'])

    def process(self, upThroughStage: int | None = None):
        logger.debug(f'Processing PipelineROI {self.key}{f" up through stage {upThroughStage}" if upThroughStage is not None else ""}')

        if upThroughStage is None:
            self.sigItemAboutToChange.emit(self.key, ['output'])

        if len(self._stages) == 0:
            logger.info(f'PipelineROI {self.key} has no stages')
            ROI = SurfaceMeshROI(key=self.key)
        else:
            ROI = None
            for iStage, stage in enumerate(self._stages):
                logger.debug(f'Processing ROI stage {iStage}: {stage}')
                ROI = stage.process(roiKey=f'{iStage}', inputROI=ROI)
                if upThroughStage is not None and iStage == upThroughStage:
                    return ROI

        if ROI is not None:
            # apply some fields from parent to child if not set
            vars = ['color', 'autoColor']
            for var in vars:
                if getattr(ROI, var) is None:
                    setattr(ROI, var, getattr(self, var))
        else:
            pass

        self._cachedOutput = ROI

        self.sigItemChanged.emit(self.key, ['output'])

    def _onSelfChanged(self, key: str, changedAttrs: list[str] | None = None):
        if changedAttrs is None \
                or 'color' in changedAttrs \
                or 'autoColor' in changedAttrs:
            # since colors are copied to output during processing, need to refresh if they change
            self.clearCache()

    def getOutput(self) -> ROI | None:
        if self._cachedOutput is _emptyCache:
            self.process()
        assert not self._cachedOutput is _emptyCache
        return self._cachedOutput

    def clearCache(self):
        logger.debug(f'Clearing cached output for PipelineROI {self.key}')
        self._cachedOutput = _emptyCache

    def asDict(self) -> dict[str, tp.Any]:
        d = attrsAsDict(self, exclude=('stages', 'session'))
        d['type'] = self.type
        d['stages'] = self._stages.asList()
        return d

    @classmethod
    def fromDict(cls, d: dict[str, tp.Any]):
        stagesList = d.pop('stages')
        session = d.get('session', None)
        stages = cls.PipelineStages.fromList(stagesList, session=session)
        d['stages'] = stages
        roi = cls(**d)
        return roi


@attrs.define
class ROIs(GenericCollection[str, ROI]):
    _session: Session | None = attrs.field(default=None, repr=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        self.sigItemsChanged.connect(self._setSessionOnItemsChanged)

    def _setSessionOnItemsChanged(self, keys: list[str], attribNames: list[str] | None = None):
        logger.debug(f'_setSessionOnItemsChanged called with keys: {keys}, attribNames: {attribNames}')
        if attribNames is None:
            for key in keys:
                if key in self:
                    self[key].session = self._session

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newSession: Session | None):
        if self._session is newSession:
            return
        self._session = newSession
        self._setSessionOnItemsChanged(self.keys())

    @classmethod
    def fromList(cls, itemList: list[dict[str, tp.Any]], session: Session | None = None) -> ROIs:
        items = {}
        for itemDict in itemList:
            key = itemDict['key']
            itemDict['session'] = session
            items[key] = cls.roiFromDict(itemDict)

        return cls(items=items, session=session)

    @classmethod
    def roiFromDict(cls, roiDict: dict[str, tp.Any]) -> ROI:
        roiType = roiDict.pop('type')
        match roiType:
            case 'SurfaceMeshROI':
                ROICls = SurfaceMeshROI
            case 'AtlasSurfaceParcel':
                ROICls = AtlasSurfaceParcel
            case 'PipelineROI':
                ROICls = PipelineROI
            case _:
                raise ValueError(f'Unknown ROI type: {roiType}')
        return ROICls.fromDict(roiDict)
