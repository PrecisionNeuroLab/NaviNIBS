from __future__ import annotations

import asyncio

import appdirs
import attrs
from datetime import datetime
import json
import logging
import numpy as np
import os
import pathlib
import pyvista as pv
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import shutil
import typing as tp

from . import MainViewPanel
from RTNaBS.Navigator.Model.Session import Session, Tools, Tool, CoilTool
from RTNaBS.util import makeStrUnique
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import transformToString, stringToTransform
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.util.GUI.QLineEdit import QLineEditWithValidationFeedback
from RTNaBS.util.GUI.QTableWidgetDragRows import QTableWidgetDragRows
from RTNaBS.util.GUI.QValidators import OptionalTransformValidator


logger = logging.getLogger(__name__)


@attrs.define
class ToolWidget:
    _tool: Tool
    _session: Session  # only used for cross-tool references like coil calibration

    _wdgt: QtWidgets.QWidget = attrs.field(init=False)
    _formLayout: QtWidgets.QFormLayout = attrs.field(init=False)
    _key: QtWidgets.QLineEdit = attrs.field(init=False)
    _usedFor: QtWidgets.QComboBox = attrs.field(init=False)
    _isActive: QtWidgets.QCheckBox = attrs.field(init=False)
    _romFilepath: QFileSelectWidget = attrs.field(init=False)
    _trackerStlFilepath: QFileSelectWidget = attrs.field(init=False)
    _toolStlFilepath: QFileSelectWidget = attrs.field(init=False)
    _toolToTrackerTransf: QtWidgets.QLineEdit = attrs.field(init=False)
    _trackerStlToTrackerTransf: QtWidgets.QLineEdit = attrs.field(init=False)

    def __attrs_post_init__(self):
        self._wdgt = QtWidgets.QGroupBox('Selected tool: {}'.format(self._tool.key))
        self._wdgt.setLayout(QtWidgets.QVBoxLayout())

        formContainer = QtWidgets.QWidget()
        self._formLayout = QtWidgets.QFormLayout()
        formContainer.setLayout(self._formLayout)
        self._wdgt.layout().addWidget(formContainer)

        #self._tool.sigToolChanged.connect(lambda key: self._onToolChanged())

        self._key = QtWidgets.QLineEdit(self._tool.key)
        self._key.editingFinished.connect(self._onKeyEdited)
        formContainer.layout().addRow('Key', self._key)

        self._usedFor = QtWidgets.QComboBox()
        self._usedFor.insertItems(0, ['coil', 'subject', 'pointer', 'calibration'])
        if len(self._tool.usedFor) > 0:
            index = self._usedFor.findText(self._tool.usedFor)
            assert index != -1, 'Unexpected tool type: {}'.format(self._tool.usedFor)
        else:
            index = -1
        self._usedFor.setCurrentIndex(index)
        self._usedFor.currentIndexChanged.connect(lambda index: self._onUsedForEdited())
        formContainer.layout().addRow('Type', self._usedFor)

        self._isActive = QtWidgets.QCheckBox('')
        self._isActive.setChecked(self._tool.isActive)
        self._isActive.stateChanged.connect(lambda state: self._onIsActiveEdited())
        formContainer.layout().addRow('Is active', self._isActive)

        self._romFilepath = QFileSelectWidget(
            browseMode='getOpenFilename',
            filepath=self._tool.romFilepath,
            showRelativeTo=self._tool.filepathsRelTo,
            extFilters='ROM (*.rom)',
            browseCaption='Choose tracker definition file',
        )
        self._romFilepath.sigFilepathChanged.connect(lambda filepath: self._onRomFilepathEdited())
        formContainer.layout().addRow('ROM filepath', self._romFilepath)

        self._trackerStlFilepath = QFileSelectWidget(
            browseMode='getOpenFilename',
            filepath=self._tool.trackerStlFilepath,
            showRelativeTo=self._tool.filepathsRelTo,
            extFilters='STL (*.stl)',
            browseCaption='Choose 3D model for tracker visualization'
        )
        self._trackerStlFilepath.sigFilepathChanged.connect(lambda filepath: self._onTrackerStlFilepathEdited())
        formContainer.layout().addRow('Tracker STL filepath', self._trackerStlFilepath)

        self._toolStlFilepath = QFileSelectWidget(
            browseMode='getOpenFilename',
            filepath=self._tool.toolStlFilepath,
            showRelativeTo=self._tool.filepathsRelTo,
            extFilters='STL (*.stl)',
            browseCaption='Choose 3D model for tool visualization'
        )
        self._toolStlFilepath.sigFilepathChanged.connect(lambda filepath: self._onToolStlFilepathEdited())
        formContainer.layout().addRow('Tool STL filepath', self._toolStlFilepath)

        self._trackerStlToTrackerTransf = QLineEditWithValidationFeedback(self._transfToStr(self._tool.trackerStlToTrackerTransf))
        self._trackerStlToTrackerTransf.setValidator(OptionalTransformValidator())
        self._trackerStlToTrackerTransf.editingFinished.connect(self._onTrackerStlToTrackerTransfEdited)
        formContainer.layout().addRow('Tracker STL to tracker transf', self._trackerStlToTrackerTransf)

        self._toolToTrackerTransf = QLineEditWithValidationFeedback(self._transfToStr(self._tool.toolToTrackerTransf))
        self._toolToTrackerTransf.setValidator(OptionalTransformValidator())
        self._toolToTrackerTransf.editingFinished.connect(self._onToolToTrackerTransfEdited)
        formContainer.layout().addRow('Tool to tracker transf', self._toolToTrackerTransf)

    @property
    def wdgt(self):
        return self._wdgt

    def _onKeyEdited(self):
        self._tool.key = self._key.text()

    def _onUsedForEdited(self):
        self._tool.usedFor = self._usedFor.currentText()

    def _onIsActiveEdited(self):
        self._tool.isActive = self._isActive.isChecked()

    def _onRomFilepathEdited(self):
        self._tool.romFilepath = self._romFilepath.filepath

    def _onTrackerStlFilepathEdited(self):
        self._tool.trackerStlFilepath = self._trackerStlFilepath.filepath

    def _onToolStlFilepathEdited(self):
        self._tool.toolStlFilepath = self._toolStlFilepath.filepath

    def _onTrackerStlToTrackerTransfEdited(self):
        newTransf = self._strToTransf(self._trackerStlToTrackerTransf.text())
        if self._transfToStr(newTransf) == self._transfToStr(self._tool.trackerStlToTrackerTransf):
            # no change
            return
        logger.info('User edited {} trackerStlToTrackerTransf: {}'.format(self._tool.key, newTransf))
        self._tool.trackerStlToTrackerTransf = newTransf

    def _onToolToTrackerTransfEdited(self):
        newTransf = self._strToTransf(self._toolToTrackerTransf.text())
        if self._transfToStr(newTransf) == self._transfToStr(self._tool.toolToTrackerTransf):
            # no change
            return
        logger.info('User edited {} toolToTrackerTransf: {}'.format(self._tool.key, newTransf))
        self._tool.toolToTrackerTransf = newTransf

    def _onToolChanged(self):
        self._key.setText(self._tool.key)
        self._usedFor.setCurrentIndex(self._usedFor.findText(self._tool.usedFor) if self._tool.usedFor is not None else -1)  # TODO: check for change in type that we can't handle without reinstantiating
        self._isActive.setChecked(self._tool.isActive)
        self._romFilepath.filepath = self._tool.romFilepath
        self._trackerStlFilepath.filepath = self._tool.trackerStlFilepath
        self._toolStlFilepath.filepath = self._tool.toolStlFilepath
        self._trackerStlToTrackerTransf.setText(self._transfToStr(self._tool.trackerStlToTrackerTransf))
        self._toolToTrackerTransf.setText(self._transfToStr(self._tool.toolToTrackerTransf))

    @staticmethod
    def _transfToStr(transf: tp.Optional[np.ndarray]) -> str:
        if transf is None:
            return ''
        else:
            return transformToString(transf, precision=6)

    @staticmethod
    def _strToTransf(inputStr: str) -> tp.Optional[np.ndarray]:
        if len(inputStr.strip()) == 0:
            return None
        else:
            return stringToTransform(inputStr)


