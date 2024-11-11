import asyncio
import attrs
import json
import logging
import numpy as np
import pytransform3d.rotations as ptr
from qtpy import QtWidgets, QtGui, QtCore
from skspatial.objects import Vector
import typing as tp

from NaviNIBS.Navigator.Model.Targets import Target
from NaviNIBS.Navigator.GUI.CollectionModels.TargetsTableModel import TargetsTableModel, FullTargetsTableModel
from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.Navigator.Model.Calculations import getClosestPointToPointOnMesh, calculateCoilToMRITransfFromTargetEntryAngle
from NaviNIBS.util import makeStrUnique
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.Transforms import applyTransform, invertTransform, composeTransform, concatenateTransforms, applyDirectionTransform, calculateRotationMatrixFromVectorToVector
from NaviNIBS.util.GUI.QDial import AngleDial
from NaviNIBS.util.GUI.QScrollContainer import QScrollContainer
from NaviNIBS.util.GUI.QMouseWheelAdjustmentGuard import preventAnnoyingScrollBehaviour

logger = logging.getLogger(__name__)


@attrs.define(kw_only=True)
class EditGridWidget:
    _session: Session = attrs.field(repr=False)

    _wdgt: QtWidgets.QWidget = attrs.field(factory=lambda: QtWidgets.QGroupBox('Edit grid'))
    _scroll: QScrollContainer = attrs.field(init=False)

    _targetComboBox: QtWidgets.QComboBox = attrs.field(init=False, factory=QtWidgets.QComboBox)
    _targetsModel: FullTargetsTableModel = attrs.field(init=False)
    _preChangeTargetComboBoxIndex: list[int] = attrs.field(init=False, factory=list)

    _gridPrimaryAngleWdgt: AngleDial = attrs.field(init=False)

    _gridSpacingAtDepthWdgt: QtWidgets.QComboBox = attrs.field(init=False, factory=QtWidgets.QComboBox)
    _gridPivotDepth: QtWidgets.QDoubleSpinBox = attrs.field(init=False, factory=QtWidgets.QDoubleSpinBox)
    _gridDepthMethodWdgt: QtWidgets.QComboBox = attrs.field(init=False, factory=QtWidgets.QComboBox)
    _gridEntryAngleMethodWdgt: QtWidgets.QComboBox = attrs.field(init=False, factory=QtWidgets.QComboBox)

    _seedTarget: Target | None = attrs.field(init=False, default=None)

    _disableWidgetsWhenNoTarget: list[QtWidgets.QWidget] = attrs.field(init=False, factory=list)

    _gridWidthWdgts: tuple[QtWidgets.QDoubleSpinBox, QtWidgets.QDoubleSpinBox] = attrs.field(init=False)
    _gridNWdgts: tuple[QtWidgets.QSpinBox, QtWidgets.QSpinBox] = attrs.field(init=False)

    _gridHandleAngleWdgts: tuple[AngleDial, AngleDial] = attrs.field(init=False)
    _gridHandleAngleNWdgt: QtWidgets.QSpinBox = attrs.field(init=False)

    _pendingGridTargetKeys: list[str] = attrs.field(init=False, factory=list)

    _gridNeedsUpdate: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    def __attrs_post_init__(self):
        outerLayout = QtWidgets.QVBoxLayout()
        self._wdgt.setLayout(outerLayout)
        outerLayout.setContentsMargins(0, 0, 0, 0)

        layout = QtWidgets.QFormLayout()
        self._scroll = QScrollContainer(innerContainerLayout=layout)
        self._scroll.scrollArea.setFrameShape(QtWidgets.QFrame.NoFrame)
        outerLayout.addWidget(self._scroll.scrollArea)

        self._scroll.scrollArea.setSizePolicy(QtWidgets.QSizePolicy.Minimum,
                                              QtWidgets.QSizePolicy.Preferred)

        self._targetsModel = FullTargetsTableModel(session=self._session)
        self._targetComboBox.setModel(self._targetsModel)
        if True:
            # because setting targets from empty to non-empty also sets current index to 0, need to monitor collection directly to block this
            self._targetsModel.collection.sigItemsAboutToChange.connect(
                self._onTargetsCollectionAboutToChange, priority=1)
            self._targetsModel.collection.sigItemsChanged.connect(
                self._onTargetsCollectionChanged, priority=-1)
        self._targetComboBox.setCurrentIndex(-1)
        self._targetComboBox.currentIndexChanged.connect(self._onTargetComboBoxCurrentIndexChanged)
        layout.addRow('Seed target:', self._targetComboBox)

        # this "primary angle" defines angle of grid X axis relative to seed target's coil X axis
        self._gridPrimaryAngleWdgt = AngleDial(
            centerAngle=0,
            offsetAngle=-90,
            doInvert=True
        )
        self._gridPrimaryAngleWdgt.sigValueChanged.connect(self._onGridPrimaryAngleChanged)
        layout.addRow('Grid angle offset:', self._gridPrimaryAngleWdgt.wdgt)
        self._disableWidgetsWhenNoTarget.append(self._gridPrimaryAngleWdgt.wdgt)

        self._gridSpacingAtDepthWdgt.addItems(['Coil', 'Entry', 'Target'])
        self._gridSpacingAtDepthWdgt.setCurrentIndex(2)
        self._gridSpacingAtDepthWdgt.currentIndexChanged.connect(self._onGridSpacingAtDepthChanged)
        layout.addRow('Grid at depth of:', self._gridSpacingAtDepthWdgt)
        self._disableWidgetsWhenNoTarget.append(self._gridSpacingAtDepthWdgt)

        self._gridPivotDepth.setRange(1, 1000)
        self._gridPivotDepth.setSingleStep(1)
        self._gridPivotDepth.setDecimals(1)
        self._gridPivotDepth.setSuffix(' mm')
        self._gridPivotDepth.setValue(60.)
        self._gridPivotDepth.valueChanged.connect(self._onGridPivotDepthChanged)
        # self._gridPivotDepth.setKeyboardTracking(False)
        layout.addRow('Grid pivot depth:', self._gridPivotDepth)
        self._disableWidgetsWhenNoTarget.append(self._gridPivotDepth)

        self._gridDepthMethodWdgt.addItems(['from pivot', 'from skin'])
        self._gridDepthMethodWdgt.setCurrentIndex(1)
        self._gridDepthMethodWdgt.currentIndexChanged.connect(self._onGridDepthMethodChanged)
        layout.addRow('Grid depth adjustment:', self._gridDepthMethodWdgt)
        self._disableWidgetsWhenNoTarget.append(self._gridDepthMethodWdgt)

        self._gridWidthWdgts = (
            QtWidgets.QDoubleSpinBox(),
            QtWidgets.QDoubleSpinBox()
        )

        self._gridNWdgts = (
            QtWidgets.QSpinBox(),
            QtWidgets.QSpinBox()
        )

        for iXY, (gridWidthWdgt, gridNWdgt) in enumerate(zip(self._gridWidthWdgts, self._gridNWdgts)):
            preventAnnoyingScrollBehaviour(gridWidthWdgt)
            # gridWidthWdgt.setKeyboardTracking(False)
            gridWidthWdgt.setRange(0, 1000)
            gridWidthWdgt.setSingleStep(1)
            gridWidthWdgt.setDecimals(1)
            gridWidthWdgt.setSuffix(' mm')
            gridWidthWdgt.setValue(20.)
            gridWidthWdgt.valueChanged.connect(self._onGridWidthChanged)
            layout.addRow(f'Grid width {"XY"[iXY]}:', gridWidthWdgt)
            self._disableWidgetsWhenNoTarget.append(gridWidthWdgt)

            preventAnnoyingScrollBehaviour(gridNWdgt)
            # gridNWdgt.setKeyboardTracking(False)
            gridNWdgt.setRange(1, 1000)
            gridNWdgt.setSingleStep(1)
            gridNWdgt.valueChanged.connect(self._onGridNChanged)
            layout.addRow(f'Grid N {"XY"[iXY]}:', gridNWdgt)
            self._disableWidgetsWhenNoTarget.append(gridNWdgt)

        # noinspection PyTypeChecker
        self._gridHandleAngleWdgts = tuple(
            AngleDial(centerAngle=0,
                      offsetAngle=-90,
                      doInvert=True) for _ in range(2))
        for iAngle, (angleWdgt, angleLabel) in enumerate(zip(self._gridHandleAngleWdgts, ('start', 'end'))):
            angleWdgt.sigValueChanged.connect(self._onGridHandleAngleSpanChanged)
            layout.addRow(f'Handle angle {angleLabel}', angleWdgt.wdgt)
            self._disableWidgetsWhenNoTarget.append(angleWdgt.wdgt)

        self._gridHandleAngleNWdgt = QtWidgets.QSpinBox()
        self._gridHandleAngleNWdgt.setRange(1, 1000)
        self._gridHandleAngleNWdgt.setSingleStep(1)
        self._gridHandleAngleNWdgt.valueChanged.connect(self._onGridNChanged)
        layout.addRow(f'Grid handle angle N', self._gridHandleAngleNWdgt)
        self._disableWidgetsWhenNoTarget.append(self._gridHandleAngleNWdgt)

        btnContainer = QtWidgets.QWidget()
        btnLayout = QtWidgets.QHBoxLayout()
        btnContainer.setLayout(btnLayout)
        btnLayout.setContentsMargins(0, 0, 0, 0)

        cancelBtn = QtWidgets.QPushButton('Cancel')
        cancelBtn.clicked.connect(self._onCancelBtnClicked)
        btnLayout.addWidget(cancelBtn)
        finishBtn = QtWidgets.QPushButton('Finish')
        finishBtn.clicked.connect(self._onFinishBtnClicked)
        btnLayout.addWidget(finishBtn)
        layout.addWidget(btnContainer)
        self._disableWidgetsWhenNoTarget.extend([cancelBtn, finishBtn])

        for wdgt in self._disableWidgetsWhenNoTarget:
            wdgt.setEnabled(self._seedTarget is not None)

        asyncio.create_task(asyncTryAndLogExceptionOnError(self._loop_updateGrid))

        self._gridNeedsUpdate.set()

    @property
    def wdgt(self):
        return self._wdgt

    @property
    def seedTarget(self):
        return self._seedTarget

    @seedTarget.setter
    def seedTarget(self, newTarget: Target | None):
        if self._seedTarget is newTarget:
            return

        if self._seedTarget is not None:
            self._seedTarget.sigItemChanged.disconnect(self._onSeedTargetItemChanged)

        self._deleteAnyPendingGridTargets()

        self._seedTarget = newTarget

        if self._seedTarget is not None:
            self._seedTarget.sigItemChanged.connect(self._onSeedTargetItemChanged)

        for wdgt in self._disableWidgetsWhenNoTarget:
            wdgt.setEnabled(self._seedTarget is not None)

        if self._seedTarget is not None:
            self._gridNeedsUpdate.set()

    async def _loop_updateGrid(self):
        while True:
            await self._gridNeedsUpdate.wait()
            await asyncio.sleep(0.1)  # rate-limit
            self._gridNeedsUpdate.clear()
            self._regenerateGrid()

    def _onTargetsCollectionAboutToChange(self, *args, **kwargs):
        if len(self._preChangeTargetComboBoxIndex) == 0:
            # don't respond to combo box index changes during a targets change,
            # since if the starting selection is empty it will force reset to non-empty
            self._targetComboBox.currentIndexChanged.disconnect(
                self._onTargetComboBoxCurrentIndexChanged)
        self._preChangeTargetComboBoxIndex.append(self._targetComboBox.currentIndex())

    def _onTargetsCollectionChanged(self, *args, **kwargs):
        if len(self._preChangeTargetComboBoxIndex) > 0:
            # one a change is complete, restore previous index
            # if it was empty, and respond to any other change
            prevIndex = self._preChangeTargetComboBoxIndex.pop()

            if len(self._preChangeTargetComboBoxIndex) == 0:
                if prevIndex == -1:
                    self._targetComboBox.setCurrentIndex(-1)
                elif prevIndex != self._targetComboBox.currentIndex():
                    self._onTargetComboBoxCurrentIndexChanged(self._targetComboBox.currentIndex())

                self._targetComboBox.currentIndexChanged.connect(
                    self._onTargetComboBoxCurrentIndexChanged)

    def _deleteAnyPendingGridTargets(self):
        if len(self._pendingGridTargetKeys) > 0:
            logger.debug(f'Deleting pending grid targets: {self._pendingGridTargetKeys}')
            self._session.targets.deleteItems(self._pendingGridTargetKeys)
            self._pendingGridTargetKeys.clear()

    def _regenerateGrid(self):
        logger.debug('Regenerating grid')
        # TODO: instead of deleting everything and recreating, edit / repurpose existing targets
        self._deleteAnyPendingGridTargets()

        #return  # TODO: debug, delete

        if self._seedTarget is None:
            return

        logger.info(f'Generating grid for seedTarget {self._seedTarget.key}')

        gridWidthX, gridWidthY = (wdgt.value() for wdgt in self._gridWidthWdgts)

        gridNX, gridNY = (wdgt.value() for wdgt in self._gridNWdgts)

        gridHandleAngleStart, gridHandleAngleStop = (wdgt.value for wdgt in self._gridHandleAngleWdgts)

        gridNAngle = self._gridHandleAngleNWdgt.value()

        depthMode = self._gridSpacingAtDepthWdgt.currentText()
        match depthMode:
            case 'Coil':
                refOrigin = self._seedTarget.entryCoordPlusDepthOffset
                refDepthFromSeedCoil = 0.
                refDepthFromSeedEntry = self._seedTarget.depthOffset
                refDepthFromSeedTarget = np.linalg.norm(self._seedTarget.entryCoord - self._seedTarget.targetCoord) + self._seedTarget.depthOffset
            case 'Entry':
                refOrigin = self._seedTarget.entryCoord
                refDepthFromSeedCoil = -self._seedTarget.depthOffset
                refDepthFromSeedEntry = 0.
                refDepthFromSeedTarget = np.linalg.norm(self._seedTarget.entryCoord - self._seedTarget.targetCoord)
            case 'Target':
                refOrigin = self._seedTarget.targetCoord
                refDepthFromSeedCoil = -np.linalg.norm(self._seedTarget.entryCoord - self._seedTarget.targetCoord) - self.seedTarget.depthOffset
                refDepthFromSeedEntry = -np.linalg.norm(self._seedTarget.entryCoord - self._seedTarget.targetCoord)
                refDepthFromSeedTarget = 0.
            case _:
                raise NotImplementedError

        seedCoilToMRITransf = self._seedTarget.coilToMRITransf
        seedCoilToUnrotSeedCoil = composeTransform(ptr.active_matrix_from_angle(2, -np.deg2rad(self._gridPrimaryAngleWdgt.value)))  # TODO: check sign of angle
        gridSpaceToSeedCoilTransf = composeTransform(ptr.active_matrix_from_angle(2, np.deg2rad(self._gridPrimaryAngleWdgt.value)),  # TODO: check sign of angle
                                                     np.asarray([0, 0, refDepthFromSeedCoil]))
        gridSpaceToMRITransf = concatenateTransforms([gridSpaceToSeedCoilTransf, seedCoilToMRITransf])

        entryDir = Vector(self._seedTarget.entryCoord - self._seedTarget.targetCoord).unit()

        pivotDepth = self._gridPivotDepth.value()  # in mm, dist from seed at grid depth to "common" origin

        # extraTransf = np.eye(4)
        # extraTransf[:3, 3] = refDepthFromSeedCoil
        # pivotOrigin_MRISpace = applyTransform(
        #     concatenateTransforms((extraTransf, self._seedTarget.coilToMRITransf)),
        #     np.asarray([0, 0, -pivotDepth]))

        # TODO: update grid spacing below to be defined based on arc lengths around pivot origin, instead of in a 2D plane

        totalThetaX = gridWidthX / pivotDepth
        assert totalThetaX < 2*np.pi, 'Grid width too large for given pivot depth'

        totalThetaY = gridWidthY / pivotDepth
        assert totalThetaY < 2*np.pi, 'Grid width too large for given pivot depth'

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

                rot = ptr.active_matrix_from_extrinsic_euler_yxy((-thetaX, -thetaY, 0))  # TODO: check sign
                transf_pivotToPivoted = composeTransform(rot)

                transf_pivotedToNewUnrot = np.eye(4)
                transf_pivotedToNewUnrot[2, 3] = -pivotDepth

                for iA, aCoord in enumerate(aCoords_seed):
                    transf_newUnrotToNew = concatenateTransforms([
                        invertTransform(seedCoilToUnrotSeedCoil),
                        composeTransform(ptr.active_matrix_from_angle(2, -np.deg2rad(aCoord))),  # TODO: check sign of angle
                    ])

                    newToGridSpaceTransfs[iX*gridNY*gridNAngle + iY*gridNAngle + iA] = invertTransform(concatenateTransforms(
                        [transf_gridSpaceToPivot, transf_pivotToPivoted, transf_pivotedToNewUnrot, transf_newUnrotToNew]))

                    gridHandleAngles[iX*gridNY*gridNAngle + iY*gridNAngle + iA] = aCoord

        newToMRISpaceTransfs = np.full((numPoints, 4, 4), np.nan)
        for i in range(numPoints):
            newToMRISpaceTransfs[i] = concatenateTransforms([newToGridSpaceTransfs[i], gridSpaceToMRITransf])

        newCoilToNewTransf = np.eye(4)
        newCoilToNewTransf[2, 3] = -refDepthFromSeedCoil  # before any additional depth correction (e.g. before matching to new skin depth)

        newEntryToNewTransf = np.eye(4)
        newEntryToNewTransf[2, 3] = -refDepthFromSeedEntry

        newTargetToNewTransf = np.eye(4)
        newTargetToNewTransf[2, 3] = -refDepthFromSeedTarget

        for i in range(numPoints):
            uniqueTargetKey = makeStrUnique(baseStr=f'{self._seedTarget.key} grid point {i+1}', # TODO: include X and Y indices separately in grid key
                                            existingStrs=self._session.targets.keys(),
                                            delimiter='#')

            newCoilToMRITransf = concatenateTransforms([newCoilToNewTransf, newToMRISpaceTransfs[i]])
            entryCoord_MRISpace = applyTransform((newEntryToNewTransf, newToMRISpaceTransfs[i]), np.asarray([0, 0, 0]))
            targetCoord_MRISpace = applyTransform((newTargetToNewTransf, newToMRISpaceTransfs[i]), np.asarray([0, 0, 0]))

            newTarget = Target(
                session=self._session,
                coilToMRITransf=newCoilToMRITransf,
                targetCoord=targetCoord_MRISpace,
                entryCoord=entryCoord_MRISpace,
                depthOffset=self._seedTarget.depthOffset,
                key=uniqueTargetKey,
                angle=self._seedTarget.angle + gridHandleAngles[i],  # TODO: check sign of offset, and note that this is approximate due to pivot angles
                color=self._seedTarget.color,
            )
            logger.debug(f'New target: {newTarget}')
            self._pendingGridTargetKeys.append(newTarget.key)
            self._session.targets.addItem(newTarget)

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

        self._gridNeedsUpdate.set()

    def _onTargetComboBoxCurrentIndexChanged(self, index: int):
        if index == -1:
            self.seedTarget = None
            return

        seedTargetKey = self._targetsModel.getCollectionItemKeyFromIndex(index)
        self.seedTarget = self._session.targets[seedTargetKey]

    def _onGridPrimaryAngleChanged(self, angle: float):
        self._gridNeedsUpdate.set()

    def _onGridSpacingAtDepthChanged(self, index: int):
        self._gridNeedsUpdate.set()

    def _onGridPivotDepthChanged(self, value: float):
        self._gridNeedsUpdate.set()

    def _onGridDepthMethodChanged(self, index: int):
        self._gridNeedsUpdate.set()

    def _onGridEntryAngleMethodChanged(self, index: int):
        self._gridNeedsUpdate.set()

    def _onGridWidthChanged(self, value: float):
        self._gridNeedsUpdate.set()

    def _onGridNChanged(self, value: int):
        self._gridNeedsUpdate.set()

    def _onGridHandleAngleSpanChanged(self, *args):
        self._gridNeedsUpdate.set()

    def _onCancelBtnClicked(self, checked: bool):
        self._deleteAnyPendingGridTargets()
        self._targetComboBox.setCurrentIndex(-1)

    def _onFinishBtnClicked(self, checked: bool):
        self._pendingGridTargetKeys.clear()
        self._targetComboBox.setCurrentIndex(-1)