import asyncio
from contextlib import contextmanager
import json
from math import nan
import typing as tp

import attrs
import logging
import numpy as np
import pytransform3d.rotations as ptr
from qtpy import QtWidgets, QtGui, QtCore
from skspatial.objects import Vector

from NaviNIBS.Navigator.Model.Targets import Target
from NaviNIBS.Navigator.Model.TargetGrids import TargetGrid, EntryAngleMethod, SpacingMethod, DepthMethod, CartesianTargetGrid
from NaviNIBS.Navigator.GUI.CollectionModels.TargetGridsTableModel import TargetGridsTableModel
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

    _gridComboBox: QtWidgets.QComboBox = attrs.field(init=False, factory=QtWidgets.QComboBox)
    _seedTargetComboBox: QtWidgets.QComboBox = attrs.field(init=False, factory=QtWidgets.QComboBox)
    _gridModel: TargetGridsTableModel = attrs.field(init=False)
    _targetsModel: FullTargetsTableModel = attrs.field(init=False)
    _preChangeGridComboBoxIndex: list[int] = attrs.field(init=False, factory=list)
    _preChangeTargetComboBoxIndex: list[int] = attrs.field(init=False, factory=list)

    _gridPrimaryAngleWdgt: AngleDial = attrs.field(init=False)

    _gridSpacingAtDepthWdgt: QtWidgets.QComboBox = attrs.field(init=False, factory=QtWidgets.QComboBox)
    _gridEntryAngleMethodWdgt: QtWidgets.QComboBox = attrs.field(init=False, factory=QtWidgets.QComboBox)
    _gridPivotDepth: QtWidgets.QDoubleSpinBox = attrs.field(init=False, factory=QtWidgets.QDoubleSpinBox)
    _gridDepthMethodWdgt: QtWidgets.QComboBox = attrs.field(init=False, factory=QtWidgets.QComboBox)

    _grid: TargetGrid | None = attrs.field(init=False, default=None)

    _disableWidgetsWhenNoGrid: list[QtWidgets.QWidget] = attrs.field(init=False, factory=list)

    _gridWidthWdgts: tuple[QtWidgets.QDoubleSpinBox, QtWidgets.QDoubleSpinBox] = attrs.field(init=False)
    _gridNWdgts: tuple[QtWidgets.QSpinBox, QtWidgets.QSpinBox] = attrs.field(init=False)

    _gridHandleAngleWdgts: tuple[AngleDial, AngleDial] = attrs.field(init=False)
    _gridHandleAngleNWdgt: QtWidgets.QSpinBox = attrs.field(init=False)

    _gridFormatStrWdgt: QtWidgets.QLineEdit = attrs.field(init=False)

    _autoapplyCheckBox: QtWidgets.QCheckBox = attrs.field(init=False)
    _generateBtn: QtWidgets.QPushButton = attrs.field(init=False)

    _widgetToGridUpdatesBlocked: bool = attrs.field(init=False, default=False)

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

        self._gridModel = TargetGridsTableModel(session=self._session)
        self._gridComboBox.setModel(self._gridModel)
        preventAnnoyingScrollBehaviour(self._gridComboBox)
        if True:
            # because setting grids from empty to non-empty also sets current index to 0, need to monitor collection directly to block this
            self._gridModel.collection.sigItemsAboutToChange.connect(
                self._onTargetGridsCollectionAboutToChange, priority=1)
            self._gridModel.collection.sigItemsChanged.connect(
                self._onTargetGridsCollectionChanged, priority=-1)
        self._gridComboBox.setCurrentIndex(-1)
        self._gridComboBox.currentIndexChanged.connect(self._onGridComboBoxCurrentIndexChanged)
        layout.addRow('Editing grid:', self._gridComboBox)

        self._targetsModel = FullTargetsTableModel(session=self._session)
        self._seedTargetComboBox.setModel(self._targetsModel)
        preventAnnoyingScrollBehaviour(self._seedTargetComboBox)
        if True:
            # because setting targets from empty to non-empty also sets current index to 0, need to monitor collection directly to block this
            self._targetsModel.collection.sigItemsAboutToChange.connect(
                self._onTargetsCollectionAboutToChange, priority=1)
            self._targetsModel.collection.sigItemsChanged.connect(
                self._onTargetsCollectionChanged, priority=-1)
        self._seedTargetComboBox.setCurrentIndex(-1)
        self._seedTargetComboBox.currentIndexChanged.connect(self._onTargetComboBoxCurrentIndexChanged)
        layout.addRow('Seed target:', self._seedTargetComboBox)
        self._disableWidgetsWhenNoGrid.append(self._seedTargetComboBox)

        # this "primary angle" defines angle of grid X axis relative to seed target's coil X axis
        self._gridPrimaryAngleWdgt = AngleDial(
            centerAngle=0,
            offsetAngle=-90,
            doInvert=True
        )
        self._gridPrimaryAngleWdgt.sigValueChanged.connect(self._onGridPrimaryAngleChanged)
        layout.addRow('Grid angle offset:', self._gridPrimaryAngleWdgt.wdgt)
        self._disableWidgetsWhenNoGrid.append(self._gridPrimaryAngleWdgt.wdgt)

        preventAnnoyingScrollBehaviour(self._gridSpacingAtDepthWdgt)
        self._gridSpacingAtDepthWdgt.addItems([key.value for key in SpacingMethod])
        self._gridSpacingAtDepthWdgt.setCurrentText(SpacingMethod.TARGET)
        self._gridSpacingAtDepthWdgt.currentIndexChanged.connect(self._onGridSpacingAtDepthChanged)
        layout.addRow('Grid at depth of:', self._gridSpacingAtDepthWdgt)
        self._disableWidgetsWhenNoGrid.append(self._gridSpacingAtDepthWdgt)

        preventAnnoyingScrollBehaviour(self._gridEntryAngleMethodWdgt)
        self._gridEntryAngleMethodWdgt.addItems([key.value for key in EntryAngleMethod])
        self._gridEntryAngleMethodWdgt.setCurrentText(EntryAngleMethod.AUTOSET_ENTRY)
        self._gridEntryAngleMethodWdgt.currentIndexChanged.connect(self._onGridEntryAngleMethodChanged)
        layout.addRow('Grid entry angle method:', self._gridEntryAngleMethodWdgt)
        self._disableWidgetsWhenNoGrid.append(self._gridEntryAngleMethodWdgt)

        self._gridPivotDepth.setRange(0, 1000)
        self._gridPivotDepth.setSingleStep(1)
        self._gridPivotDepth.setDecimals(1)
        self._gridPivotDepth.setSuffix(' mm')
        self._gridPivotDepth.setValue(60.)
        self._gridPivotDepth.valueChanged.connect(self._onGridPivotDepthChanged)
        self._gridPivotDepth.setSpecialValueText('Not set')
        # self._gridPivotDepth.setKeyboardTracking(False)
        layout.addRow('Grid pivot depth:', self._gridPivotDepth)
        self._disableWidgetsWhenNoGrid.append(self._gridPivotDepth)

        preventAnnoyingScrollBehaviour(self._gridDepthMethodWdgt)
        self._gridDepthMethodWdgt.addItems([key.value for key in DepthMethod])
        self._gridDepthMethodWdgt.setCurrentText(DepthMethod.FROM_SKIN)
        self._gridDepthMethodWdgt.currentIndexChanged.connect(self._onGridDepthMethodChanged)
        layout.addRow('Grid depth adjustment:', self._gridDepthMethodWdgt)
        self._disableWidgetsWhenNoGrid.append(self._gridDepthMethodWdgt)

        # TODO: add dropdown to change type of grid, hide irrelevant widgets based on type
        # for now, just assume it's always a CartesianTargetGrid

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
            self._disableWidgetsWhenNoGrid.append(gridWidthWdgt)

            preventAnnoyingScrollBehaviour(gridNWdgt)
            # gridNWdgt.setKeyboardTracking(False)
            gridNWdgt.setRange(0, 1000)
            gridNWdgt.setSingleStep(1)
            gridNWdgt.setSpecialValueText('Not set')
            gridNWdgt.valueChanged.connect(self._onGridNChanged)
            layout.addRow(f'Grid N {"XY"[iXY]}:', gridNWdgt)
            self._disableWidgetsWhenNoGrid.append(gridNWdgt)

        # noinspection PyTypeChecker
        self._gridHandleAngleWdgts = tuple(
            AngleDial(centerAngle=0,
                      offsetAngle=-90,
                      doInvert=True) for _ in range(2))
        for iAngle, (angleWdgt, angleLabel) in enumerate(zip(self._gridHandleAngleWdgts, ('start', 'end'))):
            angleWdgt.sigValueChanged.connect(self._onGridHandleAngleSpanChanged)
            layout.addRow(f'Handle angle {angleLabel}', angleWdgt.wdgt)
            self._disableWidgetsWhenNoGrid.append(angleWdgt.wdgt)

        self._gridHandleAngleNWdgt = QtWidgets.QSpinBox()
        self._gridHandleAngleNWdgt.setRange(0, 1000)
        self._gridHandleAngleNWdgt.setSingleStep(1)
        self._gridHandleAngleNWdgt.setSpecialValueText('Not set')
        self._gridHandleAngleNWdgt.valueChanged.connect(self._onGridNChanged)
        layout.addRow(f'Grid handle angle N', self._gridHandleAngleNWdgt)
        self._disableWidgetsWhenNoGrid.append(self._gridHandleAngleNWdgt)

        self._gridFormatStrWdgt = QtWidgets.QLineEdit()
        self._gridFormatStrWdgt.setText('')
        self._gridFormatStrWdgt.editingFinished.connect(self._onGridFormatStrEdited)
        layout.addRow('Grid point name format:', self._gridFormatStrWdgt)
        self._disableWidgetsWhenNoGrid.append(self._gridFormatStrWdgt)


        autoapplyCheckBox = QtWidgets.QCheckBox('Auto-generate on changes')
        autoapplyCheckBox.setChecked(True)
        autoapplyCheckBox.stateChanged.connect(self._onAutoapplyCheckBoxChanged)
        self._autoapplyCheckBox = autoapplyCheckBox
        self._disableWidgetsWhenNoGrid.append(autoapplyCheckBox)
        layout.addRow('', autoapplyCheckBox)

        btnContainer = QtWidgets.QWidget()
        btnLayout = QtWidgets.QHBoxLayout()
        btnContainer.setLayout(btnLayout)
        btnLayout.setContentsMargins(0, 0, 0, 0)

        self._generateBtn = QtWidgets.QPushButton('Generate targets')
        self._generateBtn.clicked.connect(self._onGenerateBtnClicked)
        btnLayout.addWidget(self._generateBtn)
        finishBtn = QtWidgets.QPushButton('Finish')
        finishBtn.clicked.connect(self._onFinishBtnClicked)
        btnLayout.addWidget(finishBtn)
        layout.addWidget(btnContainer)
        self._disableWidgetsWhenNoGrid.extend([self._generateBtn, finishBtn])

        self._updateWidgetsFromGrid()

    @contextmanager
    def _blockWidgetsToGridUpdates(self):
        prevBlocked = self._widgetToGridUpdatesBlocked
        self._widgetToGridUpdatesBlocked = True
        try:
            yield
        finally:
            self._widgetToGridUpdatesBlocked = prevBlocked

    def _updateWidgetsFromGrid(self):

        for wdgt in self._disableWidgetsWhenNoGrid:
            wdgt.setEnabled(self._grid is not None)

        if self._grid is None:
            self._gridComboBox.setCurrentIndex(-1)
            return

        with self._blockWidgetsToGridUpdates():
            index = self._gridModel.getIndexFromCollectionItemKey(self._grid.key)
            if index is None:
                index = -1
            self._gridComboBox.setCurrentIndex(index)

            if self.grid.seedTarget is not None:
                index = self._targetsModel.getIndexFromCollectionItemKey(self.grid.seedTarget.key)
                if index is None:
                    if self.grid.seedTarget.key is not None:
                        logger.warning(f'Seed target {self.grid.seedTarget.key} not found in targets model.')
                    index = -1
            else:
                index =-1
            self._seedTargetComboBox.setCurrentIndex(index)

            self._gridPrimaryAngleWdgt.value = self._grid.primaryAngle or 0.

            if self._grid.spacingAtDepth is None:
                self._gridSpacingAtDepthWdgt.setCurrentIndex(-1)
            else:
                self._gridSpacingAtDepthWdgt.setCurrentText(self._grid.spacingAtDepth)

            if self._grid.entryAngleMethod is None:
                self._gridEntryAngleMethodWdgt.setCurrentIndex(-1)
            else:
                self._gridEntryAngleMethodWdgt.setCurrentText(self._grid.entryAngleMethod)

            self._gridPivotDepth.setValue(self._grid.pivotDepth or 0.)

            if self._grid.depthMethod is None:
                self._gridDepthMethodWdgt.setCurrentIndex(-1)
            else:
                self._gridDepthMethodWdgt.setCurrentText(self._grid.depthMethod)

            assert isinstance(self._grid, CartesianTargetGrid)

            for wdgt, value in zip(self._gridWidthWdgts, (self._grid.xWidth, self._grid.yWidth)):
                wdgt.setValue(value or 0.)

            for wdgt, value in zip(self._gridNWdgts, (self._grid.xN, self._grid.yN)):
                wdgt.setValue(value or 0.)  # note: 0 will display special text

            for wdgt, value in zip(self._gridHandleAngleWdgts, self._grid.angleSpan or (0., 0.)):
                wdgt.value = value

            self._gridHandleAngleNWdgt.setValue(self._grid.angleN or 0)  # note: 0 will display special text

            self._gridFormatStrWdgt.setText(self._grid.targetFormatStr or '')

            self._autoapplyCheckBox.setChecked(self._grid.autoGenerateOnChange)

            canGenerate, reason = self._grid.canGenerateTargets
            self._generateBtn.setEnabled(canGenerate)
            self._generateBtn.setToolTip(reason)

    @property
    def wdgt(self):
        return self._wdgt

    @property
    def grid(self):
        return self._grid

    @grid.setter
    def grid(self, newGrid: TargetGrid | None):
        if self._grid is newGrid:
            return

        if self._grid is not None:
            self._grid.sigItemChanged.disconnect(self._onCurrentGridItemChanged)

        self._grid = newGrid

        if self._grid is not None:
            self._grid.sigItemChanged.connect(self._onCurrentGridItemChanged)

            index = self._gridModel.getIndexFromCollectionItemKey(newGrid.key)
            if index is not None:
                self._gridComboBox.setCurrentIndex(index)

        self._updateWidgetsFromGrid()

    def _onCurrentGridItemChanged(self, key: str, changedAttribs: list[str] | None = None):
        canGenerate, reason = self._grid.canGenerateTargets if self._grid is not None else (False, 'No grid selected')
        self._generateBtn.setEnabled(canGenerate)
        self._generateBtn.setToolTip(reason)
        # TODO: optimize to only update relevant widgets based on changedAttribs
        # TODO: add a queuing system to avoid multiple rapid updates
        self._updateWidgetsFromGrid()

    def _onTargetGridsCollectionAboutToChange(self, *args, **kwargs):
        if len(self._preChangeGridComboBoxIndex) == 0:
            # don't respond to combo box index changes during a target grids change,
            # since if the starting selection is empty it will force reset to non-empty
            self._gridComboBox.currentIndexChanged.disconnect(
                self._onGridComboBoxCurrentIndexChanged)
        self._preChangeGridComboBoxIndex.append(self._gridComboBox.currentIndex())

    def _onTargetGridsCollectionChanged(self, *args, **kwargs):
        if len(self._preChangeGridComboBoxIndex) > 0:
            # once a change is complete, restore previous index
            # if it was empty, and respond to any other change
            prevIndex = self._preChangeGridComboBoxIndex.pop()

            if len(self._preChangeGridComboBoxIndex) == 0:
                if prevIndex == -1:
                    self._gridComboBox.setCurrentIndex(-1)
                elif prevIndex != self._gridComboBox.currentIndex():
                    self._onGridComboBoxCurrentIndexChanged(self._gridComboBox.currentIndex())

                self._gridComboBox.currentIndexChanged.connect(
                    self._onGridComboBoxCurrentIndexChanged)


    def _onTargetsCollectionAboutToChange(self, *args, **kwargs):
        if len(self._preChangeTargetComboBoxIndex) == 0:
            # don't respond to combo box index changes during a targets change,
            # since if the starting selection is empty it will force reset to non-empty
            self._seedTargetComboBox.currentIndexChanged.disconnect(
                self._onTargetComboBoxCurrentIndexChanged)
        self._preChangeTargetComboBoxIndex.append(self._seedTargetComboBox.currentIndex())

    def _onTargetsCollectionChanged(self, *args, **kwargs):
        if len(self._preChangeTargetComboBoxIndex) > 0:
            # once a change is complete, restore previous index
            # if it was empty, and respond to any other change
            prevIndex = self._preChangeTargetComboBoxIndex.pop()

            if len(self._preChangeTargetComboBoxIndex) == 0:
                if prevIndex == -1:
                    self._seedTargetComboBox.setCurrentIndex(-1)
                elif prevIndex != self._seedTargetComboBox.currentIndex():
                    self._onTargetComboBoxCurrentIndexChanged(self._seedTargetComboBox.currentIndex())

                self._seedTargetComboBox.currentIndexChanged.connect(
                    self._onTargetComboBoxCurrentIndexChanged)

    def _onGridComboBoxCurrentIndexChanged(self, index: int):
        if index == -1:
            self.grid = None
            return

        gridKey = self._gridModel.getCollectionItemKeyFromIndex(index)
        self.grid = self._session.targetGrids[gridKey]

    def _onTargetComboBoxCurrentIndexChanged(self, index: int):
        if self._widgetToGridUpdatesBlocked:
            return

        if index == -1:
            seedTargetKey = None
        else:
            seedTargetKey = self._targetsModel.getCollectionItemKeyFromIndex(index)

        prevSeedTargetKey = self.grid.seedTargetKey
        if prevSeedTargetKey is None:
            prevSeedTargetKey = '<Target>'

        self.grid.seedTargetKey = seedTargetKey

        if True:
            # if grid key had previous seed target key at its beginning, then update to replace with new target key instead
            if (prevSeedTargetKey != (seedTargetKey or '<Target>') and
                    self.grid.key.startswith(prevSeedTargetKey)):
                existingGridKeys = set(self._session.targetGrids.keys())
                existingGridKeys.remove(self.grid.key)
                newGridKey = makeStrUnique(
                    baseStr=self.grid.key.replace(prevSeedTargetKey, seedTargetKey or '<Target>', 1),
                    existingStrs=existingGridKeys)
                self.grid.key = newGridKey

    def _onGridPrimaryAngleChanged(self, angle: float):
        if self._widgetToGridUpdatesBlocked:
            return

        self.grid.primaryAngle = angle

    def _onGridSpacingAtDepthChanged(self, index: int | None = None):
        if self._widgetToGridUpdatesBlocked:
            return

        methodText = self._gridSpacingAtDepthWdgt.currentText()
        if methodText == '':
            self.grid.spacingAtDepth = None
        else:
            self.grid.spacingAtDepth = SpacingMethod(methodText)

    def _onGridPivotDepthChanged(self, value: float):
        if self._widgetToGridUpdatesBlocked:
            return

        self.grid.pivotDepth = value if value != 0. else None

    def _onGridDepthMethodChanged(self, index: int):
        if self._widgetToGridUpdatesBlocked:
            return

        methodText = self._gridDepthMethodWdgt.currentText()
        if methodText == '':
            self.grid.depthMethod = None
        else:
            self.grid.depthMethod = DepthMethod(methodText)

    def _onGridEntryAngleMethodChanged(self, index: int | None = None):
        if self._widgetToGridUpdatesBlocked:
            return

        methodText = self._gridEntryAngleMethodWdgt.currentText()
        if methodText == '':
            self.grid.entryAngleMethod = None
        else:
            self.grid.entryAngleMethod = EntryAngleMethod(methodText)

    def _onGridWidthChanged(self, value: float):
        if self._widgetToGridUpdatesBlocked:
            return

        self.grid.xWidth = self._gridWidthWdgts[0].value()
        self.grid.yWidth = self._gridWidthWdgts[1].value()

    def _onGridNChanged(self, value: int):
        if self._widgetToGridUpdatesBlocked:
            return

        self.grid.xN = self._gridNWdgts[0].value() if self._gridNWdgts[0].value() != 0 else None
        self.grid.yN = self._gridNWdgts[1].value() if self._gridNWdgts[1].value() != 0 else None
        self.grid.angleN = self._gridHandleAngleNWdgt.value() if self._gridHandleAngleNWdgt.value() != 0 else None

    def _onGridHandleAngleSpanChanged(self, *args):
        if self._widgetToGridUpdatesBlocked:
            return

        self.grid.angleSpan = (
            self._gridHandleAngleWdgts[0].value,
            self._gridHandleAngleWdgts[1].value
        )

    def _onGridFormatStrEdited(self):
        if self._widgetToGridUpdatesBlocked:
            return

        self.grid.targetFormatStr = self._gridFormatStrWdgt.text() if len(self._gridFormatStrWdgt.text()) > 0 else None

    def _onAutoapplyCheckBoxChanged(self, state: int):
        if self._widgetToGridUpdatesBlocked:
            return

        self.grid.autoGenerateOnChange = (state == QtCore.Qt.CheckState.Checked.value)

    def _onGenerateBtnClicked(self, checked: bool):
        self.grid.generateTargets()

    def _onFinishBtnClicked(self, checked: bool):
        self._gridComboBox.setCurrentIndex(-1)