@attrs.define
class CoilToolWidget(ToolWidget):
    _tool: CoilTool

    _coilStlFilepath: QFileSelectWidget = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._coilStlFilepath = QFileSelectWidget(
            browseMode='getOpenFilename',
            filepath=self._tool.coilStlFilepath,
            showRelativeTo=self._tool.filepathsRelTo,
            extFilters='STL (*.stl)',
            browseCaption='Choose 3D model for coil visualization'
        )
        self._coilStlFilepath.sigFilepathChanged.connect(lambda filepath: self._onCoilStlFilepathEdited())
        self._formLayout.insertRow(
            self._formLayout.getWidgetPosition(self._stlFilepath)[0]+1,
            QtWidgets.QLabel('Coil STL filepath'),
            self._coilStlFilepath)

    def _onCoilStlFilepathEdited(self):
        self._tool.coilStlFilepath = self._coilStlFilepath.filepath


@attrs.define
class ToolsPanel(MainViewPanel):
    _tblWdgt: QtWidgets.QTableWidget = attrs.field(init=False)
    _tblToolKeys: tp.List[str] = attrs.field(init=False, factory=list)
    _tblActiveToolKeys: tp.List[str] = attrs.field(init=False, factory=list)
    _selectedToolKey: tp.Optional[str] = attrs.field(default=None)
    _toolWdgt: tp.Optional[ToolWidget] = attrs.field(init=False, default=None)
    _wdgts: tp.Dict[str, QtWidgets.QWidget] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._wdgt.setLayout(QtWidgets.QHBoxLayout())

        container = QtWidgets.QGroupBox('Tools')
        container.setLayout(QtWidgets.QVBoxLayout())
        self._wdgt.layout().addWidget(container)
        self._wdgt.layout().setAlignment(container, QtCore.Qt.AlignLeft)
        container.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.MinimumExpanding)

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QGridLayout())
        container.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Add')
        btn.clicked.connect(self._onAddBtnClicked)
        btnContainer.layout().addWidget(btn, 0, 0)

        btn = QtWidgets.QPushButton('Duplicate')
        btn.clicked.connect(self._onDuplicateBtnClicked)
        btnContainer.layout().addWidget(btn, 0, 1)

        btn = QtWidgets.QPushButton('Delete')
        btn.clicked.connect(self._onDeleteBtnClicked)
        btnContainer.layout().addWidget(btn, 0, 2)

        self._tblWdgt = QTableWidgetDragRows(0, 2)
        self._tblWdgt.setHorizontalHeaderLabels(['Tool', 'Active?'])
        self._tblWdgt.sigDragAndDropReordered.connect(self._onDragAndDropReorderedRows)
        self._tblWdgt.currentCellChanged.connect(self._onTblCurrentCellChanged)
        container.layout().addWidget(self._tblWdgt)

    def _onPanelActivated(self):
        super()._onPanelActivated()
        self._onToolsChanged()

    def _onSessionSet(self):
        super()._onSessionSet()
        self.session.tools.sigToolsChanged.connect(self._onToolsChanged)
        self._onToolsChanged()

    def _onTblCurrentCellChanged(self, currentRow: int, currentCol: int, previousRow: int, previousCol: int):
        if previousRow == currentRow:
            return  # no change in row selection
        self._updateSelectedToolWdgt()

    def _onDragAndDropReorderedRows(self):
        newOrder = [self._tblWdgt.item(iR, 0).text() for iR in range(self._tblWdgt.rowCount())]
        logger.info('Reordering tools: {}'.format(newOrder))
        self.session.tools.setTools([self.session.tools[key] for key in newOrder])

    def _onToolsChanged(self, changedKeys: tp.Optional[str] = None):
        logger.debug('Tools changed.')

        if changedKeys is None:
            changedKeys = list(self.session.tools.keys())

        newTblToolKeys = list(self.session.tools.keys())
        newTblActiveToolKeys = [key for key, tool in self.session.tools.items() if tool.isActive]
        if self._tblToolKeys != newTblToolKeys or self._tblActiveToolKeys != newTblActiveToolKeys:
            # order, number, isActive, or keys changed for existing tools. Repopulate table
            prevSelectedToolKey = self._getTblCurrentToolKey()
            self._tblWdgt.clearContents()
            self._tblWdgt.setRowCount(len(self.session.tools))
            for iTool, (key, tool) in enumerate(self.session.tools.items()):
                item = QtWidgets.QTableWidgetItem(key)
                item.setFlags(QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)
                self._tblWdgt.setItem(iTool, 0, item)
                item = QtWidgets.QTableWidgetItem()  # TODO: determine if necessary
                self._tblWdgt.setItem(iTool, 1, item)
                wdgt = QtWidgets.QCheckBox('')
                wdgt.setChecked(tool.isActive)
                wdgt.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
                wdgt.setFocusPolicy(QtCore.Qt.NoFocus)
                self._tblWdgt.setCellWidget(iTool, 1, wdgt)
            self._tblToolKeys = newTblToolKeys
            self._tblActiveToolKeys = newTblActiveToolKeys
            if prevSelectedToolKey in self._tblToolKeys:
                # restore previously selected row
                self._tblWdgt.setCurrentCell(self._tblToolKeys.index(prevSelectedToolKey), 0)

        currentToolKey = self._getTblCurrentToolKey()
        if currentToolKey in changedKeys:
            self._updateSelectedToolWdgt()

    def _updateSelectedToolWdgt(self):
        logger.debug('Updating selected tool widget')
        currentToolKey = self._getTblCurrentToolKey()
        # TODO: if possible, only update specific fields rather than fully recreating widget
        if self._toolWdgt is not None:
            self._toolWdgt.wdgt.deleteLater()  # TODO: verify this is correct way to remove from layout and also delete children
            self._toolWdgt = None

        if currentToolKey is None:
            return


        if isinstance(self.session.tools[currentToolKey], CoilTool):
            ToolWidgetCls = CoilToolWidget
        else:
            ToolWidgetCls = ToolWidget

        self._toolWdgt = ToolWidgetCls(tool=self.session.tools[currentToolKey], session=self.session)
        self._wdgt.layout().addWidget(self._toolWdgt.wdgt)

    def _getTblCurrentToolKey(self) -> tp.Optional[str]:
        curItem = self._tblWdgt.currentItem()
        if curItem is None:
            # no item selected
            return None
        return self._tblWdgt.item(curItem.row(), 0).text()

    def _onAddBtnClicked(self, checked: bool):
        logger.info('Add tool btn clicked')
        self.session.tools.addToolFromDict(dict(key=makeStrUnique('Tool', self._tblToolKeys), usedFor='coil'))

    def _onDuplicateBtnClicked(self, checked: bool):
        logger.info('Duplicate tool btn clicked')
        toolDict = self.session.tools[self._getTblCurrentToolKey()].asDict().copy()
        toolDict['key'] = makeStrUnique(toolDict['key'], self._tblToolKeys)
        self.session.tools.addToolFromDict(toolDict)

    def _onDeleteBtnClicked(self, checked: bool):
        key = self._getTblCurrentToolKey()
        if key is None:
            # no tool selected
            return
        logger.info('Deleting {} tool'.format(key))
        self.session.tools.deleteTool(key=key)










