from __future__ import annotations
import asyncio
import json
import logging
import typing as tp

import attrs
from abc import ABC
import numpy as np
import pytransform3d.rotations as ptr
from skspatial.objects import Line, Plane, Vector
from qtpy import QtWidgets, QtGui, QtCore
import qtawesome as qta

from NaviNIBS.Navigator.Model import ROIs
from NaviNIBS.Devices.ToolPositionsClient import ToolPositionsClient
from NaviNIBS.Navigator.GUI.CollectionModels.ROIsTableModel import ROIsTableModel
from NaviNIBS.Navigator.GUI.Widgets.SurfViews import Surf3DView
from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.Navigator.Model.Calculations import getClosestPointToPointOnMesh
from NaviNIBS.util import exceptionToStr
from NaviNIBS.util.Asyncio import asyncWait, asyncTryAndLogExceptionOnError
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.Transforms import applyTransform, invertTransform, composeTransform, concatenateTransforms, applyDirectionTransform, calculateRotationMatrixFromVectorToVector
from NaviNIBS.util import makeStrUnique
from NaviNIBS.util.GUI.Icons import getIcon
from NaviNIBS.util.GUI.QDial import AngleDial
from NaviNIBS.util.GUI.QScrollContainer import QScrollContainer
from NaviNIBS.util.GUI.QTextEdit import AutosizingPlainTextEdit, TextEditWithDrafting
from NaviNIBS.util.GUI.QMouseWheelAdjustmentGuard import preventAnnoyingScrollBehaviour
from NaviNIBS.util.pyvista import DefaultBackgroundPlotter, RemotePlotterProxy

if tp.TYPE_CHECKING:
    from NaviNIBS.Navigator.GUI.Widgets.EditROIWidget import EditPipelineROIInnerWidget

logger = logging.getLogger(__name__)



@attrs.define(init=False, slots=False)
class ROIStageWidget(QtWidgets.QGroupBox):
    _stage: ROIs.ROIStage
    _roi: ROIs.PipelineROI = attrs.field(repr=False)
    _roiWidget: EditPipelineROIInnerWidget = attrs.field(repr=False)
    _session: Session = attrs.field(repr=False)
    _linkTo3DView: Surf3DView | None = attrs.field(default=None, repr=False)

    _typeField: QtWidgets.QComboBox = attrs.field(init=False)
    _formLayout: QtWidgets.QFormLayout = attrs.field(init=False)
    _insertStageBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _deleteStageBtn: QtWidgets.QPushButton = attrs.field(init=False)

    def __init__(self, *args, parent: QtWidgets.QWidget | None = None, **kwargs):
        super().__init__(parent=parent)
        self.__attrs_init__(*args, **kwargs)

    def __attrs_post_init__(self):
        if False:
            self.setTitle(self._stage.label)

        # add delete button to top right corner of groupbox
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        btnContainer = QtWidgets.QWidget()
        btnLayout = QtWidgets.QHBoxLayout()
        btnLayout.setContentsMargins(0, 0, 0, 0)
        btnContainer.setLayout(btnLayout)

        btn = QtWidgets.QPushButton(getIcon('mdi6.plus'), '')
        btn.setToolTip('Insert stage above')
        btn.setFixedSize(24, 24)
        btn.clicked.connect(self._onInsertAboveClicked)
        btn.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        btnLayout.addWidget(btn, alignment=QtCore.Qt.AlignmentFlag.AlignRight)
        self._insertStageBtn = btn

        btn = QtWidgets.QPushButton(getIcon('mdi6.delete'), '')
        btn.setToolTip('Delete stage')
        btn.setFixedSize(24, 24)
        btn.clicked.connect(self._onDeleteClicked)
        btn.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        btnLayout.addWidget(btn, alignment=QtCore.Qt.AlignmentFlag.AlignRight)
        self._deleteStageBtn = btn

        formLayout = QtWidgets.QFormLayout()
        self._formLayout = formLayout
        formContainer = QtWidgets.QWidget()
        formContainer.setLayout(formLayout)
        layout.addWidget(formContainer)

        typeCombo = QtWidgets.QComboBox()
        for key in self._roi.stageLibrary.keys():
            typeCombo.addItem(key)
        typeCombo.setCurrentText(self._stage.type)
        typeCombo.currentIndexChanged.connect(self._onTypeChanged)
        preventAnnoyingScrollBehaviour(typeCombo)
        btnLayout.insertWidget(0, typeCombo, 1)
        self._typeField = typeCombo

        formLayout.addRow('Type:', btnContainer)

        self._stage.sigItemChanged.connect(self._onStageChanged)

        # TODO: add label field

    @property
    def stage(self):
        return self._stage

    def _onStageChanged(self, stage: ROIs.ROIStage, changedAttrs: list[str] | None = None):
        pass

    def _onTypeChanged(self):
        logger.debug(f'Changing stage type to {self._typeField.currentText()}')
        newType = self._typeField.currentText()
        if newType == self._stage.type:
            return
        self._roiWidget.changeTypeOfStage(self._stage, self._typeField.currentText())

    def _onInsertAboveClicked(self, _):
        logger.debug(f'Inserting stage above {self._stage}')
        stage = ROIs.PassthroughStage()
        index = self._roi.stages.index(self._stage)
        self._roi.stages.insert(index, stage)

    def _onDeleteClicked(self, _):
        logger.debug(f'Deleting stage {self._stage}')
        self.setVisible(False)
        self._roi.stages.deleteItem(self._roi.stages.index(self._stage))

    def deleteLater(self, /):
        logger.debug(f'Deleting AddFromSeedPointStageWidget for stage {self._stage}')
        self._stage.sigItemChanged.disconnect(self._onStageChanged)
        super().deleteLater()

    def __del__(self):
        logger.debug(f'Garbage collecting AddFromSeedPointStageWidget for stage {self._stage}')


