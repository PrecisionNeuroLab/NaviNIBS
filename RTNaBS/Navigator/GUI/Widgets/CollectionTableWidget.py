import asyncio
import attrs
import logging
import numpy as np
import qtawesome as qta
from qtpy import QtWidgets, QtCore, QtGui
import typing as tp

from RTNaBS.util.Asyncio import asyncTryAndLogExceptionOnError
from RTNaBS.Navigator.GUI.CollectionModels import CollectionTableModel, K, C, CI
from RTNaBS.Navigator.GUI.CollectionModels.DigitizedLocationsTableModel import DigitizedLocationsTableModel
from RTNaBS.Navigator.GUI.CollectionModels.FiducialsTableModels import PlanningFiducialsTableModel, RegistrationFiducialsTableModel
from RTNaBS.Navigator.GUI.CollectionModels.HeadPointsTableModel import HeadPointsTableModel
from RTNaBS.Navigator.GUI.CollectionModels.TargetsTableModel import TargetsTableModel, FullTargetsTableModel
from RTNaBS.Navigator.GUI.CollectionModels.SamplesTableModel import SamplesTableModel
from RTNaBS.Navigator.GUI.CollectionModels.ToolsTableModel import ToolsTableModel
from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.Navigator.Model.Samples import Sample, Samples
from RTNaBS.Navigator.Model.Targets import Target, Targets
from RTNaBS.Navigator.Model.Tools import Tool, Tools
from RTNaBS.Navigator.Model.SubjectRegistration import HeadPoint, HeadPoints, Fiducial, Fiducials
from RTNaBS.Navigator.Model.DigitizedLocations import DigitizedLocation, DigitizedLocations
from RTNaBS.util.Signaler import Signal

logger = logging.getLogger(__name__)


TM = tp.TypeVar('TM', bound=CollectionTableModel)


@attrs.define
class CollectionTableWidget(tp.Generic[K, CI, C, TM]):
    _Model: tp.Callable[[Session], TM]
    _session: tp.Optional[Session] = attrs.field(default=None, repr=False)

    _tableView: QtWidgets.QTableView = attrs.field(init=False, factory=QtWidgets.QTableView)
    _model: tp.Optional[TM] = attrs.field(init=False, default=None)

    _doAdjustSizeToContents: bool = True

    _needsResizeToContents: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    _resizeToContentsPending: bool = attrs.field(init=False, default=False)

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
        self._tableView.setSelectionBehavior(self._tableView.SelectionBehavior.SelectRows)
        self._tableView.setSelectionMode(self._tableView.SelectionMode.ExtendedSelection)
        if self._doAdjustSizeToContents:
            self._tableView.setSizeAdjustPolicy(self._tableView.SizeAdjustPolicy.AdjustToContents)
            asyncio.create_task(asyncTryAndLogExceptionOnError(self._resizeToContentsLoop))

        if self._session is not None:
            self._onSessionSet()

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
        self._onSessionSet()

    def _onSessionSet(self):
        self._model = self._Model(session=self._session)
        self._model.sigSelectionChanged.connect(self._onModelSelectionChanged)
        self._tableView.setModel(self._model)
        self._tableView.selectionModel().currentChanged.connect(self._onTableCurrentChanged)
        self._tableView.selectionModel().selectionChanged.connect(self._onTableSelectionChanged)
        self._model.rowsInserted.connect(self._onTableRowsInserted)
        self._tableView.resizeColumnsToContents()

    @property
    def currentCollectionItemKey(self) -> tp.Optional[K]:
        curRow = self._tableView.currentIndex().row()
        if curRow == -1:
            return None
        if curRow >= self._model.rowCount():
            # can happen if rows were recently deleted
            return None
        return self._model.getCollectionItemKeyFromIndex(curRow)

    @currentCollectionItemKey.setter
    def currentCollectionItemKey(self, key: K | None):
        if key is None:
            if self.currentCollectionItemKey is not None:
                # current item was deleted, clear index
                self._tableView.setCurrentIndex(QtCore.QModelIndex())
            return
        if key == self.currentCollectionItemKey:
            return
        # logger.debug(f'Setting current item to {key}')
        index = self._model.getIndexFromCollectionItemKey(key)
        self._tableView.setCurrentIndex(self._model.index(index, 0))

    @property
    def rowCount(self):
        return self._model.rowCount()

    @property
    def currentRow(self):
        return self._tableView.currentIndex().row()

    @currentRow.setter
    def currentRow(self, row: int):
        if row == self.currentRow:
            return
        self._tableView.setCurrentIndex(self._model.index(row, 0))

    @property
    def currentCollectionItem(self) -> CI:
        curRow = self._tableView.currentIndex().row()
        return self._model.getCollectionItemFromIndex(curRow)

    @property
    def selectedCollectionItemKeys(self):
        selItems = self._tableView.selectedIndexes()
        selRows = [item.row() for item in selItems]
        selRows = list(dict.fromkeys(selRows))  # remove duplicates, keep order stable
        return [self._model.getCollectionItemKeyFromIndex(selRow) for selRow in selRows if selRow < self._model.rowCount()]

    def _onTableCurrentChanged(self):
        logger.debug('Current item changed')
        self.sigCurrentItemChanged.emit(self.currentCollectionItemKey)

    def _onTableSelectionChanged(self, selected: QtCore.QItemSelection, deselected: QtCore.QItemSelection):
        logger.debug('Current selection changed')
        selectedKeys = self.selectedCollectionItemKeys
        # logger.debug(f'selectedKeys: {selectedKeys}')
        if self.currentCollectionItemKey not in selectedKeys and len(selectedKeys) > 0:
            # change current item to first item in selected keys
            # logger.debug('Updating current item to be in selection')
            self.currentCollectionItemKey = selectedKeys[0]
        self._model.setWhichItemsSelected(selectedKeys)
        self.sigSelectionChanged.emit(selectedKeys)

    def _onTableRowsInserted(self, parent: QtCore.QModelIndex, first: int, last: int):
        # scroll to end of new rows automatically
        self._tableView.scrollTo(self._model.index(last, 0))
        self._needsResizeToContents.set()

    def _onModelSelectionChanged(self, changedKeys: list[K]):
        logger.debug(f'Updating selection for keys {changedKeys}')
        selection = QtCore.QItemSelection()
        selection.merge(self._tableView.selectionModel().selection(), QtCore.QItemSelectionModel.Select)

        for key in changedKeys:
            row = self._model.getIndexFromCollectionItemKey(key)
            if row is None:
                # item no longer in collection
                continue
            leftIndex = self._model.index(row, 0)
            rightIndex = self._model.index(row, self._model.columnCount() - 1)
            if self._model.getCollectionItemIsSelected(key):
                # logger.debug(f'{key} is selected')
                cmd = QtCore.QItemSelectionModel.Select
            else:
                # logger.debug(f'{key} is deselected')
                cmd = QtCore.QItemSelectionModel.Deselect
            selection.merge(QtCore.QItemSelection(leftIndex, rightIndex), cmd)
            # logger.debug(f'selection: {selection} {selection.indexes()}')

        self._tableView.selectionModel().select(selection, QtCore.QItemSelectionModel.ClearAndSelect)

    async def _resizeToContentsLoop(self):
        """
        resizeColumnsToContents is very expensive, so don't run on every update.
        Instead, wait until there are no changes for at least 20 sec to resize.
        """
        while True:
            await self._needsResizeToContents.wait()
            self._resizeToContentsPending = True
            while self._needsResizeToContents.is_set():
                self._needsResizeToContents.clear()
                await asyncio.sleep(20.)

            if not self._resizeToContentsPending:
                # someone manually resized while we were waiting
                continue

            logger.debug('Resizing columns to contents')
            self._tableView.resizeColumnsToContents()
            self._resizeToContentsPending = False

    def resizeColumnsToContents(self):
        """
        Allow caller to manually trigger resize without waititng for auto-resize loop.
        Note: this is an expensive operation
        """
        self._needsResizeToContents.clear()
        self._tableView.resizeColumnsToContents()
        self._resizeToContentsPending = False  # cancel any auto-queued resize

