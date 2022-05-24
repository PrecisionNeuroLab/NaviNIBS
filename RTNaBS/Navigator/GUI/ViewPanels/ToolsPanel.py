from __future__ import annotations

import asyncio

import appdirs
import attrs
from datetime import datetime
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
from RTNaBS.util import makeStrUnique
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.GUI.QFileSelectWidget import QFileSelectWidget
from RTNaBS.util.GUI.QTableWidgetDragRows import QTableWidgetDragRows
from RTNaBS.Navigator.Model.Session import Session, Tools, Tool, CoilTool


logger = logging.getLogger(__name__)


@attrs.define
class ToolWidget:
    _tool: Tool

    _wdgt: QtWidgets.QWidget = attrs.field(init=False)
    _formLayout: QtWidgets.QFormLayout = attrs.field(init=False)
    _key: QtWidgets.QLineEdit = attrs.field(init=False)
    _usedFor: QtWidgets.QComboBox = attrs.field(init=False)
    _isActive: QtWidgets.QCheckBox = attrs.field(init=False)
    _romFilepath: QFileSelectWidget = attrs.field(init=False)
    _stlFilepath: QFileSelectWidget = attrs.field(init=False)
    _trackerToToolTransf: QtWidgets.QLineEdit = attrs.field(init=False)

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

        self._stlFilepath = QFileSelectWidget(
            browseMode='getOpenFilename',
            filepath=self._tool.stlFilepath,
            showRelativeTo=self._tool.filepathsRelTo,
            extFilters='STL (*.stl)',
            browseCaption='Choose 3D model for tracker visualization'
        )
        self._stlFilepath.sigFilepathChanged.connect(lambda filepath: self._onStlFilepathEdited())
        formContainer.layout().addRow('STL filepath', self._stlFilepath)

        with np.printoptions(precision=2):
            self._stlToTrackerTransf = QtWidgets.QLineEdit('{}'.format(self._tool.stlToTrackerTransf))
        self._stlToTrackerTransf.editingFinished.connect(self._onStlToTrackerTransfEdited)

        with np.printoptions(precision=2):
            self._trackerToToolTransf = QtWidgets.QLineEdit('{}'.format(self._tool.trackerToToolTransf))
        self._trackerToToolTransf.editingFinished.connect(self._onTrackerToToolTransfEdited)



    @property
    def wdgt(self):
        return self._wdgt

    def _onKeyEdited(self):
        self._tool.key = self._key.text()

    def _onUsedForEdited(self):
        self._tool.usedFor = self._usedFor.currentText()

    def _onIsActiveEdited(self):
        raise NotImplementedError()  # TODO

    def _onRomFilepathEdited(self):
        self._tool.romFilepath = self._romFilepath.filepath

    def _onStlFilepathEdited(self):
        self._tool.stlFilepath = self._stlFilepath.filepath

    def _onStlToTrackerTransfEdited(self):
        raise NotImplementedError()  # TODO

    def _onTrackerToToolTransfEdited(self):
        raise NotImplementedError()  # TODO

    def _onToolChanged(self):
        self._key.setText(self._tool.key)
        self._usedFor.setCurrentIndex(self._usedFor.findText(self._tool.usedFor) if self._tool.usedFor is not None else -1)  # TODO: check for change in type that we can't handle without reinstantiating
        self._isActive.setChecked(self._tool.isActive)
        self._romFilepath.filepath = self._tool.romFilepath
        self._stlFilepath.filepath = self._tool.stlFilepath
        with np.printoptions(precision=2):
            self._stlToTrackerTransf.setText('{}'.format(self._tool.stlToTrackerTransf))
            self._trackerToToolTransf.setText('{}'.format(self._tool.trackerToToolTransf))


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
    _hasBeenActivated: bool = attrs.field(init=False, default=False)

    def __attrs_post_init__(self):
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

        self.sigPanelActivated.connect(self._onPanelActivated)

    def _onPanelActivated(self):
        self._hasBeenActivated = True
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
            self._toolWdgt = CoilToolWidget(tool=self.session.tools[currentToolKey])
        else:
            self._toolWdgt = ToolWidget(tool=self.session.tools[currentToolKey])
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