@attrs.define(init=False, slots=False, kw_only=True)
class PassthroughStageWidget(ROIStageWidget):
    _stage: ROIs.PassthroughStage

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        # no additional parameters


@attrs.define(init=False, slots=False, kw_only=True)
class SelectSurfaceMeshStageWidget(ROIStageWidget):
    _stage: ROIs.SelectSurfaceMesh

    _meshComboBox: QtWidgets.QComboBox = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._meshComboBox = QtWidgets.QComboBox()
        self._meshComboBox.addItem('')  # allow no selection
        for surfKey in self._session.headModel.surfKeys:
            self._meshComboBox.addItem(surfKey)

        if self._stage.meshKey is not None:
            if not self._stage.meshKey in self._session.headModel.surfKeys:
                logger.error(f'SelectSurfaceMesh stage has invalid mesh key {self._stage.meshKey}, resetting to None')
                self._stage.meshKey = None
            else:
                self._meshComboBox.setCurrentText(self._stage.meshKey)

        if self._stage.meshKey is None:
            self._meshComboBox.setCurrentText('')

        self._meshComboBox.currentIndexChanged.connect(self._onMeshComboBoxCurrentIndexChanged)

        self._formLayout.addRow('Surface mesh:', self._meshComboBox)

    def _onStageChanged(self, stage: ROIs.ROIStage, changedAttrs: list[str] | None = None):
        super()._onStageChanged(stage, changedAttrs)
        if changedAttrs is None or 'meshKey' in changedAttrs:
            if self._stage.meshKey is None:
                self._meshComboBox.setCurrentIndex(-1)
            else:
                self._meshComboBox.setCurrentText(self._stage.meshKey)

    def _onMeshComboBoxCurrentIndexChanged(self, index: int):
        if index == -1:
            newMeshKey = None
        else:
            newMeshKey = self._meshComboBox.currentText()

        if newMeshKey == '':
            newMeshKey = None

        if self._stage.meshKey == newMeshKey:
            return

        logger.info(f'Updating SelectSurfaceMesh stage meshKey to {newMeshKey}')
        self._stage.meshKey = newMeshKey


