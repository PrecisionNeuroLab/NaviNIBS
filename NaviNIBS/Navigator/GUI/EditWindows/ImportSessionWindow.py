from __future__ import annotations

import asyncio
import attrs
import logging
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp

from NaviNIBS.Devices.ToolPositionsClient import ToolPositionsClient
from NaviNIBS.Navigator.Model.Session import Session, Tool
from NaviNIBS.util.Signaler import Signal

logger = logging.getLogger(__name__)


@attrs.define
class SessionTreeItem:
    _label: str
    _obj: object = None
    _parent: SessionTreeItem | None = None
    _children: list[SessionTreeItem] = attrs.field(factory=list, init=False)
    _checked: QtCore.Qt.CheckState | None = attrs.field(default=None, init=False)

    sigCheckedChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        if self._parent is not None:
            self._parent.addChild(self)

    def addChild(self, child: 'SessionTreeItem'):
        if child in self._children:
            return
        assert child._parent is None or child._parent is self, "Child already has a parent"
        child._parent = self
        self._children.append(child)
        child.sigCheckedChanged.connect(self._onChildCheckedChanged)

    def child(self, row):
        return self._children[row]

    def child_count(self):
        return len(self._children)

    def row(self):
        if self._parent:
            return self._parent._children.index(self)
        return 0
    
    def _onChildCheckedChanged(self):
        if all(child.checked == QtCore.Qt.Checked for child in self._children):
            self.checked = QtCore.Qt.Checked
        elif all(child.checked == QtCore.Qt.Unchecked for child in self._children):
            self.checked = QtCore.Qt.Unchecked
        else:
            self.checked = QtCore.Qt.PartiallyChecked

    @property
    def label(self):
        return self._label

    @property
    def obj(self):
        return self._obj

    @property
    def children(self):
        return self._children

    @property
    def parent(self):
        return self._parent

    @property
    def checked(self):
        if self._checked is None:
            if len(self._children) > 0:
                # If there are children, check their state
                if all(child.checked == QtCore.Qt.Checked for child in self._children):
                    self.checked = QtCore.Qt.Checked
                elif all(child.checked == QtCore.Qt.Unchecked for child in self._children):
                    self.checked = QtCore.Qt.Unchecked
                else:
                    self.checked = QtCore.Qt.PartiallyChecked
            else:
                # If there are no children, default to unchecked
                self.checked = QtCore.Qt.Unchecked
        
        return self._checked

    @checked.setter
    def checked(self, value: QtCore.Qt.CheckState):
        if value == self._checked:
            return
        logger.debug(f'Setting checked state of {self._label} to {value}')
        self._checked = value
        if self._checked != QtCore.Qt.PartiallyChecked:
            # Update the checked state of all children
            for child in self._children:
                with child.sigCheckedChanged.disconnected(self._onChildCheckedChanged):
                    child.checked = value
        self.sigCheckedChanged.emit()


