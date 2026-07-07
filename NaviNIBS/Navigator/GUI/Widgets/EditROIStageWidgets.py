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
from NaviNIBS.Navigator.Model.ROIs.PipelineROI import PipelineROI
from NaviNIBS.Navigator.Model.ROIs import PipelineROIStages as ROIStages
from NaviNIBS.Navigator.Model.ROIs.PipelineROIStages.AddFromSeed import AddFromSeedPoint
from NaviNIBS.Navigator.Model.ROIs.PipelineROIStages.AddFromTarget import AddFromTarget
from NaviNIBS.Navigator.Model.ROIs.PipelineROIStages.AddFromTwoTargets import AddFromTwoTargets
from NaviNIBS.Navigator.Model.ROIs.PipelineROIStages.Combine import Combine
from NaviNIBS.Navigator.Model.ROIs.PipelineROIStages.Project import ProjectBetweenSurfaces
from NaviNIBS.Navigator.GUI.CollectionModels.TargetsTableModel import FullTargetsTableModel
from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.Navigator.Model.Calculations import getClosestPointToPointOnMesh
from NaviNIBS.util import exceptionToStr
from NaviNIBS.util.Asyncio import asyncWait, asyncCreateTask
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
    _stage: ROIStages.ROIStage
    _roi: PipelineROI = attrs.field(repr=False)
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

    def _onStageChanged(self, stage: ROIStages.ROIStage, changedAttrs: list[str] | None = None):
        pass

    def _onTypeChanged(self):
        logger.debug(f'Changing stage type to {self._typeField.currentText()}')
        newType = self._typeField.currentText()
        if newType == self._stage.type:
            return
        self._roiWidget.changeTypeOfStage(self._stage, self._typeField.currentText())

    def _onInsertAboveClicked(self, _):
        logger.debug(f'Inserting stage above {self._stage}')
        stage = ROIStages.PassthroughStage()
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
    _stage: ROIStages.PassthroughStage

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        # no additional parameters


@attrs.define(init=False, slots=False, kw_only=True)
class SelectSurfaceMeshStageWidget(ROIStageWidget):
    _stage: ROIStages.SelectSurfaceMesh

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

    def _onStageChanged(self, stage: ROIStages.ROIStage, changedAttrs: list[str] | None = None):
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
class ProjectBetweenSurfacesStageWidget(ROIStageWidget):
    _stage: ProjectBetweenSurfaces

    _toSurfaceComboBox: QtWidgets.QComboBox = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._toSurfaceComboBox = QtWidgets.QComboBox()
        self._toSurfaceComboBox.addItem('')  # allow no selection
        for surfKey in self._session.headModel.surfKeys:
            self._toSurfaceComboBox.addItem(surfKey)

        if self._stage.toSurfaceKey is not None:
            if self._stage.toSurfaceKey not in self._session.headModel.surfKeys:
                logger.error(f'ProjectBetweenSurfaces stage has invalid toSurfaceKey {self._stage.toSurfaceKey}, resetting to None')
                self._stage.toSurfaceKey = None
            else:
                self._toSurfaceComboBox.setCurrentText(self._stage.toSurfaceKey)

        if self._stage.toSurfaceKey is None:
            self._toSurfaceComboBox.setCurrentText('')

        self._toSurfaceComboBox.currentIndexChanged.connect(self._onToSurfaceComboBoxCurrentIndexChanged)
        preventAnnoyingScrollBehaviour(self._toSurfaceComboBox)

        self._formLayout.addRow('To surface:', self._toSurfaceComboBox)

    def _onStageChanged(self, stage: ROIStages.ROIStage, changedAttrs: list[str] | None = None):
        super()._onStageChanged(stage, changedAttrs)
        if changedAttrs is None or 'toSurfaceKey' in changedAttrs:
            if self._stage.toSurfaceKey is None:
                self._toSurfaceComboBox.setCurrentText('')
            else:
                self._toSurfaceComboBox.setCurrentText(self._stage.toSurfaceKey)

    def _onToSurfaceComboBoxCurrentIndexChanged(self, index: int):
        if index == -1:
            newToSurfaceKey = None
        else:
            newToSurfaceKey = self._toSurfaceComboBox.currentText()

        if newToSurfaceKey == '':
            newToSurfaceKey = None

        if self._stage.toSurfaceKey == newToSurfaceKey:
            return

        logger.info(f'Updating ProjectBetweenSurfaces stage toSurfaceKey to {newToSurfaceKey}')
        self._stage.toSurfaceKey = newToSurfaceKey


