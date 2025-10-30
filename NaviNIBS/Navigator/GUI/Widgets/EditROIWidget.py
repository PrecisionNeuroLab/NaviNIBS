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
from NaviNIBS.Navigator.GUI.Widgets import EditROIStageWidgets as StageWidgets
from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.Navigator.Model.Calculations import getClosestPointToPointOnMesh
from NaviNIBS.util import exceptionToStr
from NaviNIBS.util.Asyncio import asyncWait
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.Transforms import applyTransform, invertTransform, composeTransform, concatenateTransforms, applyDirectionTransform, calculateRotationMatrixFromVectorToVector
from NaviNIBS.util import makeStrUnique
from NaviNIBS.util.GUI.Icons import getIcon
from NaviNIBS.util.GUI.QDial import AngleDial
from NaviNIBS.util.GUI.QScrollContainer import QScrollContainer
from NaviNIBS.util.GUI.QTextEdit import AutosizingPlainTextEdit, TextEditWithDrafting
from NaviNIBS.util.GUI.QMouseWheelAdjustmentGuard import preventAnnoyingScrollBehaviour
from NaviNIBS.util.pyvista import DefaultBackgroundPlotter, RemotePlotterProxy


logger = logging.getLogger(__name__)



@attrs.define(init=False, slots=False)
class EditROIInnerWidget(QtWidgets.QWidget):
    _roi: ROIs.ROI
    _session: Session = attrs.field(repr=False)
    _linkTo3DView: Surf3DView | None = attrs.field(default=None, repr=False)

    def __init__(self, *args, parent: QtWidgets.QWidget | None = None, **kwargs):
        super().__init__(parent=parent)
        self.__attrs_init__(*args, **kwargs)

    def __attrs_post_init__(self):
        pass

    def deleteLater(self, /):
        logger.debug(f'Deleting EditROIInnerWidget for ROI {self._roi.key}')
        super().deleteLater()

    def __del__(self):
        logger.debug(f'Deleted EditROIInnerWidget for ROI {self._roi.key}')


@attrs.define(init=False, slots=False, kw_only=True)
class EditPipelineROIInnerWidget(EditROIInnerWidget):
    _roi: ROIs.PipelineROI
    _innerLayout: QtWidgets.QVBoxLayout = attrs.field(init=False)

    _stageWidgets: list[StageWidgets.ROIStageWidget] = attrs.field(factory=list, init=False)
    _addStageBtn: QtWidgets.QPushButton = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        wdgt = QtWidgets.QGroupBox('Pipeline')
        layout.addWidget(wdgt)

        innerLayout = QtWidgets.QVBoxLayout()
        innerLayout.setContentsMargins(0, 0, 0, 0)
        self._innerLayout = innerLayout
        wdgt.setLayout(innerLayout)

        btn = QtWidgets.QPushButton(getIcon('mdi6.plus'), 'Add Stage')
        innerLayout.addWidget(btn)
        btn.clicked.connect(self._onAddStageClicked)
        self._addStageBtn = btn

        self._roi.stages.sigItemsChanged.connect(self._onStagesChanged)

        self._rebuildStageWidgets()

        # TODO: add drag and drop support for reordering stages

    def _onAddStageClicked(self, _):
        stage = ROIs.PassthroughStage()
        self._roi.stages.append(stage)

    def _onStagesChanged(self, changedStages: set[ROIs.ROIStage], changedAttrs: list[str] | None = None):
        # add new stage widgets
        needsRebuild = False
        for stage in changedStages:
            try:
                index = self._roi.stages.index(stage)
            except ValueError:
                # need to delete a stage
                needsRebuild = True
                break

            if index < len(self._stageWidgets) and stage is self._stageWidgets[index].stage:
                # stage widget already exists at correct index
                # assume it will handle updates
                continue
            else:
                # need to add new widget
                needsRebuild = True

        if needsRebuild:
            self._rebuildStageWidgets()

    def _rebuildStageWidgets(self):
        logger.debug(f'Rebuilding stage widgets')

        # clear existing widgets
        for stageWidget in self._stageWidgets:
            stageWidget.setVisible(False)
            stageWidget.setParent(None)
            stageWidget.deleteLater()
        self._stageWidgets.clear()

        # add widgets for all stages
        for index, stage in enumerate(self._roi.stages):
            match stage.type:
                case ROIs.PassthroughStage.type:
                    StageWidgetCls = StageWidgets.PassthroughStageWidget
                case ROIs.SelectSurfaceMesh.type:
                    StageWidgetCls = StageWidgets.SelectSurfaceMeshStageWidget
                case ROIs.AddFromSeedPoint.type:
                    StageWidgetCls = StageWidgets.AddFromSeedPointStageWidget
                case _:
                    StageWidgetCls = StageWidgets.JsonReprStageWidget

            stageWidget = StageWidgetCls(
                stage=stage,
                roi=self._roi,
                roiWidget=self,
                session=self._session,
                linkTo3DView=self._linkTo3DView,
            )

            self._innerLayout.insertWidget(index, stageWidget)
            self._stageWidgets.append(stageWidget)

    def changeTypeOfStage(self, stage: ROIs.ROIStage, newType: str):
        oldStage = stage
        index = self._roi.stages.index(oldStage)
        stageClass = self._roi.stageLibrary[newType]
        d = {k: v for k, v in oldStage.asDict().items() if k in ('label',)}
        d['session'] = oldStage.session
        # note: could possibly copy more fields here depending on stage types
        StageCls = self._roi.stages.stageLibrary[newType]
        newStage = StageCls(**d)
        self._roi.stages[index] = newStage

    def deleteLater(self, /):
        logger.debug(f'Deleting EditPipelineROIInnerWidget for ROI {self._roi.key}')
        super().deleteLater()
        self._roi.stages.sigItemsChanged.disconnect(self._onStagesChanged)
        for stageWidget in self._stageWidgets:
            stageWidget.deleteLater()


