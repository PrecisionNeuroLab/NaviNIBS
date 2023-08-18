from __future__ import annotations
import attrs
import logging
import nibabel as nib
import nitransforms as nit
import numpy as np
import os
import typing as tp

if tp.TYPE_CHECKING:
    from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.util.attrs import attrsAsDict
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import applyTransform, invertTransform

from RTNaBS.Navigator.Model.GenericCollection import GenericCollection, GenericCollectionDictItem

logger = logging.getLogger(__name__)


@attrs.define
class CoordinateSystem(GenericCollectionDictItem[str]):
    _description: str = ''
    _isVisible: bool = True
    _isAutogenerated: bool = False
    _session: tp.Optional[Session] = attrs.field(default=None, repr=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @property
    def description(self):
        return self._description

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newSes: tp.Optional[Session]):
        self.sigItemAboutToChange.emit(self._key)
        self._session = newSes
        self.sigItemChanged.emit(self._key)

    @property
    def isVisible(self):
        return self._isVisible

    @property
    def isAutogenerated(self):
        return self._isAutogenerated

    def transformFromWorldToThis(self, coords: np.ndarray) -> np.ndarray:
        raise NotImplementedError  # should be implemented by subclass

    def transformFromThisToWorld(self, coords: np.ndarray) -> np.ndarray:
        raise NotImplementedError  # should be implemented by subclass

    def asDict(self):
        return attrsAsDict(self, exclude=['session'])


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


@attrs.define(kw_only=True)
class NonlinearTransformedCoordinateSystem(CoordinateSystem):
    _deformationFieldThisToWorld_filepath: str = None
    _deformationFieldWorldToThis_filepath: str = None

    _deformationFieldThisToWorld: tp.Optional[nib.Nifti1Image] = attrs.field(init=False, default=None)
    _deformationFieldWorldToThis: tp.Optional[nib.Nifti1Image] = attrs.field(init=False, default=None)

    _transfThisToWorld: tp.Optional[nit.nonlinear.DenseFieldTransform] = attrs.field(init=False, default=None)
    _transfWorldToThis: tp.Optional[nit.nonlinear.DenseFieldTransform] = attrs.field(init=False, default=None)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        self.sigItemChanged.connect(lambda *args, **kwargs: self.clearCache())

    @property
    def deformationFieldThisToWorld(self):
        if self._deformationFieldThisToWorld is None:
            logger.info(f'Loading deformation field from {self._deformationFieldThisToWorld_filepath}')
            self._deformationFieldThisToWorld = nib.load(self._deformationFieldThisToWorld_filepath)
        return self._deformationFieldThisToWorld

    @property
    def deformationFieldWorldToThis(self):
        if self._deformationFieldWorldToThis is None:
            logger.info(f'Loading deformation field from {self._deformationFieldWorldToThis_filepath}')
            self._deformationFieldWorldToThis = nib.load(self._deformationFieldWorldToThis_filepath)
        return self._deformationFieldWorldToThis

    @property
    def transfThisToWorld(self):
        if self._transfThisToWorld is None:
            self._transfThisToWorld = nit.nonlinear.DenseFieldTransform(
                field=self.deformationFieldThisToWorld,
                is_deltas=False)  # TODO: determine whether also need to specify reference arg
        return self._transfThisToWorld

    @property
    def transfWorldToThis(self):
        if self._transfWorldToThis is None:
            self._transfWorldToThis = nit.nonlinear.DenseFieldTransform(
                field=self.deformationFieldWorldToThis,
                is_deltas=False)  # TODO: determine whether also need to specify reference arg
        return self._transfWorldToThis

    def clearCache(self):
        self._deformationFieldThisToWorld = None
        self._deformationFieldWorldToThis = None
        self._transfThisToWorld = None
        self._transfWorldToThis = None

    def transformFromWorldToThis(self, coords: np.ndarray) -> np.ndarray:
        return self.transfWorldToThis.map(coords)

    def transformFromThisToWorld(self, coords: np.ndarray) -> np.ndarray:
        return self.transfThisToWorld.map(coords)