@attrs.define(init=False, slots=False, kw_only=True)
class AddFromSeedPointStageWidget(ROIStageWidget):
    _stage: AddFromSeedPoint

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

    def _onStageChanged(self, stage: ROIStages.ROIStage, changedAttrs: list[str] | None = None):
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
        asyncCreateTask(self._selectSeedPointInteractive)
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

    def _onStageChanged(self, stage: ROIStages.ROIStage, changedAttrs: list[str] | None = None):
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


@attrs.define(init=False, slots=False, kw_only=True)
class AddFromTargetStageWidget(ROIStageWidget):
    _stage: AddFromTarget

    _targetsModel: FullTargetsTableModel = attrs.field(init=False)
    _targetCombo: QtWidgets.QComboBox = attrs.field(init=False)
    _radiusXField: QtWidgets.QDoubleSpinBox = attrs.field(init=False)
    _radiusYField: QtWidgets.QDoubleSpinBox = attrs.field(init=False)
    _offsetXField: QtWidgets.QDoubleSpinBox = attrs.field(init=False)
    _offsetYField: QtWidgets.QDoubleSpinBox = attrs.field(init=False)
    _depthThicknessField: QtWidgets.QDoubleSpinBox = attrs.field(init=False)

    _preChangeTargetComboBoxIndex: list[int] = attrs.field(init=False, factory=list)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._targetsModel = FullTargetsTableModel(session=self._session)

        self._targetCombo = QtWidgets.QComboBox()
        self._targetCombo.setModel(self._targetsModel)
        preventAnnoyingScrollBehaviour(self._targetCombo)
        self._targetsModel.collection.sigItemsAboutToChange.connect(
            self._onTargetsCollectionAboutToChange, priority=1)
        self._targetsModel.collection.sigItemsChanged.connect(
            self._onTargetsCollectionChanged, priority=-1)
        self._targetCombo.setCurrentIndex(-1)
        if self._stage.targetKey is not None:
            index = self._targetsModel.getIndexFromCollectionItemKey(self._stage.targetKey)
            if index is not None:
                self._targetCombo.setCurrentIndex(index)
        self._targetCombo.currentIndexChanged.connect(self._onTargetComboCurrentIndexChanged)
        self._formLayout.addRow('Target:', self._targetCombo)

        self._radiusXField = QtWidgets.QDoubleSpinBox()
        self._radiusXField.setRange(0.0, 1e3)
        self._radiusXField.setValue(self._stage.radiusX if self._stage.radiusX is not None else 0.0)
        self._radiusXField.setSpecialValueText(' ')
        preventAnnoyingScrollBehaviour(self._radiusXField)
        self._radiusXField.valueChanged.connect(self._onRadiusXValueChanged)
        self._formLayout.addRow('Radius X (mm):', self._radiusXField)

        self._radiusYField = QtWidgets.QDoubleSpinBox()
        self._radiusYField.setRange(0.0, 1e3)
        self._radiusYField.setValue(self._stage.radiusY if self._stage.radiusY is not None else 0.0)
        self._radiusYField.setSpecialValueText(' ')
        preventAnnoyingScrollBehaviour(self._radiusYField)
        self._radiusYField.valueChanged.connect(self._onRadiusYValueChanged)
        self._formLayout.addRow('Radius Y (mm):', self._radiusYField)

        self._depthThicknessField = QtWidgets.QDoubleSpinBox()
        self._depthThicknessField.setRange(0.0, 1e3)
        self._depthThicknessField.setValue(self._stage.depthThickness)
        preventAnnoyingScrollBehaviour(self._depthThicknessField)
        self._depthThicknessField.valueChanged.connect(self._onDepthThicknessValueChanged)
        self._formLayout.addRow('Depth thickness (mm):', self._depthThicknessField)

        self._offsetXField = QtWidgets.QDoubleSpinBox()
        self._offsetXField.setRange(-1e3, 1e3)
        self._offsetXField.setValue(self._stage.offsetX if self._stage.offsetX is not None else 0.0)
        preventAnnoyingScrollBehaviour(self._offsetXField)
        self._offsetXField.valueChanged.connect(self._onOffsetXValueChanged)
        self._formLayout.addRow('Offset X (mm):', self._offsetXField)

        self._offsetYField = QtWidgets.QDoubleSpinBox()
        self._offsetYField.setRange(-1e3, 1e3)
        self._offsetYField.setValue(self._stage.offsetY if self._stage.offsetY is not None else 0.0)
        preventAnnoyingScrollBehaviour(self._offsetYField)
        self._offsetYField.valueChanged.connect(self._onOffsetYValueChanged)
        self._formLayout.addRow('Offset Y (mm):', self._offsetYField)

    def _onStageChanged(self, stage: ROIStages.ROIStage, changedAttrs: list[str] | None = None):
        super()._onStageChanged(stage, changedAttrs)

        if changedAttrs is None or 'targetKey' in changedAttrs:
            self._targetCombo.currentIndexChanged.disconnect(self._onTargetComboCurrentIndexChanged)
            if self._stage.targetKey is None:
                self._targetCombo.setCurrentIndex(-1)
            else:
                index = self._targetsModel.getIndexFromCollectionItemKey(self._stage.targetKey)
                self._targetCombo.setCurrentIndex(index if index is not None else -1)
            self._targetCombo.currentIndexChanged.connect(self._onTargetComboCurrentIndexChanged)

        if changedAttrs is None or 'radiusX' in changedAttrs:
            self._radiusXField.valueChanged.disconnect(self._onRadiusXValueChanged)
            self._radiusXField.setValue(self._stage.radiusX if self._stage.radiusX is not None else 0.0)
            self._radiusXField.valueChanged.connect(self._onRadiusXValueChanged)

        if changedAttrs is None or 'radiusY' in changedAttrs:
            self._radiusYField.valueChanged.disconnect(self._onRadiusYValueChanged)
            self._radiusYField.setValue(self._stage.radiusY if self._stage.radiusY is not None else 0.0)
            self._radiusYField.valueChanged.connect(self._onRadiusYValueChanged)

        if changedAttrs is None or 'depthThickness' in changedAttrs:
            self._depthThicknessField.valueChanged.disconnect(self._onDepthThicknessValueChanged)
            self._depthThicknessField.setValue(self._stage.depthThickness)
            self._depthThicknessField.valueChanged.connect(self._onDepthThicknessValueChanged)

        if changedAttrs is None or 'offsetX' in changedAttrs:
            self._offsetXField.valueChanged.disconnect(self._onOffsetXValueChanged)
            self._offsetXField.setValue(self._stage.offsetX if self._stage.offsetX is not None else 0.0)
            self._offsetXField.valueChanged.connect(self._onOffsetXValueChanged)

        if changedAttrs is None or 'offsetY' in changedAttrs:
            self._offsetYField.valueChanged.disconnect(self._onOffsetYValueChanged)
            self._offsetYField.setValue(self._stage.offsetY if self._stage.offsetY is not None else 0.0)
            self._offsetYField.valueChanged.connect(self._onOffsetYValueChanged)

    def _onTargetsCollectionAboutToChange(self, *args, **kwargs):
        if len(self._preChangeTargetComboBoxIndex) == 0:
            self._targetCombo.currentIndexChanged.disconnect(self._onTargetComboCurrentIndexChanged)
        self._preChangeTargetComboBoxIndex.append(self._targetCombo.currentIndex())

    def _onTargetsCollectionChanged(self, *args, **kwargs):
        if len(self._preChangeTargetComboBoxIndex) > 0:
            prevIndex = self._preChangeTargetComboBoxIndex.pop()
            if len(self._preChangeTargetComboBoxIndex) == 0:
                if prevIndex == -1:
                    self._targetCombo.setCurrentIndex(-1)
                elif prevIndex != self._targetCombo.currentIndex():
                    self._onTargetComboCurrentIndexChanged(self._targetCombo.currentIndex())
                self._targetCombo.currentIndexChanged.connect(self._onTargetComboCurrentIndexChanged)

    def _onTargetComboCurrentIndexChanged(self, index: int):
        if index == -1:
            newKey = None
        else:
            newKey = self._targetsModel.getCollectionItemKeyFromIndex(index)

        if self._stage.targetKey == newKey:
            return

        logger.info(f'Updating AddFromTarget stage targetKey to {newKey!r}')
        self._stage.targetKey = newKey

    def _onRadiusXValueChanged(self, newValue: float):
        newRadius = None if newValue == 0.0 else newValue
        if self._stage.radiusX == newRadius:
            return
        logger.info(f'Updating AddFromTarget stage radiusX to {newRadius}')
        self._stage.radiusX = newRadius

    def _onRadiusYValueChanged(self, newValue: float):
        newRadius = None if newValue == 0.0 else newValue
        if self._stage.radiusY == newRadius:
            return
        logger.info(f'Updating AddFromTarget stage radiusY to {newRadius}')
        self._stage.radiusY = newRadius

    def _onDepthThicknessValueChanged(self, newValue: float):
        if self._stage.depthThickness == newValue:
            return
        logger.info(f'Updating AddFromTarget stage depthThickness to {newValue}')
        self._stage.depthThickness = newValue

    def _onOffsetXValueChanged(self, newValue: float):
        newOffset = None if newValue == 0.0 else newValue
        if self._stage.offsetX == newOffset:
            return
        logger.info(f'Updating AddFromTarget stage offsetX to {newOffset}')
        self._stage.offsetX = newOffset

    def _onOffsetYValueChanged(self, newValue: float):
        newOffset = None if newValue == 0.0 else newValue
        if self._stage.offsetY == newOffset:
            return
        logger.info(f'Updating AddFromTarget stage offsetY to {newOffset}')
        self._stage.offsetY = newOffset

    def deleteLater(self, /):
        logger.debug(f'Deleting AddFromTargetStageWidget for stage {self._stage}')
        self._targetsModel.collection.sigItemsAboutToChange.disconnect(self._onTargetsCollectionAboutToChange)
        self._targetsModel.collection.sigItemsChanged.disconnect(self._onTargetsCollectionChanged)
        super().deleteLater()