class SessionTreeModel(QtCore.QAbstractItemModel):
    """Model for displaying session elements in a tree view."""

    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self._session = session
        self._rootItem = SessionTreeItem('Session', session)
        self._selectionModel = None  # Will be set by the view

        item = SessionTreeItem(label='MRI', obj=self._session.MRI, parent=self._rootItem)

        item = SessionTreeItem(label='Head Model', obj=self._session.headModel, parent=self._rootItem)

        item = SessionTreeItem(label='Coordinate Systems', obj=None, parent=self._rootItem)
        for key, cs in self._session.coordinateSystems.items():
            child = SessionTreeItem(label=key, obj=cs, parent=item)
        
        item = SessionTreeItem(label='Targets', obj=None, parent=self._rootItem)
        for target in self._session.targets.values():
            child = SessionTreeItem(label=target.key, obj=target, parent=item)
        
        item = SessionTreeItem(label='Tools', obj=None, parent=self._rootItem)
        for tool in self._session.tools.values():
            child = SessionTreeItem(label=tool.key, obj=tool, parent=item)
        
        item = SessionTreeItem(label='Samples', obj=None, parent=self._rootItem)
        for key, sample in self._session.samples.items():
            child = SessionTreeItem(label=key, obj=sample, parent=item)
        
        item = SessionTreeItem(label='Digitized Locations', obj=None, parent=self._rootItem)
        for key, loc in self._session.digitizedLocations.items():
            child = SessionTreeItem(label=key, obj=loc, parent=item)

        def connectCheckedSignal(item: SessionTreeItem):
            item.sigCheckedChanged.connect(lambda item=item: self._onItemCheckedChanged(item))
            for child in item.children:
                connectCheckedSignal(child)

        connectCheckedSignal(self._rootItem)

    def setSelectionModel(self, selectionModel):
        self._selectionModel = selectionModel

    def index(self, row, column, parent=QtCore.QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QtCore.QModelIndex()

        if not parent.isValid():
            # No parent, so we are at the top level
            if row < 1:
                return self.createIndex(row, column, self._rootItem)
        else:
            # Child items
            parentItem: SessionTreeItem = parent.internalPointer()
            if row < len(parentItem.children):
                return self.createIndex(row, column, parentItem.children[row])

        return QtCore.QModelIndex()

    def parent(self, index):
        if not index.isValid():
            return QtCore.QModelIndex()

        item = index.internalPointer()
        if item.parent is None:
            return QtCore.QModelIndex()
        return self._indexForItem(item.parent)
        
    def _indexForItem(self, item: SessionTreeItem):
        """Get the index for a given item"""
        if item is self._rootItem:
            return self.createIndex(0, 0, item)
        assert item.parent is not None
        row = item.parent.children.index(item)
        return self.createIndex(row, 0, item)

    def rowCount(self, parent=QtCore.QModelIndex()):
        if not parent.isValid():
            return 1
        item = parent.internalPointer()
        return len(item.children)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 1

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        item = index.internalPointer()
        if role == QtCore.Qt.DisplayRole:
            return item.label
        elif role == QtCore.Qt.CheckStateRole:
            return item.checked
        return None

    def flags(self, index):
        if not index.isValid():
            return QtCore.Qt.NoItemFlags
        return QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsUserCheckable

    def setData(self, index, value, role=QtCore.Qt.EditRole):
        if role == QtCore.Qt.CheckStateRole:
            item = index.internalPointer()
            value = QtCore.Qt.CheckState(value)  # convert from generic value to CheckState
            # If selection model is set and item is selected, apply to all selected items
            if self._selectionModel is not None and self._selectionModel.isSelected(index):
                for selected_index in self._selectionModel.selectedIndexes():
                    selected_item = selected_index.internalPointer()
                    selected_item.checked = value
                    self.dataChanged.emit(selected_index, selected_index, [QtCore.Qt.CheckStateRole])
                return True
            else:
                item.checked = value
                return True
        return False

    def _onItemCheckedChanged(self, item: SessionTreeItem):
        """Handle the checked state change of an item"""
        index = self._indexForItem(item)
        assert index.isValid(), "Item index is not valid"
        self.dataChanged.emit(
            index, index, [QtCore.Qt.CheckStateRole])

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        # if orientation == QtCore.Qt.Horizontal and role == QtCore.Qt.DisplayRole:
        #     return "Session Elements"
        return None


@attrs.define
class ImportSessionWindow:
    _parent: QtWidgets.QWidget
    _session: Session = attrs.field(repr=False)
    _otherSession: Session = attrs.field(repr=False)

    _wdgt: QtWidgets.QDialog = attrs.field(init=False)
    _presetsComboBox: QtWidgets.QComboBox = attrs.field(init=False)
    _sessionTreeView: QtWidgets.QTreeView = attrs.field(init=False)
    _sessionTreeModel: SessionTreeModel = attrs.field(init=False)

    sigFinished: Signal = attrs.field(init=False, factory=lambda: Signal((bool,)))

    def __attrs_post_init__(self):
        self._wdgt = QtWidgets.QDialog(self._parent)
        self._wdgt.setModal(True)

        self._wdgt.setWindowTitle('Import session')

        self._wdgt.setWindowModality(QtGui.Qt.WindowModal)

        self._wdgt.finished.connect(self._onDlgFinished)

        topLayout = QtWidgets.QVBoxLayout()
        self._wdgt.setLayout(topLayout)

        formContainer = QtWidgets.QWidget()
        topLayout.addWidget(formContainer)
        formLayout = QtWidgets.QFormLayout()
        formContainer.setLayout(formLayout)
        self._presetsComboBox = QtWidgets.QComboBox(self._wdgt)
        self._presetsComboBox.addItems(self._getPresetKeys())
        self._presetsComboBox.currentIndexChanged.connect(self._onPresetGUIChanged)
        formLayout.addRow('Preset', self._presetsComboBox)

        # Add session tree view
        treeContainer = QtWidgets.QWidget()
        topLayout.addWidget(treeContainer)
        treeLayout = QtWidgets.QVBoxLayout()
        treeContainer.setLayout(treeLayout)

        treeLabel = QtWidgets.QLabel("Select elements to import:")
        treeLayout.addWidget(treeLabel)

        self._sessionTreeView = QtWidgets.QTreeView(self._wdgt)
        # Use ExtendedSelection mode to support shift-selection
        self._sessionTreeView.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._sessionTreeView.setHeaderHidden(True)
        self._sessionTreeModel = SessionTreeModel(self._otherSession, self._wdgt)
        self._sessionTreeView.setModel(self._sessionTreeModel)
        self._sessionTreeView.setMinimumHeight(300)
        self._sessionTreeView.setMinimumWidth(300)
        self._sessionTreeView.expandToDepth(0)
        self._sessionTreeModel.setSelectionModel(self._sessionTreeView.selectionModel())

        treeLayout.addWidget(self._sessionTreeView)

        # Add button box
        buttonBox = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttonBox.accepted.connect(self._onAccept)
        buttonBox.rejected.connect(self._wdgt.reject)
        topLayout.addWidget(buttonBox)

        # TODO: autoselect default preset based on whether session subject IDs match

        self._wdgt.show()

    def _getPresetKeys(self) -> list[str]:
        return ['Custom', 'Different subject', 'Same subject, different session', 'Select all', 'Select none']

    def _onPresetGUIChanged(self, index: int):
        preset = self._getPresetKeys()[index]

        # Deselect and uncheck all items
        self._sessionTreeView.clearSelection()
        self._uncheckAllItems()

        # Select and check items based on preset
        if preset == 'Different subject':
            # Only select items that don't depend on subject
            self._checkCategoryByIndex(0)  # MRI
            self._checkCategoryByIndex(2)  # Coordinate Systems
        elif preset == 'Same subject, different session':
            # Select subject-specific items
            self._checkCategoryByIndex(0)  # MRI
            self._checkCategoryByIndex(1)  # Head Model
            self._checkCategoryByIndex(2)  # Coordinate Systems
            self._checkCategoryByIndex(6)  # Digitized Locations
        elif preset == 'Select all':
            # Select everything
            for i in range(self._sessionTreeModel.rowCount()):
                self._checkCategoryByIndex(i)
        elif preset == 'Select none':
            # Leave everything unselected
            pass

    def _uncheckAllItems(self):
        """Uncheck all items in the tree"""
        for i in range(self._sessionTreeModel.rowCount()):
            model_index = self._sessionTreeModel.index(i, 0)
            self._sessionTreeModel.setData(model_index, QtCore.Qt.Unchecked, QtCore.Qt.CheckStateRole)

    def _checkCategoryByIndex(self, index):
        """Check and select a top-level category by index"""
        model_index = self._sessionTreeModel.index(index, 0)
        self._sessionTreeView.selectionModel().select(
            model_index, QtCore.QItemSelectionModel.Select)
        self._sessionTreeModel.setData(model_index, QtCore.Qt.Checked, QtCore.Qt.CheckStateRole)

    def _selectCategoryByIndex(self, index):
        """Select a top-level category by index"""
        model_index = self._sessionTreeModel.index(index, 0)
        self._sessionTreeView.selectionModel().select(
            model_index, QtCore.QItemSelectionModel.Select)

    def _onDlgFinished(self, result: int):
        self.sigFinished.emit(result == QtWidgets.QDialog.Accepted)

    def _onAccept(self):
        """Handle import when OK is clicked"""
        # Get checked items
        elements_to_import = []

        # Iterate through all root items
        for i in range(self._sessionTreeModel.rowCount()):
            model_index = self._sessionTreeModel.index(i, 0)
            if self._sessionTreeModel.data(model_index, QtCore.Qt.CheckStateRole) == QtCore.Qt.Checked:
                item = model_index.internalPointer()
                category = item.label

                # Map category name to section name in Session
                if category == 'MRI':
                    elements_to_import.append('MRI')
                elif category == 'Head Model':
                    elements_to_import.append('headModel')
                elif category == 'Coordinate Systems':
                    elements_to_import.append('coordinateSystems')
                elif category == 'Targets':
                    elements_to_import.append('targets')
                elif category == 'Tools':
                    elements_to_import.append('tools')
                elif category == 'Samples':
                    elements_to_import.append('samples')
                elif category == 'Digitized Locations':
                    elements_to_import.append('digitizedLocations')

        # Log what would be imported - actual implementation needed in Session class
        logger.info(f"Would import elements: {', '.join(elements_to_import)}")

        raise NotImplementedError

        self._wdgt.accept()

    def show(self):
        self._wdgt.show()

    @property
    def wdgt(self):
        return self._wdgt

    @property
    def session(self):
        return self._session

    @property
    def otherSession(self):
        return self._otherSession


