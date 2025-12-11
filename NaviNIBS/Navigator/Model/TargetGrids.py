from __future__ import annotations

import shutil

import asyncio
import attrs
from datetime import datetime
import enum
import nibabel as nib
import json
import logging
import numpy as np
import os
import pandas as pd
import pytransform3d.rotations as ptr
import pyvista as pv
from skspatial.objects import Vector
import tempfile
import typing as tp
from typing import ClassVar, TYPE_CHECKING
import functools

from NaviNIBS.util import makeStrUnique
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.attrs import attrsAsDict
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.Transforms import applyTransform, invertTransform, composeTransform, concatenateTransforms, applyDirectionTransform, calculateRotationMatrixFromVectorToVector
from NaviNIBS.util.numpy import array_equalish, attrsWithNumpyAsDict, attrsWithNumpyFromDict
if TYPE_CHECKING:
    from NaviNIBS.Navigator.Model.Session import Session

from NaviNIBS.Navigator.Model.Targets import Target

from NaviNIBS.Navigator.Model.GenericCollection import GenericCollection, GenericCollectionDictItem, collectionDictItemAttrSetter
from NaviNIBS.Navigator.Model.Calculations import calculateAngleFromMidlineFromCoilToMRITransf, calculateCoilToMRITransfFromTargetEntryAngle, getClosestPointToPointOnMesh


logger = logging.getLogger(__name__)


class SpacingMethod(enum.StrEnum):
    COIL = 'Coil'
    ENTRY = 'Entry'
    TARGET = 'Target'


class EntryAngleMethod(enum.StrEnum):
    AUTOSET_ENTRY = 'Autoset entry'
    PIVOT_FROM_SEED = 'Pivot from seed'

class DepthMethod(enum.StrEnum):
    FROM_PIVOT = 'From pivot'
    FROM_SKIN = 'From skin'