@attrs.define(init=False, slots=False, kw_only=True)
class AddFromTwoTargetsStageWidget(ROIStageWidget):
    _stage: AddFromTwoTargets

    _target1Model: FullTargetsTableModel = attrs.field(init=False)
    _target1Combo: QtWidgets.QComboBox = attrs.field(init=False)
    _target2Model: FullTargetsTableModel = attrs.field(init=False)
    _target2Combo: QtWidgets.QComboBox = attrs.field(init=False)
    _minorAxisRatioField: QtWidgets.QDoubleSpinBox = attrs.field(init=False)
    _majorAxisPaddingField: QtWidgets.QDoubleSpinBox = attrs.field(init=False)
    _depthThicknessField: QtWidgets.QDoubleSpinBox = attrs.field(init=False)

    _preChange1: list[int] = attrs.field(init=False, factory=list)
    _preChange2: list[int] = attrs.field(init=False, factory=list)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._target1Model, self._target1Combo = self._buildTargetCombo(
            self._stage.target1Key,
            self._onTarget1ComboCurrentIndexChanged,
            self._onTarget1CollectionAboutToChange,
            self._onTarget1CollectionChanged)
        self._formLayout.addRow('Target 1:', self._target1Combo)

        self._target2Model, self._target2Combo = self._buildTargetCombo(
            self._stage.target2Key,
            self._onTarget2ComboCurrentIndexChanged,
            self._onTarget2CollectionAboutToChange,
            self._onTarget2CollectionChanged)
        self._formLayout.addRow('Target 2:', self._target2Combo)

        self._minorAxisRatioField = QtWidgets.QDoubleSpinBox()
        self._minorAxisRatioField.setRange(0.0, 1e3)
        self._minorAxisRatioField.setSingleStep(0.1)
        self._minorAxisRatioField.setValue(self._stage.minorAxisRatio)
        preventAnnoyingScrollBehaviour(self._minorAxisRatioField)
        self._minorAxisRatioField.valueChanged.connect(self._onMinorAxisRatioValueChanged)
        self._formLayout.addRow('Minor axis ratio:', self._minorAxisRatioField)

        self._majorAxisPaddingField = QtWidgets.QDoubleSpinBox()
        self._majorAxisPaddingField.setRange(-1e3, 1e3)
        self._majorAxisPaddingField.setValue(self._stage.majorAxisPadding)
        preventAnnoyingScrollBehaviour(self._majorAxisPaddingField)
        self._majorAxisPaddingField.valueChanged.connect(self._onMajorAxisPaddingValueChanged)
        self._formLayout.addRow('Major axis padding (mm):', self._majorAxisPaddingField)

        self._depthThicknessField = QtWidgets.QDoubleSpinBox()
        self._depthThicknessField.setRange(0.0, 1e3)
        self._depthThicknessField.setValue(self._stage.depthThickness)
        preventAnnoyingScrollBehaviour(self._depthThicknessField)
        self._depthThicknessField.valueChanged.connect(self._onDepthThicknessValueChanged)
        self._formLayout.addRow('Depth thickness (mm):', self._depthThicknessField)

    def _buildTargetCombo(self, currentKey, comboSlot, aboutSlot, changedSlot):
        model = FullTargetsTableModel(session=self._session)
        combo = QtWidgets.QComboBox()
        combo.setModel(model)
        preventAnnoyingScrollBehaviour(combo)
        model.collection.sigItemsAboutToChange.connect(aboutSlot, priority=1)
        model.collection.sigItemsChanged.connect(changedSlot, priority=-1)
        combo.setCurrentIndex(-1)
        if currentKey is not None:
            index = model.getIndexFromCollectionItemKey(currentKey)
            if index is not None:
                combo.setCurrentIndex(index)
        combo.currentIndexChanged.connect(comboSlot)
        return model, combo

    def _onStageChanged(self, stage: ROIStages.ROIStage, changedAttrs: list[str] | None = None):
        super()._onStageChanged(stage, changedAttrs)

        if changedAttrs is None or 'target1Key' in changedAttrs:
            self._refreshTargetCombo(self._target1Combo, self._target1Model,
                                     self._stage.target1Key, self._onTarget1ComboCurrentIndexChanged)

        if changedAttrs is None or 'target2Key' in changedAttrs:
            self._refreshTargetCombo(self._target2Combo, self._target2Model,
                                     self._stage.target2Key, self._onTarget2ComboCurrentIndexChanged)

        if changedAttrs is None or 'minorAxisRatio' in changedAttrs:
            self._minorAxisRatioField.valueChanged.disconnect(self._onMinorAxisRatioValueChanged)
            self._minorAxisRatioField.setValue(self._stage.minorAxisRatio)
            self._minorAxisRatioField.valueChanged.connect(self._onMinorAxisRatioValueChanged)

        if changedAttrs is None or 'majorAxisPadding' in changedAttrs:
            self._majorAxisPaddingField.valueChanged.disconnect(self._onMajorAxisPaddingValueChanged)
            self._majorAxisPaddingField.setValue(self._stage.majorAxisPadding)
            self._majorAxisPaddingField.valueChanged.connect(self._onMajorAxisPaddingValueChanged)

        if changedAttrs is None or 'depthThickness' in changedAttrs:
            self._depthThicknessField.valueChanged.disconnect(self._onDepthThicknessValueChanged)
            self._depthThicknessField.setValue(self._stage.depthThickness)
            self._depthThicknessField.valueChanged.connect(self._onDepthThicknessValueChanged)

    def _refreshTargetCombo(self, combo: QtWidgets.QComboBox, model: FullTargetsTableModel,
                            key: str | None, slot):
        combo.currentIndexChanged.disconnect(slot)
        if key is None:
            combo.setCurrentIndex(-1)
        else:
            index = model.getIndexFromCollectionItemKey(key)
            combo.setCurrentIndex(index if index is not None else -1)
        combo.currentIndexChanged.connect(slot)

    def _onTarget1CollectionAboutToChange(self, *args, **kwargs):
        if len(self._preChange1) == 0:
            self._target1Combo.currentIndexChanged.disconnect(self._onTarget1ComboCurrentIndexChanged)
        self._preChange1.append(self._target1Combo.currentIndex())

    def _onTarget1CollectionChanged(self, *args, **kwargs):
        if len(self._preChange1) > 0:
            prevIndex = self._preChange1.pop()
            if len(self._preChange1) == 0:
                if prevIndex == -1:
                    self._target1Combo.setCurrentIndex(-1)
                elif prevIndex != self._target1Combo.currentIndex():
                    self._onTarget1ComboCurrentIndexChanged(self._target1Combo.currentIndex())
                self._target1Combo.currentIndexChanged.connect(self._onTarget1ComboCurrentIndexChanged)

    def _onTarget2CollectionAboutToChange(self, *args, **kwargs):
        if len(self._preChange2) == 0:
            self._target2Combo.currentIndexChanged.disconnect(self._onTarget2ComboCurrentIndexChanged)
        self._preChange2.append(self._target2Combo.currentIndex())

    def _onTarget2CollectionChanged(self, *args, **kwargs):
        if len(self._preChange2) > 0:
            prevIndex = self._preChange2.pop()
            if len(self._preChange2) == 0:
                if prevIndex == -1:
                    self._target2Combo.setCurrentIndex(-1)
                elif prevIndex != self._target2Combo.currentIndex():
                    self._onTarget2ComboCurrentIndexChanged(self._target2Combo.currentIndex())
                self._target2Combo.currentIndexChanged.connect(self._onTarget2ComboCurrentIndexChanged)

    def _onTarget1ComboCurrentIndexChanged(self, index: int):
        newKey = None if index == -1 else self._target1Model.getCollectionItemKeyFromIndex(index)
        if self._stage.target1Key == newKey:
            return
        logger.info(f'Updating AddFromTwoTargets stage target1Key to {newKey!r}')
        self._stage.target1Key = newKey

    def _onTarget2ComboCurrentIndexChanged(self, index: int):
        newKey = None if index == -1 else self._target2Model.getCollectionItemKeyFromIndex(index)
        if self._stage.target2Key == newKey:
            return
        logger.info(f'Updating AddFromTwoTargets stage target2Key to {newKey!r}')
        self._stage.target2Key = newKey

    def _onMinorAxisRatioValueChanged(self, newValue: float):
        if self._stage.minorAxisRatio == newValue:
            return
        logger.info(f'Updating AddFromTwoTargets stage minorAxisRatio to {newValue}')
        self._stage.minorAxisRatio = newValue

    def _onMajorAxisPaddingValueChanged(self, newValue: float):
        if self._stage.majorAxisPadding == newValue:
            return
        logger.info(f'Updating AddFromTwoTargets stage majorAxisPadding to {newValue}')
        self._stage.majorAxisPadding = newValue

    def _onDepthThicknessValueChanged(self, newValue: float):
        if self._stage.depthThickness == newValue:
            return
        logger.info(f'Updating AddFromTwoTargets stage depthThickness to {newValue}')
        self._stage.depthThickness = newValue

    def deleteLater(self, /):
        logger.debug(f'Deleting AddFromTwoTargetsStageWidget for stage {self._stage}')
        self._target1Model.collection.sigItemsAboutToChange.disconnect(self._onTarget1CollectionAboutToChange)
        self._target1Model.collection.sigItemsChanged.disconnect(self._onTarget1CollectionChanged)
        self._target2Model.collection.sigItemsAboutToChange.disconnect(self._onTarget2CollectionAboutToChange)
        self._target2Model.collection.sigItemsChanged.disconnect(self._onTarget2CollectionChanged)
        super().deleteLater()


