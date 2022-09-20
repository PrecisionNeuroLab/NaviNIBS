import attrs
import logging
import numpy as np
import qtawesome as qta
from qtpy import QtWidgets, QtCore, QtGui
import typing as tp

from RTNaBS.Navigator.GUI.CollectionModels import CollectionTableModel, K, C, CI
from RTNaBS.Navigator.GUI.CollectionModels.TargetsTableModel import TargetsTableModel
from RTNaBS.Navigator.GUI.CollectionModels.SamplesTableModel import SamplesTableModel
from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.Navigator.Model.Samples import Sample, Samples
from RTNaBS.Navigator.Model.Targets import Target, Targets
from RTNaBS.Navigator.Model.SubjectRegistration import HeadPoint, HeadPoints
from RTNaBS.util.Signaler import Signal

logger = logging.getLogger(__name__)


TM = tp.TypeVar('TM', bound=CollectionTableModel)


@attrs.define
class CollectionTableWidget(tp.Generic[K, C, CI, TM]):
    _Model: tp.Callable[[Session], TM]
    _session: tp.Optional[Session] = None

    _tableView: QtWidgets.QTableView = attrs.field(init=False, factory=QtWidgets.QTableView)
    _model: tp.Optional[TM] = attrs.field(init=False, default=None)

    sigCurrentItemChanged: Signal = attrs.field(init=False, factory=lambda: Signal((K,)))
    """
    Includes key (or index) of newly selected item.

    Note: this is emitted when the selection changes (e.g. a different sample is selected), NOT when a property of the currently selected sample changes.

    Note: this is Qt's "current" item. If multiple samples are selected, the "current" sample will just be the last sample added to the selection
    """

    sigSelectionChanged: Signal = attrs.field(init=False, factory=lambda: Signal((list[K],)))
    """
    Includes keys (or indices) of all selected items.

    Note: this is emitted when the selection changes (e.g. a different sample is selected), NOT when a property of the currently selected sample changes.
    """

    def __attrs_post_init__(self):
        self._tableView.setSelectionBehavior(self._tableView.SelectRows)
        self._tableView.setSelectionMode(self._tableView.ExtendedSelection)

    @property
    def wdgt(self):
        return self._tableView

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newSes: tp.Optional[Session]):
        if self._session is newSes:
            return
        if self._session is not None:
            raise NotImplementedError  # TODO: notify table view of model change, disconnect from previous signals
        assert self._model is None
        self._session = newSes
        self._model = self._Model(self._session)
        self._model.sigSelectionChanged.connect(self._onModelSelectionChanged)
        self._tableView.setModel(self._model)
        self._tableView.selectionModel().currentChanged.connect(self._onTableCurrentChanged)
        self._tableView.selectionModel().selectionChanged.connect(self._onTableSelectionChanged)
        self._model.rowsInserted.connect(self._onTableRowsInserted)
        self._tableView.resizeColumnsToContents()

    @property
    def currentCollectionItemKey(self) -> K:
        curRow = self._tableView.currentIndex().row()
        return self._model.getCollectionItemKeyFromIndex(curRow)

    @currentCollectionItemKey.setter
    def currentCollectionItemKey(self, key: K):
        if key == self.currentCollectionItemKey:
            return
        index = self._model.getIndexFromCollectionItemKey(key)
        self._tableView.setCurrentIndex(self._model.index(index, 0))

    @property
    def currentCollectionItem(self) -> CI:
        curRow = self._tableView.currentIndex().row()
        return self._model.getCollectionItemFromIndex(curRow)

    @property
    def selectedCollectionItemKeys(self):
        selItems = self._tableView.selectedIndexes()
        selRows = [item.row() for item in selItems]
        selRows = list(dict.fromkeys(selRows))  # remove duplicates, keep order stable
        return [self._model.getCollectionItemKeyFromIndex(selRow) for selRow in selRows]

    def _onTableCurrentChanged(self):
        logger.debug('Current item changed')
        self.sigCurrentItemChanged.emit(self.currentCollectionItemKey)

    def _onTableSelectionChanged(self, selected: QtCore.QItemSelection, deselected: QtCore.QItemSelection):
        logger.debug('Current selection changed')
        selectedKeys = self.selectedCollectionItemKeys
        self._model.setWhichItemsSelected(selectedKeys)
        self.sigSelectionChanged.emit(selectedKeys)

    def _onTableRowsInserted(self, parent: QtCore.QModelIndex, first: int, last: int):
        # scroll to end of new rows automatically
        self._tableView.scrollTo(self._model.index(last, 0))

    def _onModelSelectionChanged(self, changedKeys: list[K]):
        logger.debug(f'Updating selection for keys {changedKeys}')
        selection = QtCore.QItemSelection()
        selection.merge(self._tableView.selectionModel().selection(), QtCore.QItemSelectionModel.Select)

        for key in changedKeys:
            row = self._model.getIndexFromCollectionItemKey(key)
            if row is None:
                # item no longer in collection
                continue
            index = self._model.index(row, 0)
            if self._model.getCollectionItemIsSelected(key):
                cmd = QtCore.QItemSelectionModel.Select
            else:
                cmd = QtCore.QItemSelectionModel.Deselect
            selection.merge(QtCore.QItemSelection(index, index), cmd)

        self._tableView.selectionModel().select(selection, QtCore.QItemSelectionModel.SelectCurrent)


@attrs.define
class SamplesTableWidget(CollectionTableWidget[str, Sample, Samples, SamplesTableModel]):
    _Model: tp.Callable[[Session], SamplesTableModel] = SamplesTableModel

    def __attrs_post_init__(self):
        super().__attrs_post_init__()


@attrs.define
class TargetsTableWidget(CollectionTableWidget[str, Target, Targets, TargetsTableModel]):
    _Model: tp.Callable[[Session], TargetsTableModel] = TargetsTableModel

    def __attrs_post_init__(self):
        super().__attrs_post_init__()