@attrs.define(init=False, slots=False, kw_only=True)
class AddFromSeedPointStageWidget(ROIStageWidget):
    _stage: ROIs.AddFromSeedPoint

    _selectSeedBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _cancelSeedSelectBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _cancelPressedEvt: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    _confirmSeedSelectBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _confirmPressedEvt: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    _seedCoordField: QtWidgets.QLabel = attrs.field(init=False)
    # add an edit option to seed coordinate
    _radiusField: QtWidgets.QDoubleSpinBox = attrs.field(init=False)
    # TODO: add distance metric combo box

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        coordContainer = QtWidgets.QWidget()
        coordLayout = QtWidgets.QHBoxLayout()
        coordLayout.setContentsMargins(0, 0, 0, 0)
        coordContainer.setLayout(coordLayout)

        self._seedCoordField = QtWidgets.QLabel()
        coordLayout.addWidget(self._seedCoordField)
        self._formLayout.addRow('Seed point:', coordContainer)

        self._selectSeedBtn = QtWidgets.QPushButton('Select...')
        self._selectSeedBtn.clicked.connect(self._onSelectSeedBtnClicked)
        coordLayout.addWidget(self._selectSeedBtn)

        btnContainer = QtWidgets.QWidget()
        btnLayout = QtWidgets.QHBoxLayout()
        btnLayout.setContentsMargins(0, 0, 0, 0)
        btnContainer.setLayout(btnLayout)
        self._formLayout.addRow('', btnContainer)

        btn = QtWidgets.QPushButton(getIcon('mdi6.restore'), 'Cancel')
        btnLayout.addWidget(btn)
        btn.clicked.connect(lambda _: self._cancelPressedEvt.set())
        self._cancelSeedSelectBtn = btn

        btn = QtWidgets.QPushButton(getIcon('mdi6.check'), 'Confirm')
        btn.clicked.connect(lambda _: self._confirmPressedEvt.set())
        btnLayout.addWidget(btn)
        self._confirmSeedSelectBtn = btn

        for btn in (self._cancelSeedSelectBtn, self._confirmSeedSelectBtn):
            btn.setVisible(False)
            btn.setEnabled(False)

        # TODO: add menu with actions to copy seed from existing target or sample

        self._radiusField = QtWidgets.QDoubleSpinBox()
        self._radiusField.setRange(0.0, 1e3)
        self._radiusField.setValue(self._stage.radius if self._stage.radius is not None else 0.0)
        self._radiusField.setSpecialValueText(' ')
        self._radiusField.valueChanged.connect(self._onRadiusFieldValueChanged)
        self._formLayout.addRow('Radius (mm):', self._radiusField)

        self._onStageChanged(self._stage, ['seedPoint'])  # initialize seed point display

    def _onStageChanged(self, stage: ROIs.ROIStage, changedAttrs: list[str] | None = None):
        super()._onStageChanged(stage, changedAttrs)
        if changedAttrs is None or 'radius' in changedAttrs:
            self._radiusField.valueChanged.disconnect(self._onRadiusFieldValueChanged)
            self._radiusField.setValue(self._stage.radius if self._stage.radius is not None else 0.0)
            self._radiusField.valueChanged.connect(self._onRadiusFieldValueChanged)

        if changedAttrs is None or 'seedPoint' in changedAttrs:
            if self._stage.seedPoint is not None:
                self._seedCoordField.setText(','.join(['%.1f' % val for val in self._stage.seedPoint]))
            else:
                self._seedCoordField.setText('None')

    def _onSelectSeedBtnClicked(self, _):
        logger.info(f'Selecting seed point for AddFromSeedPoint stage {self._stage}')
        asyncio.create_task(asyncTryAndLogExceptionOnError(self._selectSeedPointInteractive))
        # TODO: start interactive point picking session

    async def _selectSeedPointInteractive(self):
        # make sure 3D view is ready
        if self._linkTo3DView is None:
            logger.warning('Cannot select seed point interactively: no linked 3D view')
            while self._linkTo3DView is None:
                await asyncio.sleep(0.1)

        if isinstance(self._linkTo3DView.plotter, RemotePlotterProxy):
            await self._linkTo3DView.plotter.isReadyEvent.wait()

        pickedPoint = None

        # keep copy of original seed point in case of cancellation
        origSeedPoint = self._stage.seedPoint

        # prepare for picking
        def _onPointClicked(newPt):
            logger.info(f'Point picked at {newPt} for AddFromSeedPoint stage {self._stage}')
            nonlocal pickedPoint
            pickedPoint = newPt
            self._confirmSeedSelectBtn.setEnabled(True)
            if True:
                # provisionally apply change so that visualization can be updated
                logger.info(f'Seed point confirmed at {pickedPoint} for stage {self._stage}')
                self._stage.seedPoint = pickedPoint.tolist()

        with self._linkTo3DView.plotter.allowNonblockingCalls():
            self._linkTo3DView.plotter.disable_picking()  # disable any previous

            self._linkTo3DView.plotter.enable_point_picking(
                show_message='Right click to pick seed point',
                show_point=True,
                tolerance=0.1,
                picker='hardware',
                pickable_window=False,
                point_size =20,
                color='red',
                callback=_onPointClicked
            )

            indexInPipeline = self._roi.stages.index(self._stage)
            inputROI = self._roi.process(upThroughStage=indexInPipeline - 1)
            assert isinstance(inputROI, ROIs.SurfaceMeshROI)
            self._linkTo3DView.pickableSurfs = [inputROI.meshKey]

        self._selectSeedBtn.setEnabled(False)
        self._selectSeedBtn.setVisible(False)
        self._cancelSeedSelectBtn.setEnabled(True)
        for btn in (self._cancelSeedSelectBtn, self._confirmSeedSelectBtn):
            btn.setVisible(True)

        for evt in (self._cancelPressedEvt, self._confirmPressedEvt):
            evt.clear()

        # wait for user to accept picking result or cancel
        await asyncWait(
            [self._cancelPressedEvt.wait(), self._confirmPressedEvt.wait()],
            return_when=asyncio.FIRST_COMPLETED)

        # clean up
        with self._linkTo3DView.plotter.allowNonblockingCalls():
            self._linkTo3DView.plotter.disable_picking()
            self._linkTo3DView.pickableSurfs = []

            self._linkTo3DView.plotter.render()
            # TODO: determine how to hide picked point

        self._selectSeedBtn.setVisible(True)
        self._selectSeedBtn.setEnabled(True)
        for btn in (self._cancelSeedSelectBtn, self._confirmSeedSelectBtn):
            btn.setVisible(False)
            btn.setEnabled(False)

        if self._cancelPressedEvt.is_set():
            logger.info(f'Seed point selection cancelled for stage {self._stage}')
            # revert change
            self._stage.seedPoint = origSeedPoint
            return

        assert self._confirmPressedEvt.is_set()
        assert pickedPoint is not None  # since confirm button is disabled until a point is picked

        # apply final change
        logger.info(f'Seed point confirmed at {pickedPoint} for stage {self._stage}')
        self._stage.seedPoint = np.asarray(pickedPoint)


    def _onRadiusFieldValueChanged(self, newValue: float):
        if newValue == 0.0:
            newRadius = None
        else:
            newRadius = newValue

        if self._stage.radius == newRadius:
            return

        logger.info(f'Updating AddFromSeedPoint stage radius to {newRadius}')
        self._stage.radius = newRadius