@attrs.define(init=False, slots=False, kw_only=True)
class CombineStageWidget(ROIStageWidget):
    _stage: Combine

    _roiKeyContainerLayout: QtWidgets.QVBoxLayout = attrs.field(init=False)
    _addROIKeyBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _roiKeyWidgets: list[tuple[QtWidgets.QComboBox, QtWidgets.QWidget]] = attrs.field(
        init=False, factory=list)  # (combo, rowContainer)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        roiKeyContainer = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        roiKeyContainer.setLayout(layout)
        self._roiKeyContainerLayout = layout
        self._formLayout.addRow('ROIs:', roiKeyContainer)

        self._addROIKeyBtn = QtWidgets.QPushButton(getIcon('mdi6.plus'), 'Add ROI')
        self._addROIKeyBtn.clicked.connect(self._onAddROIKeyClicked)
        layout.addWidget(self._addROIKeyBtn)

        self._session.ROIs.sigItemsAboutToChange.connect(
            self._onROIsAboutToChange, priority=1)
        self._session.ROIs.sigItemsChanged.connect(
            self._onROIsChanged, priority=-1)

        self._rebuildROIKeyWidgets()

    def _getAvailableROIKeys(self) -> list[str]:
        return [key for key in self._session.ROIs.keys() if key != self._roi.key]

    def _populateCombo(self, combo: QtWidgets.QComboBox, currentValue: str | None):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem('(pipeline input)')  # index 0 → None
        for key in self._getAvailableROIKeys():
            combo.addItem(key)
        if currentValue is None:
            combo.setCurrentIndex(0)
        else:
            idx = combo.findText(currentValue)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _rebuildROIKeyWidgets(self):
        for _combo, rowContainer in self._roiKeyWidgets:
            rowContainer.setParent(None)
            rowContainer.deleteLater()
        self._roiKeyWidgets.clear()

        for key in self._stage.roiKeys:
            self._appendROIKeyWidget(key)

        self._updateAddBtnState()

    def _appendROIKeyWidget(self, key: str | None):
        rowContainer = QtWidgets.QWidget()
        rowLayout = QtWidgets.QHBoxLayout()
        rowLayout.setContentsMargins(0, 0, 0, 0)
        rowContainer.setLayout(rowLayout)

        combo = QtWidgets.QComboBox()
        preventAnnoyingScrollBehaviour(combo)
        self._populateCombo(combo, key)
        combo.currentIndexChanged.connect(
            lambda _idx, c=combo: self._onROIKeyComboChanged(c))
        rowLayout.addWidget(combo, 1)

        removeBtn = QtWidgets.QPushButton(getIcon('mdi6.delete'), '')
        removeBtn.setFixedSize(24, 24)
        removeBtn.clicked.connect(
            lambda _, rc=rowContainer: self._onRemoveROIKeyClicked(rc))
        rowLayout.addWidget(removeBtn)

        insertPos = self._roiKeyContainerLayout.count() - 1
        self._roiKeyContainerLayout.insertWidget(insertPos, rowContainer)
        self._roiKeyWidgets.append((combo, rowContainer))

    def _updateAddBtnState(self):
        maxNum = self._stage._maxNumROIs
        self._addROIKeyBtn.setEnabled(maxNum is None or len(self._stage.roiKeys) < maxNum)

    def _onAddROIKeyClicked(self, _):
        self._stage.roiKeys = list(self._stage.roiKeys) + [None]

    def _onRemoveROIKeyClicked(self, rowContainer: QtWidgets.QWidget):
        row_index = next(
            i for i, (_c, rc) in enumerate(self._roiKeyWidgets) if rc is rowContainer)
        new_keys = list(self._stage.roiKeys)
        del new_keys[row_index]
        self._stage.roiKeys = new_keys

    def _onROIKeyComboChanged(self, combo: QtWidgets.QComboBox):
        row_index = next(
            i for i, (c, _rc) in enumerate(self._roiKeyWidgets) if c is combo)
        new_key = None if combo.currentIndex() == 0 else combo.currentText()
        current_keys = list(self._stage.roiKeys)
        if row_index >= len(current_keys) or current_keys[row_index] == new_key:
            return
        current_keys[row_index] = new_key
        self._stage.sigItemChanged.disconnect(self._onStageChanged)
        try:
            self._stage.roiKeys = current_keys
        finally:
            self._stage.sigItemChanged.connect(self._onStageChanged)

    def _onStageChanged(self, stage, changedAttrs=None):
        super()._onStageChanged(stage, changedAttrs)
        if changedAttrs is None or 'roiKeys' in changedAttrs:
            self._rebuildROIKeyWidgets()

    def _onROIsAboutToChange(self, *args, **kwargs):
        self._savedComboValues = [
            None if c.currentIndex() == 0 else c.currentText()
            for c, _rc in self._roiKeyWidgets
        ]

    def _onROIsChanged(self, *args, **kwargs):
        saved = getattr(self, '_savedComboValues', [])
        for i, (combo, _rc) in enumerate(self._roiKeyWidgets):
            self._populateCombo(combo, saved[i] if i < len(saved) else None)
        self._updateAddBtnState()

    def deleteLater(self, /):
        logger.debug(f'Deleting CombineStageWidget for stage {self._stage}')
        self._session.ROIs.sigItemsAboutToChange.disconnect(self._onROIsAboutToChange)
        self._session.ROIs.sigItemsChanged.disconnect(self._onROIsChanged)
        super().deleteLater()