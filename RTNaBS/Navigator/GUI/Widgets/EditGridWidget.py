import asyncio
import attrs
import json
import logging
import numpy as np
import pytransform3d.rotations as ptr
from qtpy import QtWidgets, QtGui, QtCore
from skspatial.objects import Vector
import typing as tp

from RTNaBS.Navigator.Model.Targets import Target
from RTNaBS.Navigator.GUI.CollectionModels.TargetsTableModel import TargetsTableModel, FullTargetsTableModel
from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.Navigator.Model.Calculations import getClosestPointToPointOnMesh, calculateCoilToMRITransfFromTargetEntryAngle
from RTNaBS.util import makeStrUnique
from RTNaBS.util.Asyncio import asyncTryAndLogExceptionOnError
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import applyTransform, invertTransform, composeTransform, concatenateTransforms, applyDirectionTransform, calculateRotationMatrixFromVectorToVector
from RTNaBS.util.GUI.QDial import AngleDial
from RTNaBS.util.GUI.QScrollContainer import QScrollContainer
from RTNaBS.util.GUI.QMouseWheelAdjustmentGuard import preventAnnoyingScrollBehaviour

logger = logging.getLogger(__name__)


@attrs.define(kw_only=True)
class EditGridWidget:
    _session: Session = attrs.field(repr=False)

    _wdgt: QtWidgets.QWidget = attrs.field(factory=lambda: QtWidgets.QGroupBox('Edit grid'))
    _scroll: QScrollContainer = attrs.field(init=False)

    _targetComboBox: QtWidgets.QComboBox = attrs.field(init=False, factory=QtWidgets.QComboBox)
    _targetsModel: FullTargetsTableModel = attrs.field(init=False)

    _gridPrimaryAngleWdgt: AngleDial = attrs.field(init=False)

    _gridDepthWdgt: QtWidgets.QComboBox = attrs.field(init=False, factory=QtWidgets.QComboBox)

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

        self._gridDepthWdgt.addItems(['Coil', 'Entry', 'Target'])
        self._gridDepthWdgt.setCurrentIndex(2)
        self._gridDepthWdgt.currentIndexChanged.connect(self._onGridDepthChanged)
        layout.addRow('Grid depth:', self._gridDepthWdgt)
        self._disableWidgetsWhenNoTarget.append(self._gridDepthWdgt)

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
            gridWidthWdgt.setRange(0, 1000)
            gridWidthWdgt.setSingleStep(1)
            gridWidthWdgt.setDecimals(1)
            gridWidthWdgt.setSuffix(' mm')
            gridWidthWdgt.setValue(20.)
            gridWidthWdgt.valueChanged.connect(self._onGridWidthChanged)
            layout.addRow(f'Grid width {"XY"[iXY]}:', gridWidthWdgt)
            self._disableWidgetsWhenNoTarget.append(gridWidthWdgt)

            preventAnnoyingScrollBehaviour(gridNWdgt)
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

    def _deleteAnyPendingGridTargets(self):
        if len(self._pendingGridTargetKeys) > 0:
            self._session.targets.deleteItems(self._pendingGridTargetKeys)
            self._pendingGridTargetKeys.clear()

    def _regenerateGrid(self):
        # TODO: instead of deleting everything and recreating, edit / repurpose existing targets
        self._deleteAnyPendingGridTargets()

        #return  # TODO: debug, delete

        if self._seedTarget is None:
            return

        depthMode = self._gridDepthWdgt.currentText()
        match depthMode:
            case 'Coil':
                refOrigin = self._seedTarget.entryCoordPlusDepthOffset
            case 'Entry':
                refOrigin = self._seedTarget.entryCoord
            case 'Target':
                refOrigin = self._seedTarget.targetCoord
            case _:
                raise NotImplementedError

        closestPt_skin = getClosestPointToPointOnMesh(
            session=self._session,
            whichMesh='skinSurf',
            point_MRISpace=refOrigin,
        )

        entryDir = Vector(self._seedTarget.entryCoord - self._seedTarget.targetCoord).unit()

        refDepthFromSkin = entryDir.scalar_projection(Vector(refOrigin - closestPt_skin))

        targetDepthFromSkin = -np.linalg.norm(closestPt_skin - self._seedTarget.targetCoord)  # assume target inside head

        gridWidthX, gridWidthY = (wdgt.value() for wdgt in self._gridWidthWdgts)

        gridNX, gridNY = (wdgt.value() for wdgt in self._gridNWdgts)

        gridHandleAngleStart, gridHandleAngleStop = (wdgt.value for wdgt in self._gridHandleAngleWdgts)

        gridNAngle = self._gridHandleAngleNWdgt.value()

        xCoords_seed = np.linspace(-gridWidthX / 2, gridWidthX / 2, gridNX)
        yCoords_seed = np.linspace(-gridWidthY / 2, gridWidthY / 2, gridNY)
        aCoords_seed = np.linspace(gridHandleAngleStart, gridHandleAngleStop, gridNAngle)
        coords_seed = np.array(np.meshgrid(xCoords_seed, yCoords_seed, aCoords_seed)).T.reshape(-1, 3)

        refDepthFromSeedCoil = -np.linalg.norm(refOrigin - self._seedTarget.entryCoordPlusDepthOffset)
        refDepthTargetToEntryDist = np.linalg.norm(self._seedTarget.entryCoord - self._seedTarget.targetCoord)

        gridCoords_seedCoilSpace = np.hstack((
            coords_seed[:, :2],
            np.full((coords_seed.shape[0], 1), refDepthFromSeedCoil),  # add z axis
            coords_seed[:, np.newaxis, 2]  # angle will be 4th column
        ))

        seedCoilToMRITransf = self._seedTarget.coilToMRITransf
        extraTransf = composeTransform(ptr.active_matrix_from_angle(2, np.deg2rad(self._gridPrimaryAngleWdgt.value)))
        seedCoilToMRITransf = concatenateTransforms([extraTransf, seedCoilToMRITransf])

        gridCoords_MRISpace = applyTransform(seedCoilToMRITransf, gridCoords_seedCoilSpace[:, :3])

        # not ideal, but adjust entry angle based on each new target's position (in brain)
        targetCoords_seedCoilSpace = np.concatenate((coords_seed[:, :2], np.full((coords_seed.shape[0], 1), targetDepthFromSkin)), axis=1)

        targetCoords_MRISpace = applyTransform(seedCoilToMRITransf, targetCoords_seedCoilSpace)
        entryCoords_MRISpace = np.full(targetCoords_MRISpace.shape, np.nan)

        for i in range(gridCoords_MRISpace.shape[0]):
            gridCoord = gridCoords_MRISpace[i, :]
            targetCoord = targetCoords_MRISpace[i, :]
            closestPt_skin_i = getClosestPointToPointOnMesh(
                session=self._session,
                whichMesh='skinSurf',
                point_MRISpace=targetCoord)
            entryDir = Vector(closestPt_skin_i - targetCoord).unit()
            targetCoords_MRISpace[i, :] = gridCoord + entryDir * (targetDepthFromSkin - refDepthFromSkin)  # TODO: double check signs
            entryCoords_MRISpace[i, :] = targetCoords_MRISpace[i, :] + entryDir * refDepthTargetToEntryDist

        for i in range(gridCoords_MRISpace.shape[0]):
            uniqueTargetKey = makeStrUnique(baseStr=f'{self._seedTarget.key} grid point {i+1}', # TODO: include X and Y indices separately in grid key
                                            existingStrs=self._session.targets.keys(),
                                            delimiter='#')
            newTarget = Target(
                session=self._session,
                targetCoord=targetCoords_MRISpace[i, :],
                entryCoord=entryCoords_MRISpace[i, :],
                depthOffset=self._seedTarget.depthOffset,
                key=uniqueTargetKey,
                angle=self._seedTarget.angle + gridCoords_seedCoilSpace[i, 3],
                color=self._seedTarget.color,
            )
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

    def _onGridDepthChanged(self, index: int):
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