@attrs.define(init=False, slots=False, kw_only=True)
class JsonReprStageWidget(ROIStageWidget):
    _textEdit: TextEditWithDrafting = attrs.field(init=False)

    class JsonReprValidator(QtGui.QValidator):
        def __init__(self, StageCls, **kwargs):
            super().__init__(**kwargs)
            self._StageCls = StageCls

        def validate(self, inputStr: str, pos: int) -> tuple[QtGui.QValidator.State, str, int]:
            jsonStr = '{' + inputStr + '}'  # add outer dict braces
            try:
                d = json.loads(jsonStr)
            except Exception:
                return (QtGui.QValidator.Intermediate, inputStr, pos)

            try:
                newStage = self._StageCls.fromDict(d)
            except Exception:
                return (QtGui.QValidator.Intermediate, inputStr, pos)

            return (QtGui.QValidator.Acceptable, inputStr, pos)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        logger.debug(f'Creating JSON repr widget for stage {self._stage}')

        StageCls = self._roi.stageLibrary[self._stage.type]

        textEdit = TextEditWithDrafting(AutosizingPlainTextEdit(),
                                        validator=self.JsonReprValidator(StageCls))
        self._formLayout.addRow('JSON', QtWidgets.QWidget())
        self._formLayout.addRow(textEdit)
        self._textEdit = textEdit

        self._updateTextEdit()

        self._textEdit.textSubmitted.connect(self._onTextEditChanged)

    def _updateTextEdit(self):
        logger.debug(f'Updating JSON repr text edit for stage {self._stage}')
        d = self._stage.asDict()
        d.pop('type')
        jsonStr = self._session._prettyJSONDumps(d)
        jsonStr = jsonStr.strip()[1:-1].strip()  # remove outer dict braces
        self._textEdit.textSubmitted.disconnect(self._onTextEditChanged)
        self._textEdit.text = jsonStr
        self._textEdit.textSubmitted.connect(self._onTextEditChanged)

    def _onStageChanged(self, stage: ROIs.ROIStage, changedAttrs: list[str] | None = None):
        super()._onStageChanged(stage, changedAttrs)
        self._updateTextEdit()

    def _onTextEditChanged(self, newText: str):
        logger.debug(f'Parsing JSON repr text edit for stage {self._stage}')

        jsonStr = newText
        jsonStr = '{' + jsonStr + '}'  # add outer dict braces
        try:
            d = json.loads(jsonStr)
        except Exception as e:
            logger.error(f'Error parsing stage JSON: {exceptionToStr(e)}')
            self._updateTextEdit()  # revert text
            return

        typ = self._stage.type

        try:
            StageCls = self._roi.stageLibrary[typ]
            newStage = StageCls.fromDict(d)
        except Exception as e:
            logger.error(f'Error creating stage from JSON: {exceptionToStr(e)}')
            self._updateTextEdit()  # revert text
            return

        logger.info(f'Updating stage {self._stage} from JSON repr')
        index = self._roi.stages.index(self._stage)
        self._roi.stages[index] = newStage  # replace stage