@attrs.define(eq=False)
class EditROIWidget:
    _session: Session = attrs.field(repr=False)
    _wdgt: QtWidgets.QWidget = attrs.field(factory=lambda: QtWidgets.QGroupBox('Edit ROI'))
    _doTrackModelSelectedROI: bool = True
    _linkTo3DView: Surf3DView | None = attrs.field(default=None, repr=False)

    _scroll: QScrollContainer = attrs.field(init=False)
    _roiComboBox: QtWidgets.QComboBox = attrs.field(init=False, factory=QtWidgets.QComboBox)
    _roiSpecificContainer: QtWidgets.QWidget | None = attrs.field(init=False, default=None)
    _roiSpecificInnerWdgt: EditROIInnerWidget | None = attrs.field(init=False, default=None)

    _ROI: ROIs.ROI | None = attrs.field(init=False, default=None)
    _roisModel: ROIsTableModel = attrs.field(init=False)

    def __attrs_post_init__(self):
        outerLayout = QtWidgets.QVBoxLayout()
        self._wdgt.setLayout(outerLayout)
        outerLayout.setContentsMargins(0, 0, 0, 0)

        innerLayout = QtWidgets.QVBoxLayout()
        innerLayout.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollContainer(innerContainerLayout=innerLayout)
        self._scroll.scrollArea.setFrameShape(QtWidgets.QFrame.NoFrame)
        outerLayout.addWidget(self._scroll.scrollArea)
        self._scroll.scrollArea.setSizePolicy(QtWidgets.QSizePolicy.Minimum,
                                              QtWidgets.QSizePolicy.Preferred)

        layout = QtWidgets.QFormLayout()
        wdgt = QtWidgets.QWidget()
        wdgt.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Preferred)
        wdgt.setLayout(layout)
        innerLayout.addWidget(wdgt)
        innerLayout.addStretch()

        self._roisModel = ROIsTableModel(session=self._session)

        self._roiComboBox.setModel(self._roisModel)
        self._roiComboBox.currentIndexChanged.connect(self._onROIComboBoxCurrentIndexChanged)
        preventAnnoyingScrollBehaviour(self._roiComboBox)
        self._roisModel.sigSelectionChanged.connect(self._onModelSelectionChanged)
        layout.addRow('Editing ROI:', self._roiComboBox)

        self._roiComboBox.setCurrentIndex(-1)
        self._onROIComboBoxCurrentIndexChanged(self._roiComboBox.currentIndex())  # enable/disable widgets

    @property
    def wdgt(self):
        return self._wdgt

    @property
    def roiComboBox(self):
        return self._roiComboBox

    @property
    def ROI(self):
        return self._ROI

    @ROI.setter
    def ROI(self, newROI: ROIs.ROI | None):
        if self._ROI is newROI:
            return

        if self._ROI is not None:
            self._ROI.sigItemChanged.disconnect(self._onROIItemChanged)

        self._ROI = newROI

        if self._ROI is not None:
            self._ROI.sigItemChanged.connect(self._onROIItemChanged)

        self._reinit()

    @property
    def roisModel(self):
        return self._roisModel

    @roisModel.setter
    def roisModel(self, newModel: ROIsTableModel):
        if self._roisModel is newModel:
            return

        self._roisModel.sigSelectionChanged.disconnect(self._onModelSelectionChanged)

        self._roisModel = newModel
        self._roiComboBox.setModel(self._roisModel)

        self._roisModel.sigSelectionChanged.connect(self._onModelSelectionChanged)

    def _reinit(self):
        if self._roiSpecificContainer is not None:
            self._roiSpecificContainer.setParent(None)
            self._roiSpecificContainer.deleteLater()
            self._roiSpecificContainer.setVisible(False)
            self._roiSpecificContainer = None
            self._roiSpecificInnerWdgt.deleteLater()
            self._roiSpecificInnerWdgt = None

        if self._ROI is None:
            return

        self._roiSpecificContainer = QtWidgets.QWidget()
        self._scroll.innerContainerLayout.insertWidget(self._scroll.innerContainerLayout.count()-1,
                                                       self._roiSpecificContainer)

        if isinstance(self._ROI, ROIs.PipelineROI):
            EditROICls = EditPipelineROIInnerWidget
        else:
            raise NotImplementedError

        self._roiSpecificInnerWdgt = EditROICls(
            roi=self._ROI,
            session=self._session,
            linkTo3DView=self._linkTo3DView
        )
        innerLayout = QtWidgets.QVBoxLayout()
        innerLayout.setContentsMargins(0, 0, 0, 0)
        self._roiSpecificContainer.setLayout(innerLayout)
        innerLayout.addWidget(self._roiSpecificInnerWdgt)

    def _onROIItemChanged(self, roiKey: str, attribsChanged: list[str] | None = None):
        pass

    def _onROIComboBoxCurrentIndexChanged(self, index: int):

        if index == -1:
            self.ROI = None
            if self._roiSpecificContainer is not None:
                self._roiSpecificContainer.setVisible(False)
        else:
            roiKey = self._roisModel.getCollectionItemKeyFromIndex(index)
            ROI = self._session.ROIs[roiKey]
            self.ROI = ROI

            if self._roiSpecificContainer is not None:
                self._roiSpecificContainer.setVisible(True)

    def _onModelSelectionChanged(self, changedKeys: list[str]):
        if not self._doTrackModelSelectedROI:
            return

        # make sure current combobox item is in selection

        menuCurrentROIIndex = self._roiComboBox.currentIndex()
        if menuCurrentROIIndex != -1:
            try:
                menuCurrentROIKey = self._roisModel.getCollectionItemKeyFromIndex(menuCurrentROIIndex)
            except IndexError:
                # model probably just changed to reduce number of items
                return  # TODO: determine better behavior to handle this instead of just ignoring

            if menuCurrentROIKey not in changedKeys:
                return

            if self._roisModel.getCollectionItemIsSelected(menuCurrentROIKey):
                return

        firstSelectedROIKey = None
        for roi in self._session.ROIs.values():
            if roi.isSelected:
                firstSelectedROIKey = roi.key
                break

        if firstSelectedROIKey is None:
            self._roiComboBox.setCurrentIndex(-1)
        else:
            index = self._roisModel.getIndexFromCollectionItemKey(firstSelectedROIKey)
            if False:
                self._roiComboBox.setCurrentIndex(index)
            else:
                # run after short delay to prevent issue with nested model changes
                QtCore.QTimer.singleShot(0, lambda index=index: self._roiComboBox.setCurrentIndex(index))