@attrs.define
class TargetGrid(GenericCollectionDictItem[str]):
    """
    Define parameters used to create a grid of targets.
    Unspecified parameters may be set later before generating the actual target grid.
    """
    type: ClassVar[str] = 'TargetGrid'
    _seedTargetKey: str | None = attrs.field(default=None)
    _primaryAngle: float | None = attrs.field(default=0.)
    """
    Angle between seed target and grid major axis, in degrees.
    """
    _spacingAtDepth: SpacingMethod | None = attrs.field(default=SpacingMethod.TARGET)
    """
    At which depth to apply the specified spacing.
    """
    _entryAngleMethod: EntryAngleMethod | None = attrs.field(default=EntryAngleMethod.AUTOSET_ENTRY)
    """
    How to set the entry angle for each target in the grid.
    """
    _pivotDepth: float | None = attrs.field(default=None)
    """
    Depth from the seed target to the pivot point, in mm.
    """
    _depthMethod: DepthMethod | None = attrs.field(default=DepthMethod.FROM_SKIN)
    """
    How to set the depth (coil location) for each target in the grid.
    """
    _targetFormatStr: str | None = attrs.field(default=None)
    """
    Format string used to generate keys for targets in the grid.
    Will be passed the following keyword arguments:
    - gridKey: key of the target grid
    - seedTargetKey: key of the seed target
    - i: index of the target in the grid, starting at 1
    Subclasses may pass additional keyword arguments.
    If None, will use a default implementation.
    """
    _session: Session | None = attrs.field(default=None, repr=False)

    _autoGenerateOnChange: bool = True
    _generatedTargetKeys: list[str] = attrs.field(factory=list)
    """
    List of target keys generated from this grid. If the grid is regenerated, targets in this list
    will be removed and replaced with newly generated targets.
    If one of those targets is modified manually, it will be removed from this list to avoid being deleted.
    """

    _gridNeedsUpdate: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    _gridUpdateLoopTask: asyncio.Task | None = attrs.field(init=False, default=None, repr=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        for key in self._generatedTargetKeys:
            if self._session is not None and key in self._session.targets:
                self._session.targets[key].sigItemAboutToChange.connect(self._onGeneratedTargetAboutToChange)

        if len(self._generatedTargetKeys) == 0:
            self._setGridNeedsUpdate()
        else:
            # assume if there are generated target keys on init, then the grid is up to date
            # (note: this may result in a discrepency between grid parameters and generated targets
            # if parameters were changed while not connected to a session)
            pass

        if self.seedTarget is not None:
            self.seedTarget.sigItemChanged.connect(self._onSeedTargetItemChanged)

    def _setGridNeedsUpdate(self):
        self._gridNeedsUpdate.set()
        if self._autoGenerateOnChange and (self._gridUpdateLoopTask is None or self._gridUpdateLoopTask.done()):
            # only launch grid update loop after first request to update grid
            self._gridUpdateLoopTask = asyncio.create_task(asyncTryAndLogExceptionOnError(self._loop_updateGrid))

    async def _loop_updateGrid(self):
        while True:
            await self._gridNeedsUpdate.wait()
            await asyncio.sleep(0.1)  # rate-limit
            if not self._autoGenerateOnChange:
                continue
            self._gridNeedsUpdate.clear()
            self.generateTargets()

    def _onSeedTargetItemChanged(self, item: Target, attribsChanged: list[str] | None = None):

        if attribsChanged is not None and all(attrib in
                                              ('isVisible',
                                               'isSelected',
                                               'isHistorical',
                                               'mayBeADependency',
                                               'isSelected')
                                              for attrib in attribsChanged):
            # can ignore these changes
            return

        self._setGridNeedsUpdate()

    def _onGeneratedTargetAboutToChange(self, targetKey: str, attribNames: list[str] | None = None):
        if targetKey in self._generatedTargetKeys:
            if attribNames is not None:
                # ignore certain attribute changes that don't merit dropping the target from the grid
                ignoredAttribs = {'isVisible', 'mayBeADependency', 'isSelected'}
                if all(attribName in ignoredAttribs for attribName in attribNames):
                    return

            logger.debug(f'Target {targetKey} modified manually, removing from list of generated targets of grid {self.key}')
            self._generatedTargetKeys.remove(targetKey)
            self._session.targets[targetKey].sigItemAboutToChange.disconnect(self._onGeneratedTargetAboutToChange)

    def deleteAnyGeneratedTargets(self):
        if len(self._generatedTargetKeys) > 0:
            logger.debug(f'Deleting previous grid targets: {self._generatedTargetKeys}')
            self._session.targets.deleteItems([x for x in self._generatedTargetKeys if x in self._session.targets])
            self._generatedTargetKeys.clear()

    @property
    def canGenerateTargets(self) -> tuple[bool, str | None]:
        """
        Check if enough parameters are set to generate targets for the grid.

        Can be overridden by subclasses to add additional required parameters.

        Returns whether can generate and a string explaining what's missing if not
        """
        if self._session is None:
            return False, 'No session assigned'
        if self._seedTargetKey is None:
            return False, 'No seed target specified'
        if self._primaryAngle is None:
            return False, 'No primary angle specified'
        if self._spacingAtDepth is None:
            return False, 'No spacing at depth method specified'
        if self._entryAngleMethod is None:
            return False, 'No entry angle method specified'
        if self._pivotDepth is None:
            return False, 'No pivot depth specified'
        if self._depthMethod is None:
            return False, 'No depth method specified'
        return True, None

    def _generateTargets(self) -> list[Target]:
        """
        Generate new targets for the grid.

        Should not add new targets to the session or clear previous targets; that is handled by the parent class.
        """
        raise NotImplementedError  # to be implemented by subclass

    def generateTargets(self):
        """
        Regenerate the target grid, deleting any previously generated targets, and adding new targets to the session.
        """
        self._gridNeedsUpdate.clear()
        self.deleteAnyGeneratedTargets()
        canGenerate, reason = self.canGenerateTargets
        if not canGenerate:
            logger.warning(f'Cannot generate target grid {self.key}: {reason}')
            return
        logger.debug(f'Regenerating target grid {self.key}')
        newTargets = self._generateTargets()  # subclass implementation
        self._session.targets.merge(newTargets)
        for target in newTargets:
            self._generatedTargetKeys.append(target.key)
            target.sigItemAboutToChange.connect(self._onGeneratedTargetAboutToChange)
        logger.info(f'Regenerated target grid {self.key}, created {len(newTargets)} targets')

    @property
    def seedTargetKey(self) -> str | None:
        return self._seedTargetKey

    @seedTargetKey.setter
    @collectionDictItemAttrSetter()
    def seedTargetKey(self, key: str | None) -> None:
        # optional per-setter logic can go here; mark grid dirty after setter logic
        if self.seedTarget is not None:
            self.seedTarget.sigItemChanged.disconnect(self._onSeedTargetItemChanged)
        self._setGridNeedsUpdate()
        newSeedTarget = self._session.targets.get(key, None) if self._session is not None else None
        if newSeedTarget is not None:
            newSeedTarget.sigItemChanged.connect(self._onSeedTargetItemChanged)
    
    @property
    def seedTarget(self) -> Target | None:
        if self._session is None or self._seedTargetKey is None:
            return None
        return self._session.targets.get(self._seedTargetKey, None)

    @property
    def primaryAngle(self) -> float | None:
        return self._primaryAngle

    @primaryAngle.setter
    @collectionDictItemAttrSetter()
    def primaryAngle(self, angle: float | None) -> None:
        self._setGridNeedsUpdate()

    @property
    def spacingAtDepth(self) -> SpacingMethod | None:
        return self._spacingAtDepth

    @spacingAtDepth.setter
    @collectionDictItemAttrSetter()
    def spacingAtDepth(self, spacing: SpacingMethod | None) -> None:
        self._setGridNeedsUpdate()

    @property
    def entryAngleMethod(self) -> EntryAngleMethod | None:
        return self._entryAngleMethod

    @entryAngleMethod.setter
    @collectionDictItemAttrSetter()
    def entryAngleMethod(self, method: EntryAngleMethod | None) -> None:
        self._setGridNeedsUpdate()

    @property
    def pivotDepth(self) -> float | None:
        return self._pivotDepth

    @pivotDepth.setter
    @collectionDictItemAttrSetter()
    def pivotDepth(self, depth: float | None) -> None:
        self._setGridNeedsUpdate()

    @property
    def depthMethod(self) -> DepthMethod | None:
        return self._depthMethod

    @depthMethod.setter
    @collectionDictItemAttrSetter()
    def depthMethod(self, method: DepthMethod | None) -> None:
        self._setGridNeedsUpdate()

    @property
    def autoGenerateOnChange(self):
        return self._autoGenerateOnChange

    @autoGenerateOnChange.setter
    @collectionDictItemAttrSetter()
    def autoGenerateOnChange(self, doAuto: bool) -> None:
        if doAuto:
            if self._gridNeedsUpdate.is_set():
                self._setGridNeedsUpdate()  # start loop if needed

    @property
    def targetFormatStr(self) -> str | None:
        return self._targetFormatStr

    @targetFormatStr.setter
    @collectionDictItemAttrSetter()
    def targetFormatStr(self, fmt: str | None) -> None:
        logger.debug(f'Setting target format string to: {fmt}')
        self._setGridNeedsUpdate()

    @property
    def defaultTargetFormatStr(self):
        return '{seedTargetKey} grid point {i}'

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, session: Session):
        if self._session is session:
            return

        if self.seedTarget is not None:
            self.seedTarget.sigItemChanged.disconnect(self._onSeedTargetItemChanged)

        for key in self._generatedTargetKeys:
            if self._session is not None and key in self._session.targets:
                self._session.targets[key].sigItemAboutToChange.disconnect(self._onGeneratedTargetAboutToChange)

        self._session = session

        for key in self._generatedTargetKeys:
            if self._session is not None and key in self._session.targets:
                self._session.targets[key].sigItemAboutToChange.connect(self._onGeneratedTargetAboutToChange)

        if self.seedTarget is not None:
            self.seedTarget.sigItemChanged.connect(self._onSeedTargetItemChanged)

        self._setGridNeedsUpdate()

    @property
    def numGeneratedTargets(self):
        """
        Note: this will not include any targets that were manually modified and thus removed from the generated list.
        """
        return len(self._generatedTargetKeys)

    def asDict(self) -> dict[str, tp.Any]:
        d = attrsAsDict(self, exclude=['session'])
        return {'type': self.type, **d}