@attrs.define
class CoordinateSystems(GenericCollection[str, CoordinateSystem]):
    _session: tp.Optional[Session] = attrs.field(default=None, repr=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        if self.session is not None:
            self.session.headModel.sigFilepathChanged.connect(self.autogenerateCoordinateSystems)
            self.autogenerateCoordinateSystems()

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newSes: tp.Optional[Session]):
        if self._session is newSes:
            return
        if self._session is not None:
            self.session.headModel.sigFilepathChanged.disconnect(self.autogenerateCoordinateSystems)
        self.setAttribForItems(self.keys(), dict(session=[newSes for key in self.keys()]))
        self._session = newSes
        if newSes is not None:
            newSes.headModel.sigFilepathChanged.connect(self.autogenerateCoordinateSystems)
        self.autogenerateCoordinateSystems()

    def autogenerateCoordinateSystems(self):
        if self.session is not None:
            if self.session.headModel.filepath is not None:
                # autogenerate from files generated by SimNIBS
                logger.info('Autogenerating coord system transforms from SimNIBS results')

                parentDir = os.path.dirname(self.session.headModel.filepath)  # simnibs results dir
                subStr = os.path.splitext(os.path.basename(self.session.headModel.filepath))[0]  # e.g. 'sub-1234'
                m2mDir = os.path.join(parentDir, 'm2m_' + subStr)

                inputPath = os.path.join(m2mDir, 'toMNI', 'MNI2conform_12DOF.txt')
                if os.path.exists(inputPath):
                    with open(inputPath, 'r') as f:
                        MNIToMRITransf = np.loadtxt(f)

                    coordSys = AffineTransformedCoordinateSystem(
                        key='MNI_SimNIBS12DoF',
                        description='SimNIBS-generated 12DoF MNI transform',
                        transfThisToWorld=MNIToMRITransf,
                        isAutogenerated=True
                    )
                    assert coordSys.key not in self._items
                    self.setItem(coordSys)
                else:
                    logger.error(f'Did not find expected input file at {inputPath}')

                inputPath = os.path.join(m2mDir, 'toMNI', 'MNI2Conform_nonl.nii')
                if os.path.exists(inputPath):
                    inputPath2 = os.path.join(m2mDir, 'toMNI', 'Conform2MNI_nonl.nii')
                    if os.path.exists(inputPath2):
                        coordSys = NonlinearTransformedCoordinateSystem(
                            key='MNI_SimNIBSNonlinear',
                            description='SimNIBS-generated nonlinear MNI transform',
                            deformationFieldThisToWorld_filepath=inputPath,
                            deformationFieldWorldToThis_filepath=inputPath2,
                            isAutogenerated=True
                        )
                        assert coordSys.key not in self._items
                        self.setItem(coordSys)
                    else:
                        logger.error(f'Did not find expected input file at {inputPath2}')
                else:
                    logger.error(f'Did not find expected input file at {inputPath}')

    def asList(self) -> list[dict[str, tp.Any]]:
        # (don't include auto-generated coord systems since they can be restored separately)
        return [coordSys.asDict() for coordSys in self._items.values() if not coordSys.isAutogenerated]

    @classmethod
    def fromList(cls, coordSysList: list[dict[str, tp.Any]]) -> CoordinateSystems:
        coordinateSystems = {}
        for coordSysDict in coordSysList:
            match coordSysDict['__type']:
                case 'AffineTransformedCoordinateSystem':
                    CoordSysCls = AffineTransformedCoordinateSystem
                case 'NonlinearTransformedCoordinateSystem':
                    CoordSysCls = NonlinearTransformedCoordinateSystem
                case _:
                    raise NotImplementedError("Unexpected CoordinateSystem type: {coordSysDict['__type']}")

            coordinateSystems[coordSysDict['key']] = CoordSysCls.fromDict(coordSysDict)

        return cls(items=coordinateSystems)
