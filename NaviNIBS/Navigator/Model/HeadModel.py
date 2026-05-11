from __future__ import annotations

import shutil
from enum import StrEnum

import attrs
from datetime import datetime
import enum
import nibabel as nib
import json
import logging
import numpy as np
import os
import pandas as pd
import pyvista as pv
import tempfile
import typing as tp
from typing import ClassVar

from NaviNIBS.util.attrs import attrsAsDict
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.numpy import array_equalish, attrsWithNumpyAsDict, attrsWithNumpyFromDict

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from NaviNIBS.Navigator.Model.Session import Session

logger = logging.getLogger(__name__)


SurfMesh = pv.PolyData
VolMesh = pv.PolyData


class MshVersion(StrEnum):
    HEADRECO = enum.auto()
    CHARM = enum.auto()


# TODO: get these dynamically from SimNIBS-generated .msh.opt files instead of hardcoding here
defaultCharmMshSurfIndexMapping = dict(
    WM=1001,
    GM=1002,
    CSF=1003,
    Scalp=1005,
    Eye_balls=1006,
    Compact_bone=1007,
    Spongy_bone=1008,
    Blood=1009,
    Muscle=1010,
)



@attrs.define()
class HeadModel:
    _filepath: str | None = None
    """ 
    Path to .msh file in simnibs folder.
    (note that .msh file and other nested files in same parent dir will be used)
    """
    _skinSurfFilepath: str | None = None
    """
    Path to skin surface mesh file, if not provided will be loaded from simnibs results folder.
    """
    _gmSurfFilepath: str | None = None
    """
    Path to gray matter surface mesh file, if not provided will be loaded from simnibs results folder.
    """
    _meshToMRITransform: np.ndarray | None = None
    """"
    Optional 4x4 transform matrix to convert mesh coordinates to MRI coordinates. Applied to all
    meshes when loading. Should not be needed in typical use cases.
    """
    _defaceSkinForDisplay: bool = False
    """
    Whether to try to deface the skin by cropping below LPA/RPA up to NAS for display purposes.
    This only works if fiducials with expected names are set.
    """
    _defaceFiducialNames: tuple[str, str, str] = ('LPA', 'NAS', 'RPA')
    """
    Names of fiducials to use for defacing, in (LPA, NAS, RPA) order.
    LPA and RPA define the 10 mm inferior cut level; NAS defines the 5 mm inferior cut level.
    Override if fiducials use non-standard names.
    """
    _freesurferFilepath: str | None = None
    """
    Path to FreeSurfer output directory or zip archive.
    Used to derive MNI nonlinear transforms from SynthMorph warp files,
    and potentially other FreeSurfer-based processing in the future.
    """
    _session: Session | None = attrs.field(init=False, default=None, repr=False)

    _msh: tp.Optional[pv.PolyData] = attrs.field(init=False, default=None)
    _skinSurf: tp.Optional[SurfMesh] = attrs.field(init=False, default=None)
    _csfSurf: tp.Optional[SurfMesh] = attrs.field(init=False, default=None)
    _gmSurf: tp.Optional[SurfMesh] = attrs.field(init=False, default=None)
    _skinSimpleSurf: tp.Optional[SurfMesh] = attrs.field(init=False, default=None)
    _skinConvexSurf: tp.Optional[SurfMesh] = attrs.field(init=False, default=None)
    _skinDefacedSurf: tp.Optional[SurfMesh] = attrs.field(init=False, default=None)
    _skinSimpleDefacedSurf: tp.Optional[SurfMesh] = attrs.field(init=False, default=None)
    _gmSimpleSurf: tp.Optional[SurfMesh] = attrs.field(init=False, default=None)
    _eegPositions: tp.Optional[pd.DataFrame] = attrs.field(init=False, default=None)
    _mshVersion: tp.Optional[MshVersion] = attrs.field(init=False, default=None)
    _freesurferTempDir: tempfile.TemporaryDirectory | None = attrs.field(init=False, default=None)

    sigFilepathChanged: Signal = attrs.field(init=False, factory=Signal)
    """
    Emitted when main .msh or manually specified skin or gray matter surface mesh filepaths change.
    """
    sigDataChanged: Signal = attrs.field(init=False, factory=lambda: Signal((tp.Optional[str],)))
    """
    emits key `which` indicating what changed, e.g. which='gmSurf'; 
    if None all should be assumed to have changed
    """
    sigTransformChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        self.sigFilepathChanged.connect(self._onFilepathChanged)
        self.validateFilepath(self._filepath)

    @property
    def defaceFiducialNames(self) -> tuple[str, str, str]:
        return self._defaceFiducialNames

    @defaceFiducialNames.setter
    def defaceFiducialNames(self, value: tuple[str, str, str]):
        if self._defaceFiducialNames != value:
            self._defaceFiducialNames = value
            if self._skinDefacedSurf is not None:
                self._skinDefacedSurf = None
                self.sigDataChanged.emit('skinDefacedSurf')
            if self._skinSimpleDefacedSurf is not None:
                self._skinSimpleDefacedSurf = None
                self.sigDataChanged.emit('skinSimpleDefacedSurf')

    @property
    def defaceSkinForDisplay(self) -> bool:
        return self._defaceSkinForDisplay

    @defaceSkinForDisplay.setter
    def defaceSkinForDisplay(self, value: bool):
        if self._defaceSkinForDisplay != value:
            self._defaceSkinForDisplay = value
            self.sigDataChanged.emit(None)

    @property
    def session(self) -> Session | None:
        return self._session

    @session.setter
    def session(self, session: Session):
        if self._session is not session:
            self._session = session
            if session is not None:
                session.subjectRegistration.fiducials.sigItemsChanged.connect(
                    self._onFiducialsChanged)
                if self._skinDefacedSurf is not None:
                    self._skinDefacedSurf = None
                    self.sigDataChanged.emit('skinDefacedSurf')
                if self._skinSimpleDefacedSurf is not None:
                    self._skinSimpleDefacedSurf = None
                    self.sigDataChanged.emit('skinSimpleDefacedSurf')

    def _onFiducialsChanged(self, keys: list[str], attrNames: list[str] | None):
        if not any(k in self._defaceFiducialNames for k in keys):
            return
        if attrNames is not None and 'plannedCoord' not in attrNames:
            return
        if self._skinDefacedSurf is not None:
            self._skinDefacedSurf = None
            self.sigDataChanged.emit('skinDefacedSurf')
        if self._skinSimpleDefacedSurf is not None:
            self._skinSimpleDefacedSurf = None
            self.sigDataChanged.emit('skinSimpleDefacedSurf')

    @property
    def m2mDir(self) -> str | None:
        if self.filepath is None:
            return None
        parentDir = os.path.dirname(self.filepath)  # simnibs results dir
        match self.mshVersion:
            case MshVersion.CHARM:
                # charm .msh should already be embedded in m2m directory
                return parentDir
            case MshVersion.HEADRECO:
                # headreco .msh should be next to m2m directory
                subStr = os.path.splitext(os.path.basename(self.filepath))[0]  # e.g. 'sub-1234'
                m2mDir = os.path.join(parentDir, 'm2m_' + subStr)
                assert os.path.exists(m2mDir), 'm2m folder not found. Are full SimNIBS results available next to specified .msh file?'
                return m2mDir
            case _:
                raise NotImplementedError('Unknown msh version. Cannot determine m2m directory.')

    @property
    def skinSurfPath(self):
        if self._skinSurfFilepath is not None:
            # if skinSurfFilepath is set, use it
            return self._skinSurfFilepath
        # otherwise, use default path in m2m folder
        return os.path.join(self.m2mDir, 'skin.stl')

    @property
    def csfSurfPath(self):
        return os.path.join(self.m2mDir, 'csf.stl')

    @property
    def gmSurfPath(self):
        if self._gmSurfFilepath is not None:
            # if gmSurfFilepath is set, use it
            return self._gmSurfFilepath
        # otherwise, use default path in m2m folder
        return os.path.join(self.m2mDir, 'gm.stl')

    @property
    def meshToMRITransform(self) -> np.ndarray | None:
        return self._meshToMRITransform

    @meshToMRITransform.setter
    def meshToMRITransform(self, newTransform: np.ndarray | None):
        if array_equalish(newTransform, self._meshToMRITransform):
            return
        if newTransform is not None:
            assert isinstance(newTransform, np.ndarray), 'meshToMRITransform must be a numpy array'
            assert newTransform.shape == (4, 4), 'meshToMRITransform must be a 4x4 matrix'
        logger.info('Setting meshToMRITransform to {}'.format(newTransform))
        self._meshToMRITransform = newTransform
        self.sigTransformChanged.emit()
        self.clearCache('all')  # will signal change for all cached meshes

    @property
    def mshVersion(self):
        if self._mshVersion is None:
            self.loadCache(which='mshVersion')
        return self._mshVersion

    def loadCache(self, which: str):
        if not self.isSet:
            logger.warning('Load data requested, but no filepath(s) set. Returning.')
            return

        if which in ('msh', 'skinSurf', 'csfSurf', 'gmSurf'):
            if self.filepath is None \
                    or which == 'msh' \
                    or self.mshVersion == MshVersion.HEADRECO:
                match which:
                    case 'msh':
                        meshPath = self.filepath
                    case 'gmSurf':
                        meshPath = self.gmSurfPath
                    case 'csfSurf':
                        meshPath = self.csfSurfPath
                    case 'skinSurf':
                        meshPath = self.skinSurfPath
                    case _:
                        raise NotImplementedError

                if meshPath is None:
                    logger.warning(f'No mesh path set for {which}')
                    mesh = None
                else:
                    logger.info('Loading {} mesh from {}'.format(which, meshPath))
                    mesh = pv.read(meshPath)

            elif self.mshVersion == MshVersion.CHARM:
                # separate surface from larger .msh file
                fullMesh = self.msh

                match which:
                    case 'gmSurf':
                        surfIndex = defaultCharmMshSurfIndexMapping['GM']
                        if self._gmSurfFilepath is not None:
                            raise NotImplementedError('Separate gmSurfFilepath not supported when using CHARM msh version.')
                    case 'csfSurf':
                        surfIndex = defaultCharmMshSurfIndexMapping['CSF']
                    case 'skinSurf':
                        surfIndex = defaultCharmMshSurfIndexMapping['Scalp']
                        if self._skinSurfFilepath is not None:
                            raise NotImplementedError('Separate skinSurfFilepath not supported when using CHARM msh version.')
                    case _:
                        raise NotImplementedError

                mesh = fullMesh.extract_values(values=surfIndex, scalars='gmsh:physical', adjacent_cells=False).extract_surface()

            else:
                raise NotImplementedError

            if self._meshToMRITransform is not None and mesh is not None:
                logger.debug('Applying meshToMRITransform to mesh')
                mesh.transform(self._meshToMRITransform, inplace=True)

            setattr(self, '_' + which, mesh)

        elif which in ('skinSimpleSurf', 'gmSimpleSurf'):
            if which == 'gmSimpleSurf':
                mesh = self.gmSurf
            elif which == 'skinSimpleSurf':
                mesh = self.skinSurf
            else:
                raise NotImplementedError

            if mesh is None:
                logger.warning(f"No mesh set for {which.replace('Simple', '')}. Returning.")
                return

            logger.info(f'Simplifying mesh for {which}')
            mesh = mesh.decimate(0.8)
            logger.debug('Done simplifying mesh')

            # don't apply meshToMRITransform here since it was already applied to the unsimplified mesh

            setattr(self, '_' + which, mesh)

        elif which in ('skinConvexSurf',):
            if which == 'skinConvexSurf':
                mesh = self.skinSurf
            else:
                raise NotImplementedError

            if mesh is None:
                logger.warning(f"No mesh set for {which.replace('Convex', '')}. Returning.")
                return

            logger.info(f'Computing convex hull for {which}')
            if True:
                # adapted from https://gist.github.com/flutefreak7/bd621a9a836c8224e92305980ed829b9
                from scipy.spatial import ConvexHull
                hull = ConvexHull(mesh.points)
                faces = np.column_stack((3 * np.ones((len(hull.simplices), 1), dtype=int), hull.simplices)).flatten()
                convexMesh = pv.PolyData(mesh.points, faces)
            else:
                # TODO: try doing convex hull natively through pyvista/vtk and benchmark comparison against scipy
                raise NotImplementedError
            # TODO: implement alternate scipy version and benchmark to see which is faster
            logger.debug('Done computing convex hull')

            # don't apply meshToMRITransform here since it was already applied to the unsimplified mesh
            setattr(self, '_' + which, convexMesh)

        elif which == 'skinDefacedSurf':
            skinSurf = self.skinSurf
            if skinSurf is None:
                logger.warning('No skin surface available for defacing')
                return

            defaced = None
            if self._session is None:
                logger.warning('No session set on HeadModel, cannot deface skin surface')
            else:
                plannedFiducials = self._session.subjectRegistration.fiducials.plannedFiducials
                lpaName, nasName, rpaName = self._defaceFiducialNames
                lpa = plannedFiducials.get(lpaName)
                nas = plannedFiducials.get(nasName)
                rpa = plannedFiducials.get(rpaName)
                if any(coord is None for coord in (lpa, nas, rpa)):
                    logger.warning(f'Missing one or more defacing fiducials ({self._defaceFiducialNames}), '
                                   f'cannot deface skin surface')
                else:
                    center = (lpa + rpa) / 2
                    dir_lr = rpa - lpa
                    dir_lr /= np.linalg.norm(dir_lr)
                    dir_pa = nas - center
                    dir_pa /= np.linalg.norm(dir_pa)
                    dir_sup = np.cross(dir_lr, dir_pa)
                    dir_sup /= np.linalg.norm(dir_sup)

                    p_nas = nas - 1.0 * dir_sup
                    p_lpa = lpa - 2.0 * dir_sup
                    p_rpa = rpa - 2.0 * dir_sup

                    normal = np.cross(p_lpa - p_nas, p_rpa - p_nas)
                    normal /= np.linalg.norm(normal)

                    logger.info('Defacing skin surface')
                    defaced = tp.cast(SurfMesh, skinSurf.clip(normal=normal, origin=p_nas, invert=False))
                    logger.debug('Done defacing skin surface')

            if defaced is None:
                defaced = skinSurf  # fall back to full surface

            self._skinDefacedSurf = defaced

        elif which == 'skinSimpleDefacedSurf':
            mesh = self.skinDefacedSurf
            if mesh is None:
                logger.warning('No defaced skin surface available for simplification')
                return
            logger.info('Simplifying skinDefacedSurf')
            mesh = mesh.decimate(0.8)
            logger.debug('Done simplifying skinDefacedSurf')
            self._skinSimpleDefacedSurf = mesh

        elif which == 'eegPositions':
            csvPath = os.path.join(self.m2mDir, 'eeg_positions', 'EEG10-10_UI_Jurak_2007.csv')
            columnLabels = ('type', 'x', 'y', 'z', 'label')
            logger.info('Loading EEG positions from {}'.format(csvPath))
            self._eegPositions = pd.read_csv(csvPath, names=columnLabels, index_col='label')
            assert self._eegPositions.shape[1] == len(columnLabels) - 1

            if self._meshToMRITransform is not None:
                raise NotImplementedError  # TODO: add support for transforming EEG positions with meshToMRITransform

        elif which == 'mshVersion':
            # determine msh version by checking for version-specific log in m2m folder
            if self._filepath is None:
                self._mshVersion = None

            else:
                mshDir = os.path.dirname(self._filepath)
                if os.path.exists(os.path.join(mshDir, 'charm_log.html')):
                    # charm .msh should already be embedded in m2m directory
                    self._mshVersion = MshVersion.CHARM
                else:
                    # headreco .msh should be next to m2m directory
                    subStr = os.path.splitext(os.path.basename(self._filepath))[0]  # e.g. 'sub-1234'
                    m2mDir = os.path.join(mshDir, 'm2m_' + subStr)
                    if os.path.exists(os.path.join(m2mDir, 'headreco_log.html')):
                        self._mshVersion = MshVersion.HEADRECO
                    else:
                        raise RuntimeError('Could not determine msh version from SimNIBS results folder structure.')

        else:
            raise NotImplementedError()

        self.sigDataChanged.emit(which)

    def clearCache(self, which: str):

        if which == 'all':
            allKeys = ('skinSurf', 'csfSurf', 'gmSurf', 'skinSimpleSurf', 'gmSimpleSurf',
                       'skinConvexSurf', 'skinDefacedSurf', 'skinSimpleDefacedSurf', 'eegPositions', 'mshVersion')
            for w in allKeys:
                self.clearCache(which=w)
            return

        if which in ('skinSurf', 'csfSurf', 'gmSurf', 'gmSimpleSurf', 'skinSimpleSurf',
                     'skinConvexSurf', 'skinDefacedSurf', 'skinSimpleDefacedSurf', 'eegPositions', 'mshVersion'):
            if getattr(self, '_' + which) is None:
                return
            setattr(self, '_' + which, None)
        else:
            raise NotImplementedError

        self.sigDataChanged.emit(which)

    def _onFilepathChanged(self):
        with self.sigDataChanged.blocked():
            self.clearCache('all')
        self.sigDataChanged.emit(None)

    @property
    def filepath(self):
        return self._filepath

    @filepath.setter
    def filepath(self, newPath: str):
        if self._filepath == newPath:
            return
        self.validateFilepath(newPath)
        self._filepath = newPath
        self.sigFilepathChanged.emit()
        # TODO: here or with slots connected to sigDataChanged, make sure any cached MRI data or metadata is cleared/reloaded

    @property
    def skinSurfFilepath(self) -> str | None:
        """
        Different than skinSurfPath, this is only not None if explicitly set.
        """
        return self._skinSurfFilepath

    @skinSurfFilepath.setter
    def skinSurfFilepath(self, newPath: str | None):
        if self._skinSurfFilepath == newPath:
            return
        if newPath is not None:
            assert os.path.exists(newPath), f'Skin surface mesh file not found at {newPath}'
        self._skinSurfFilepath = newPath
        self.sigFilepathChanged.emit()

    @property
    def gmSurfFilepath(self) -> str | None:
        """
        Different than gmSurfPath, this is only not None if explicitly set.
        """
        return self._gmSurfFilepath

    @gmSurfFilepath.setter
    def gmSurfFilepath(self, newPath: str | None):
        if self._gmSurfFilepath == newPath:
            return
        if newPath is not None:
            assert os.path.exists(newPath), f'Gray matter surface mesh file not found at {newPath}'
        self._gmSurfFilepath = newPath
        self.sigFilepathChanged.emit()

    @property
    def freesurferFilepath(self) -> str | None:
        return self._freesurferFilepath

    @freesurferFilepath.setter
    def freesurferFilepath(self, newPath: str | None):
        if self._freesurferFilepath == newPath:
            return
        if newPath is not None:
            assert os.path.exists(newPath), f'FreeSurfer path not found at {newPath}'
        self._freesurferFilepath = newPath
        self._freesurferTempDir = None  # invalidate cached extractions
        self.sigFilepathChanged.emit()

    def getFreesurferSubfilePath(self, subpath: str) -> str | None:
        """
        Returns the path to a file within the FreeSurfer directory or zip archive.
        For zip archives, extracts just the requested file to a temporary directory
        on first access (cached per HeadModel instance; cleared when freesurferFilepath changes).
        Returns None if freesurferFilepath is not set or the file cannot be found.
        """
        import zipfile
        if self._freesurferFilepath is None:
            return None
        if os.path.isdir(self._freesurferFilepath):
            return os.path.join(self._freesurferFilepath, subpath)
        # treat as zip archive
        if self._freesurferTempDir is None:
            self._freesurferTempDir = tempfile.TemporaryDirectory(prefix='NaviNIBSFreeSurfer_')
        extractedPath = os.path.join(self._freesurferTempDir.name, subpath)
        if not os.path.exists(extractedPath):
            zipSubpath = subpath.replace('\\', '/')
            logger.info(f'Extracting {zipSubpath} from FreeSurfer zip {self._freesurferFilepath}')
            try:
                with zipfile.ZipFile(self._freesurferFilepath, 'r') as zf:
                    zf.extract(zipSubpath, self._freesurferTempDir.name)
            except (KeyError, FileNotFoundError):
                logger.error(f'File {zipSubpath} not found in zip {self._freesurferFilepath}')
                return None
        return extractedPath

    @property
    def isSet(self):
        return self._filepath is not None or self._skinSurfFilepath is not None and self._gmSurfFilepath is not None

    @property
    def skinSurfIsSet(self):
        return self._filepath is not None or self._skinSurfFilepath is not None

    @property
    def gmSurfIsSet(self):
        return self._filepath is not None or self._gmSurfFilepath is not None

    @property
    def surfKeys(self):
        if self._filepath is not None:
            # TODO: set this dynamically instead of hardcoded
            # TODO: add others
            allSurfKeys = ('skinSurf', 'csfSurf', 'gmSurf')
        else:
            allSurfKeys = []
            if self._skinSurfFilepath is not None:
                allSurfKeys.append('skinSurf')
            if self._gmSurfFilepath is not None:
                allSurfKeys.append('gmSurf')
            allSurfKeys = tuple(allSurfKeys)

        return allSurfKeys

    @property
    def gmSurf(self):
        if self.gmSurfIsSet and self._gmSurf is None:
            self.loadCache(which='gmSurf')
        return self._gmSurf

    @property
    def gmSimpleSurf(self):
        """
        Simplified version of gmSurf mesh, for faster plotting.
        """
        if self.gmSurfIsSet and self._gmSimpleSurf is None:
            self.loadCache(which='gmSimpleSurf')
        return self._gmSimpleSurf

    @property
    def csfSurf(self):
        if self._filepath is not None and self._csfSurf is None:
            self.loadCache(which='csfSurf')
        return self._csfSurf

    @property
    def skinSurf(self):
        if self.skinSurfIsSet and self._skinSurf is None:
            self.loadCache(which='skinSurf')
        return self._skinSurf

    @property
    def msh(self):
        if self._filepath is not None and self._msh is None:
            self.loadCache(which='msh')
        return self._msh

    @property
    def skinSimpleSurf(self):
        """
        Simplified version of skinSurf mesh, for faster plotting.
        """
        if self.skinSurfIsSet and self._skinSimpleSurf is None:
            self.loadCache(which='skinSimpleSurf')
        return self._skinSimpleSurf

    @property
    def skinConvexSurf(self):
        if self.skinSurfIsSet and self._skinConvexSurf is None:
            self.loadCache(which='skinConvexSurf')
        return self._skinConvexSurf
    
    @property
    def skinDisplaySurf(self):
        if self._defaceSkinForDisplay:
            return self.skinDefacedSurf
        else:
            return self.skinSurf

    @property
    def skinSimpleDisplaySurf(self):
        if self._defaceSkinForDisplay:
            return self.skinSimpleDefacedSurf
        else:
            return self.skinSimpleSurf

    @property
    def skinSimpleDefacedSurf(self):
        """
        Simplified version of skinDefacedSurf, for faster rendering.
        """
        if self.skinSurfIsSet and self._skinSimpleDefacedSurf is None:
            self.loadCache(which='skinSimpleDefacedSurf')
        return self._skinSimpleDefacedSurf

    @property
    def skinDefacedSurf(self):
        if self.skinSurfIsSet and self._skinDefacedSurf is None:
            self.loadCache(which='skinDefacedSurf')
        return self._skinDefacedSurf

    @property
    def eegPositions(self):
        if self._filepath is not None and self._eegPositions is None:
            self.loadCache(which='eegPositions')
        return self._eegPositions

    def asDict(self, filepathRelTo: str) -> tp.Dict[str, tp.Any]:
        d = attrsWithNumpyAsDict(self, npFields=('meshToMRITransform',))
        # convert to relative paths
        for key in ('filepath', 'skinSurfFilepath', 'gmSurfFilepath', 'freesurferFilepath'):
            if key in d:
                d[key] = os.path.relpath(d[key], filepathRelTo)

        return d

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any], filepathRelTo: str,
                 session: Session | None = None) -> HeadModel:
        # TODO: validate against schema

        for key in ('filepath', 'skinSurfFilepath', 'gmSurfFilepath', 'freesurferFilepath'):
            if key in d and d[key] is not None:
                # convert to absolute paths
                d[key] = os.path.abspath(os.path.join(filepathRelTo, d[key]))
                if key == 'filepath':
                    cls.validateFilepath(d[key], strict=True)
                else:
                    assert os.path.exists(d[key]), f'File not found at {d[key]}'

        result = attrsWithNumpyFromDict(cls, d, npFields=('meshToMRITransform',))
        result.session = session
        return result

    @classmethod
    def validateFilepath(cls, filepath: tp.Optional[str], strict: bool = False) -> None:
        if filepath is None:
            return
        if strict:
            # make sure .msh actually exists
            assert filepath.endswith('.msh')
            assert os.path.exists(filepath), 'File not found at {}'.format(filepath)
            # TODO: also verify that expected related files (e.g. m2m_* folder) are next to the referenced .msh filepath
        else:
            # just make sure parent directory (typically SimNIBS results dir) actually exists
            parentDir = os.path.dirname(filepath)
            assert os.path.exists(parentDir), 'Parent directory not found at {}'.format(parentDir)