@attrs.define
class CartesianTargetGrid(TargetGrid):
    """
    More specific target grid with Cartesian spacing.

    targetFormatStr will be passed the following keyword arguments in addition to those in TargetGrid:
    - iX: index along the X axis
    - iY: index along the Y axis
    - iA: index of the angle
    """
    type: ClassVar[str] = 'CartesianTargetGrid'
    _xWidth: float | None = attrs.field(default=None)
    _yWidth: float | None = attrs.field(default=None)
    _xN: int | None = attrs.field(default=None)
    _yN: int | None = attrs.field(default=None)
    _angleSpan: tuple[float, float] | None = attrs.field(
        default=None, converter=attrs.converters.optional(tuple))
    _angleN: int | None = attrs.field(default=None)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @property
    def canGenerateTargets(self) -> tuple[bool, str]:
        canGenerate, reason = super().canGenerateTargets
        if not canGenerate:
            return False, reason
        if self._xWidth is not None and self._xN is not None:
            return True, None
        if self._yWidth is not None and self._yN is not None:
            return True, None
        if self._angleSpan is not None and self._angleN is not None:
            return True, None
        return False, 'At least one of X, Y, or angle spacing parameters must be specified'

    def _generateTargets(self) -> list[Target]:
        assert self.canGenerateTargets

        match self._spacingAtDepth:
            case SpacingMethod.COIL:
                refOrigin = self.seedTarget.entryCoordPlusDepthOffset
                refDepthFromSeedCoil = 0.
                refDepthFromSeedEntry = self.seedTarget.depthOffset
                refDepthFromSeedTarget = np.linalg.norm(
                    self.seedTarget.entryCoord - self.seedTarget.targetCoord) + self.seedTarget.depthOffset
            case SpacingMethod.ENTRY:
                refOrigin = self.seedTarget.entryCoord
                refDepthFromSeedCoil = -self.seedTarget.depthOffset
                refDepthFromSeedEntry = 0.
                refDepthFromSeedTarget = np.linalg.norm(self.seedTarget.entryCoord - self.seedTarget.targetCoord)
            case SpacingMethod.TARGET:
                refOrigin = self.seedTarget.targetCoord
                refDepthFromSeedCoil = -np.linalg.norm(
                    self.seedTarget.entryCoord - self.seedTarget.targetCoord) - self.seedTarget.depthOffset
                refDepthFromSeedEntry = -np.linalg.norm(self.seedTarget.entryCoord - self.seedTarget.targetCoord)
                refDepthFromSeedTarget = 0.
            case _:
                raise NotImplementedError

        seedCoilToMRITransf = self.seedTarget.coilToMRITransf
        seedCoilToUnrotSeedCoil = composeTransform(
            ptr.active_matrix_from_angle(2, -np.deg2rad(self._primaryAngle)))
        gridSpaceToSeedCoilTransf = composeTransform(
            ptr.active_matrix_from_angle(2, np.deg2rad(self._primaryAngle)),  # TODO: check sign of angle
            np.asarray([0, 0, refDepthFromSeedCoil]))
        gridSpaceToMRITransf = concatenateTransforms([gridSpaceToSeedCoilTransf, seedCoilToMRITransf])

        entryDir = Vector(self.seedTarget.entryCoord - self.seedTarget.targetCoord).unit()

        gridNX = self._xN if self._xN is not None else 1
        gridNY = self._yN if self._yN is not None else 1
        gridNAngle = self._angleN if self._angleN is not None else 1

        gridHandleAngleStart, gridHandleAngleStop = (self._angleSpan if self._angleSpan is not None
                                                     else (0., 0.))

        entryMode = self._entryAngleMethod
        if entryMode == EntryAngleMethod.AUTOSET_ENTRY and gridNX == 1 and gridNY == 1:
            # angle variation only, ignore specified entry mode and just match seed instead
            logger.info(f'Single-point grid with autoset entry: matching seed target entry angle')
            entryMode = EntryAngleMethod.PIVOT_FROM_SEED

        pivotDepth = self._pivotDepth  # in mm, dist from seed at grid depth to "common" origin

        # extraTransf = np.eye(4)
        # extraTransf[:3, 3] = refDepthFromSeedCoil
        # pivotOrigin_MRISpace = applyTransform(
        #     concatenateTransforms((extraTransf, self.seedTarget.coilToMRITransf)),
        #     np.asarray([0, 0, -pivotDepth]))

        # TODO: update grid spacing below to be defined based on arc lengths around pivot origin, instead of in a 2D plane

        # note: even in 'Autoset entry' mode, we use pivot to define target locations
        # (so that large offsets don't cause as large of depth changes)

        totalThetaX = (self._xWidth or 0.) / pivotDepth
        assert totalThetaX < 2 * np.pi, 'Grid width too large for given pivot depth'

        totalThetaY = (self._yWidth or 0.)/ pivotDepth
        assert totalThetaY < 2 * np.pi, 'Grid width too large for given pivot depth'

        if gridNX == 1:
            thetaXs = [0.]
        else:
            thetaXs = np.linspace(-totalThetaX / 2, totalThetaX / 2, gridNX)
        if gridNY == 1:
            thetaYs = [0.]
        else:
            thetaYs = np.linspace(-totalThetaY / 2, totalThetaY / 2, gridNY)
        if gridNAngle == 1:
            aCoords_seed = [np.mean([gridHandleAngleStart, gridHandleAngleStop])]
        else:
            aCoords_seed = np.linspace(gridHandleAngleStart, gridHandleAngleStop, gridNAngle)

        numPoints = gridNX * gridNY * gridNAngle
        newToGridSpaceTransfs = np.full((numPoints, 4, 4), np.nan)
        gridHandleAngles = np.full((numPoints,), np.nan)

        for iX, thetaX in enumerate(thetaXs):
            for iY, thetaY in enumerate(thetaYs):
                transf_gridSpaceToPivot = np.eye(4)
                transf_gridSpaceToPivot[2, 3] = pivotDepth

                rot = ptr.matrix_from_euler((-thetaX, -thetaY, 0), 1, 0, 1, extrinsic=True)
                transf_pivotToPivoted = composeTransform(rot)

                transf_pivotedToNewUnrot = np.eye(4)
                transf_pivotedToNewUnrot[2, 3] = -pivotDepth

                for iA, aCoord in enumerate(aCoords_seed):
                    transf_newUnrotToNew = concatenateTransforms([
                        invertTransform(seedCoilToUnrotSeedCoil),
                        composeTransform(ptr.active_matrix_from_angle(2, -np.deg2rad(aCoord))),
                        # TODO: check sign of angle
                    ])

                    newToGridSpaceTransfs[iX * gridNY * gridNAngle + iY * gridNAngle + iA] = invertTransform(
                        concatenateTransforms(
                            [transf_gridSpaceToPivot, transf_pivotToPivoted, transf_pivotedToNewUnrot,
                             transf_newUnrotToNew]))

                    gridHandleAngles[iX * gridNY * gridNAngle + iY * gridNAngle + iA] = aCoord

        newToMRISpaceTransfs = np.full((numPoints, 4, 4), np.nan)
        for i in range(numPoints):
            newToMRISpaceTransfs[i] = concatenateTransforms([newToGridSpaceTransfs[i], gridSpaceToMRITransf])

        newCoilToNewTransf = np.eye(4)
        newCoilToNewTransf[
            2, 3] = -refDepthFromSeedCoil  # before any additional depth correction (e.g. before matching to new skin depth)

        newEntryToNewTransf = np.eye(4)
        newEntryToNewTransf[2, 3] = -refDepthFromSeedEntry

        newTargetToNewTransf = np.eye(4)
        newTargetToNewTransf[2, 3] = -refDepthFromSeedTarget

        targets = []

        for i in range(numPoints):
            # TODO: make the baseStr formatter configurable in GUI and grid templates
            formatStr = self.targetFormatStr or self.defaultTargetFormatStr
            uniqueTargetKey = makeStrUnique(baseStr=formatStr.format(
                gridKey=self.key,
                seedTargetKey=self.seedTargetKey,
                i=i + 1,
                iX=(i // (gridNY * gridNAngle)) % gridNX + 1,
                iY=(i // gridNAngle) % gridNY + 1,
                iA=(i % gridNAngle) + 1,
            ), existingStrs=self._session.targets.keys(),
                delimiter='#')

            newCoilToMRITransf = concatenateTransforms([newCoilToNewTransf, newToMRISpaceTransfs[i]])
            entryCoord_MRISpace = applyTransform((newEntryToNewTransf, newToMRISpaceTransfs[i]), np.asarray([0, 0, 0]))
            targetCoord_MRISpace = applyTransform((newTargetToNewTransf, newToMRISpaceTransfs[i]),
                                                  np.asarray([0, 0, 0]))

            newTarget = Target(
                session=self._session,
                coilToMRITransf=newCoilToMRITransf,
                targetCoord=targetCoord_MRISpace,
                entryCoord=entryCoord_MRISpace,
                depthOffset=self.seedTarget.depthOffset,
                key=uniqueTargetKey,
                angle=self.seedTarget.angle + gridHandleAngles[i],
                # TODO: check sign of offset, and note that this is approximate due to pivot angles
                color=self.seedTarget.color,
            )

            match entryMode:
                case EntryAngleMethod.PIVOT_FROM_SEED:
                    pass  # no additional adjustment needed, since grid points were defined based on pivot

                case EntryAngleMethod.AUTOSET_ENTRY:
                    # set entry based on closest point on skin along entry direction
                    closestPt_skin_seed = getClosestPointToPointOnMesh(
                        session=self._session,
                        whichMesh='skinConvexSurf',
                        point_MRISpace=self.seedTarget.entryCoord)
                    # signed offset from closest skin point along entry direction
                    seedEntryToSkinOffset = Vector(self.seedTarget.entryCoord - closestPt_skin_seed).dot(
                        entryDir)  # TODO: check sign
                    newTarget.autosetEntryCoord(offsetFromSkin=seedEntryToSkinOffset)

            logger.debug(f'New target: {newTarget}')
            targets.append(newTarget)

        return targets


    @property
    def xWidth(self):
        return self._xWidth

    @xWidth.setter
    @collectionDictItemAttrSetter()
    def xWidth(self, width: float | None) -> None:
        self._setGridNeedsUpdate()

    @property
    def yWidth(self) -> float | None:
        return self._yWidth

    @yWidth.setter
    @collectionDictItemAttrSetter()
    def yWidth(self, width: float | None) -> None:
        self._setGridNeedsUpdate()

    @property
    def xN(self) -> int | None:
        return self._xN

    @xN.setter
    @collectionDictItemAttrSetter()
    def xN(self, n: int | None) -> None:
        self._setGridNeedsUpdate()

    @property
    def yN(self) -> int | None:
        return self._yN

    @yN.setter
    @collectionDictItemAttrSetter()
    def yN(self, n: int | None) -> None:
        self._setGridNeedsUpdate()

    @property
    def angleSpan(self) -> tuple[float, float] | None:
        return self._angleSpan

    @angleSpan.setter
    @collectionDictItemAttrSetter()
    def angleSpan(self, span: tuple[float, float] | None) -> None:
        self._setGridNeedsUpdate()

    @property
    def angleN(self) -> int | None:
        return self._angleN

    @angleN.setter
    @collectionDictItemAttrSetter()
    def angleN(self, n: int | None) -> None:
        self._setGridNeedsUpdate()

    @property
    def defaultTargetFormatStr(self):
         if self._angleN is not None and self._angleN > 1:
            return '{seedTargetKey} grid x{iX} y{iY} θ{iA}'
         else:
            return '{seedTargetKey} grid x{iX} y{iY}'


@attrs.define
class TargetGrids(GenericCollection[str, TargetGrid]):
    _session: Session | None = attrs.field(default=None, repr=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        self.sigItemsChanged.connect(self._setSessionOnItemsChanged)

    def _setSessionOnItemsChanged(self, keys: list[str], attribNames: list[str] | None = None):
        if attribNames is None:
            for key in keys:
                if key in self:
                    self[key].session = self._session

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, session: Session):
        if self._session is not session:
            self._session = session
            self._setSessionOnItemsChanged(self.keys())

    @classmethod
    def gridFromDict(cls, gridDict: dict[str, TargetGrid], session: Session | None = None) -> TargetGrid:
        gridDict = gridDict.copy()
        gridType = gridDict.pop('type')
        match gridType:
            case CartesianTargetGrid.type:
                gridCls = CartesianTargetGrid
            case _:
                raise NotImplementedError

        grid = gridCls(**gridDict, session=session)
        return grid

    @classmethod
    def fromList(cls, gridList: list[dict[str, tp.Any]], session: Session | None = None) -> TargetGrids:

        grids = {}
        for gridDict in gridList:
            grid = cls.gridFromDict(gridDict, session=session)
            grids[grid.key] = grid

        return cls(items=grids)