@attrs.define
class DigitizedLocationsTableWidget(CollectionTableWidget[str, DigitizedLocation, DigitizedLocations, DigitizedLocationsTableModel]):
    _Model: tp.Callable[[Session], DigitizedLocationsTableModel] = DigitizedLocationsTableModel

    def __attrs_post_init__(self):
        super().__attrs_post_init__()


@attrs.define
class SamplesTableWidget(CollectionTableWidget[str, Sample, Samples, SamplesTableModel]):
    _Model: tp.Callable[[Session], SamplesTableModel] = SamplesTableModel

    def __attrs_post_init__(self):
        super().__attrs_post_init__()


@attrs.define
class FullTargetsTableWidget(CollectionTableWidget[str, Target, Targets, FullTargetsTableModel]):
    _Model: tp.Callable[[Session], FullTargetsTableModel] = FullTargetsTableModel

    def __attrs_post_init__(self):
        super().__attrs_post_init__()


@attrs.define
class TargetsTableWidget(CollectionTableWidget[str, Target, Targets, TargetsTableModel]):
    _Model: tp.Callable[[Session], TargetsTableModel] = TargetsTableModel

    def __attrs_post_init__(self):
        super().__attrs_post_init__()


@attrs.define
class PlanningFiducialsTableWidget(CollectionTableWidget[str, Fiducial, Fiducials, PlanningFiducialsTableModel]):
    _Model: tp.Callable[[Session], PlanningFiducialsTableModel] = PlanningFiducialsTableModel

    def __attrs_post_init__(self):
        super().__attrs_post_init__()


@attrs.define
class RegistrationFiducialsTableWidget(CollectionTableWidget[str, Fiducial, Fiducials, RegistrationFiducialsTableModel]):
    _Model: tp.Callable[[Session], RegistrationFiducialsTableModel] = RegistrationFiducialsTableModel

    def __attrs_post_init__(self):
        super().__attrs_post_init__()


@attrs.define
class HeadPointsTableWidget(CollectionTableWidget[int, HeadPoint, HeadPoints, HeadPointsTableModel]):
    _Model: tp.Callable[[Session], HeadPointsTableModel] = HeadPointsTableModel

    def __attrs_post_init__(self):
        super().__attrs_post_init__()


@attrs.define
class ToolsTableWidget(CollectionTableWidget[int, Tool, Tools, ToolsTableModel]):
    _Model: tp.Callable[[Session], ToolsTableModel] = ToolsTableModel

    def __attrs_post_init__(self):
        super().__attrs_post_init__()