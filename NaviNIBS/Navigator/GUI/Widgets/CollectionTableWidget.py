import asyncio
import attrs
import logging
import numpy as np
import qtawesome as qta
from qtpy import QtWidgets, QtCore, QtGui
import typing as tp

from NaviNIBS.Navigator.GUI.CollectionModels.TargetGridsTableModel import TargetGridsTableModel
from NaviNIBS.Navigator.Model.TargetGrids import TargetGrid
from NaviNIBS.util.Asyncio import asyncCreateTask
from NaviNIBS.Navigator.GUI.CollectionModels import CollectionTableModel, FilteredCollectionModel, K, C, CI
from NaviNIBS.Navigator.GUI.CollectionModels.DigitizedLocationsTableModel import DigitizedLocationsTableModel
from NaviNIBS.Navigator.GUI.CollectionModels.FiducialsTableModels import PlanningFiducialsTableModel, RegistrationFiducialsTableModel
from NaviNIBS.Navigator.GUI.CollectionModels.HeadPointsTableModel import HeadPointsTableModel
from NaviNIBS.Navigator.GUI.CollectionModels.ROIsTableModel import ROIsTableModel
from NaviNIBS.Navigator.GUI.CollectionModels.TargetsTableModel import TargetsTableModel, FullTargetsTableModel
from NaviNIBS.Navigator.GUI.CollectionModels.TargetGridsTableModel import TargetGridsTableModel
from NaviNIBS.Navigator.GUI.CollectionModels.SamplesTableModel import SamplesTableModel
from NaviNIBS.Navigator.GUI.CollectionModels.ToolsTableModel import ToolsTableModel
from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.Navigator.Model.ROIs import ROI, ROIs
from NaviNIBS.Navigator.Model.Samples import Sample, Samples
from NaviNIBS.Navigator.Model.Targets import Target, Targets
from NaviNIBS.Navigator.Model.TargetGrids import TargetGrid, TargetGrids
from NaviNIBS.Navigator.Model.Tools import Tool, Tools
from NaviNIBS.Navigator.Model.SubjectRegistration import HeadPoint, HeadPoints, Fiducial, Fiducials
from NaviNIBS.Navigator.Model.DigitizedLocations import DigitizedLocation, DigitizedLocations
from NaviNIBS.util.Signaler import Signal

logger = logging.getLogger(__name__)


TM = tp.TypeVar('TM', bound=CollectionTableModel | FilteredCollectionModel)


class _HeaderGeometryWatcher(QtCore.QObject):
    """
    Small event filter that invokes a callback whenever a watched widget (e.g. a QHeaderView) is moved or resized.
    Used to keep the corner filter button aligned with the table's header corner.
    """
    def __init__(self, callback: tp.Callable[[], None], parent: QtCore.QObject | None = None):
        super().__init__(parent)
        self._callback = callback

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() in (QtCore.QEvent.Type.Resize, QtCore.QEvent.Type.Move,
                            QtCore.QEvent.Type.Show, QtCore.QEvent.Type.Hide):
            self._callback()
        return False


@attrs.define
class CollectionTableWidget(tp.Generic[K, CI, C, TM]):
    _Model: tp.Callable[[Session], TM]
    _modelKwargs: tp.Dict[str, tp.Any] = attrs.field(factory=dict)
    _session: tp.Optional[Session] = attrs.field(default=None, repr=False)

    _container: QtWidgets.QWidget = attrs.field(init=False, factory=QtWidgets.QWidget)
    _tableView: QtWidgets.QTableView = attrs.field(init=False, factory=QtWidgets.QTableView)
    _sourceModel: CollectionTableModel | None = attrs.field(init=False, default=None)
    _model: FilteredCollectionModel | None = attrs.field(init=False, default=None)
    """
    Proxy model set on the table view, providing text filtering and sorting over `_sourceModel`.
    If `_Model` itself produces a FilteredCollectionModel (e.g. TargetsTableModel), it is used directly.
    """

    _doAdjustSizeToContents: bool | None = None
    """
    if None, will change automatically based on number of rows, to avoid performance issues with large tables
    """
    _doAdjustColumnWidthsToContents: bool = True

    _doAllowSorting: bool = True
    """
    Whether clicking on column headers sorts by that column (click again for descending, a third time to clear).
    """
    _doShowFilterButton: bool = True
    """
    Whether to show a small search icon in the table's top-left corner for opening the filter bar.
    (Filter bar can still be opened via Ctrl+F or header context menu.)
    """

    _filterBar: QtWidgets.QWidget = attrs.field(init=False, factory=QtWidgets.QWidget)
    _filterLineEdit: QtWidgets.QLineEdit = attrs.field(init=False, factory=QtWidgets.QLineEdit)
    _filterColumnComboBox: QtWidgets.QComboBox = attrs.field(init=False, factory=QtWidgets.QComboBox)
    _filterBarColumnKeys: list[str] = attrs.field(init=False, factory=list)
    _filterDebounceTimer: QtCore.QTimer = attrs.field(init=False, factory=QtCore.QTimer)
    _filterDebounceInterval_ms: int = 150
    """
    How long to wait after the last keystroke in the filter box before re-filtering, so that typing a
    multi-character query in a large table doesn't trigger a re-filter per character.
    """
    _cornerFilterBtn: QtWidgets.QToolButton | None = attrs.field(init=False, default=None)
    _headerGeometryWatcher: _HeaderGeometryWatcher | None = attrs.field(init=False, default=None)

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
        else:
            # default to adjusting on first show only
            pass

        if self._doAdjustSizeToContents is None or self._doAdjustColumnWidthsToContents:
            asyncCreateTask(self._resizeToContentsLoop)

        # set fixed row height to improve performance
        self._tableView.verticalHeader().setDefaultSectionSize(25)
        self._tableView.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Fixed)

        """
        Allow drag reordering of columns. However, these won't affect underlying
        data models and won't be persistent.
        """
        self._tableView.horizontalHeader().setSectionsMovable(True)
        # TODO: add extra infrastructure to make these reorderings persistent
        # TODO: add right click context menu to hide / show columns

        from NaviNIBS.util.DiagnosticTelemetry import registerObject
        registerObject('collectionTableWidgets', self)

        self._initFilterBar()

        if self._session is not None:
            self._onSessionSet()

    def _initFilterBar(self):
        """
        Set up container layout with (initially hidden) filter bar above table view, plus the various ways of
        opening it: corner search button, Ctrl+F, and header context menu.
        """
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._container.setLayout(layout)

        barLayout = QtWidgets.QHBoxLayout()
        barLayout.setContentsMargins(0, 0, 0, 0)
        barLayout.setSpacing(2)
        self._filterBar.setLayout(barLayout)

        self._filterLineEdit.setPlaceholderText('Filter…')
        self._filterLineEdit.setClearButtonEnabled(True)
        self._filterLineEdit.setToolTip('Show only rows containing this text (case-insensitive). Press Escape to clear and close.')
        self._filterLineEdit.textChanged.connect(self._onFilterTextChanged)
        barLayout.addWidget(self._filterLineEdit, stretch=1)

        self._filterDebounceTimer.setSingleShot(True)
        self._filterDebounceTimer.timeout.connect(self._applyFilterTextNow)

        self._filterColumnComboBox.setToolTip('Which column(s) to search within')
        self._filterColumnComboBox.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._filterColumnComboBox.currentIndexChanged.connect(self._onFilterColumnChanged)
        barLayout.addWidget(self._filterColumnComboBox)

        closeBtn = QtWidgets.QToolButton()
        closeBtn.setIcon(qta.icon('mdi6.close'))
        closeBtn.setAutoRaise(True)
        closeBtn.setToolTip('Clear filter and close filter bar (Escape)')
        closeBtn.clicked.connect(lambda *args: self.hideFilterBar())
        barLayout.addWidget(closeBtn)

        self._filterBar.setVisible(False)
        layout.addWidget(self._filterBar)
        layout.addWidget(self._tableView)

        # Escape within filter box clears and closes it
        escShortcut = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_Escape), self._filterLineEdit)
        escShortcut.setContext(QtCore.Qt.ShortcutContext.WidgetShortcut)
        escShortcut.activated.connect(self.hideFilterBar)

        # Ctrl+F anywhere within table opens filter bar
        findShortcut = QtGui.QShortcut(QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Find), self._container)
        findShortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        findShortcut.activated.connect(lambda: self.showFilterBar())

        # right-click on column header for filter / sort options
        header = self._tableView.horizontalHeader()
        header.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._onHeaderContextMenuRequested)

        if self._doShowFilterButton:
            # small search button occupying the (otherwise unused) corner between row and column headers
            self._tableView.setCornerButtonEnabled(False)
            btn = QtWidgets.QToolButton(self._tableView)
            btn.setIcon(qta.icon('mdi6.magnify'))
            btn.setAutoRaise(True)
            btn.setToolTip('Filter table rows (Ctrl+F)')
            btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(lambda *args: self.showFilterBar())
            self._cornerFilterBtn = btn
            self._headerGeometryWatcher = _HeaderGeometryWatcher(self._updateCornerFilterBtnGeometry,
                                                                  parent=self._tableView)
            self._tableView.horizontalHeader().installEventFilter(self._headerGeometryWatcher)
            self._tableView.verticalHeader().installEventFilter(self._headerGeometryWatcher)
            self._updateCornerFilterBtnGeometry()

    def _updateCornerFilterBtnGeometry(self):
        if self._cornerFilterBtn is None:
            return
        hHeader = self._tableView.horizontalHeader()
        vHeader = self._tableView.verticalHeader()
        if hHeader.isHidden() or vHeader.isHidden() or vHeader.width() <= 0 or hHeader.height() <= 0:
            self._cornerFilterBtn.hide()
            return
        rect = QtCore.QRect(vHeader.geometry().x(), hHeader.geometry().y(), vHeader.width(), hHeader.height())
        self._cornerFilterBtn.setGeometry(rect)
        iconSize = max(8, min(rect.width(), rect.height()) - 6)
        self._cornerFilterBtn.setIconSize(QtCore.QSize(iconSize, iconSize))
        self._cornerFilterBtn.show()
        self._cornerFilterBtn.raise_()

    @property
    def wdgt(self) -> QtWidgets.QWidget:
        """
        Container widget including table view and (usually hidden) filter bar. Add this to layouts.
        """
        return self._container

    @property
    def tableView(self) -> QtWidgets.QTableView:
        return self._tableView

    @property
    def filterText(self) -> str:
        return self._filterLineEdit.text()

    @filterText.setter
    def filterText(self, text: str):
        self._filterLineEdit.setText(text)
        self._applyFilterTextNow()  # apply immediately when set programmatically, without debounce delay

    @property
    def filterColumnKey(self) -> str | None:
        if self._model is None:
            return None
        return self._model.filterColumnKey

    @filterColumnKey.setter
    def filterColumnKey(self, colKey: str | None):
        if colKey is None:
            self._filterColumnComboBox.setCurrentIndex(0)
        else:
            iCombo = self._filterBarColumnKeys.index(colKey) + 1
            self._filterColumnComboBox.setCurrentIndex(iCombo)

    @property
    def isFilterBarVisible(self) -> bool:
        return not self._filterBar.isHidden()

    def showFilterBar(self, columnKey: str | None = None):
        """
        Show filter bar and focus the text field. If columnKey is specified, restrict the filter to that column;
        otherwise keep current column scope.
        """
        if columnKey is not None:
            self.filterColumnKey = columnKey
        self._filterBar.setVisible(True)
        self._filterLineEdit.setFocus()
        self._filterLineEdit.selectAll()

    def hideFilterBar(self):
        """
        Clear filter (revealing all rows) and hide filter bar.
        """
        self.clearFilter()
        self._filterBar.setVisible(False)
        self._tableView.setFocus()

    def clearFilter(self):
        self._filterLineEdit.clear()
        self._applyFilterTextNow()  # also covers case where line edit was already empty

    def _onFilterTextChanged(self, text: str):
        """
        Note: doesn't apply the filter immediately, but instead restarts a short debounce timer, so that
        typing a multi-character query in a large table doesn't re-filter on every keystroke.
        """
        self._filterDebounceTimer.start(self._filterDebounceInterval_ms)

    def _applyFilterTextNow(self):
        """
        Apply the filter text currently in the line edit immediately, cancelling any pending debounce.
        """
        self._filterDebounceTimer.stop()
        if self._model is None:
            return
        self._model.filterText = self._filterLineEdit.text()

    def _onFilterColumnChanged(self, iCombo: int):
        if self._model is None or iCombo < 0:
            return
        self._model.filterColumnKey = self._filterColumnComboBox.itemData(iCombo)

    def _refreshFilterColumnComboBox(self):
        """
        Populate column scope combobox from model's columns; called on init and whenever model columns may have changed.
        """
        if self._model is None:
            return
        colKeys = list(self._model.columns)
        if colKeys == self._filterBarColumnKeys:
            return
        colLabels = self._model.columnLabels
        prevColKey = self._model.filterColumnKey

        with QtCore.QSignalBlocker(self._filterColumnComboBox):
            self._filterColumnComboBox.clear()
            self._filterColumnComboBox.addItem('All columns', None)
            for colKey in colKeys:
                self._filterColumnComboBox.addItem(colLabels.get(colKey, colKey), colKey)
            self._filterBarColumnKeys = colKeys

            if prevColKey is not None and prevColKey in colKeys:
                self._filterColumnComboBox.setCurrentIndex(colKeys.index(prevColKey) + 1)
            else:
                self._filterColumnComboBox.setCurrentIndex(0)
                self._model.filterColumnKey = None

    def _onHeaderContextMenuRequested(self, pos: QtCore.QPoint):
        if self._model is None:
            return
        header = self._tableView.horizontalHeader()
        logicalIndex = header.logicalIndexAt(pos)

        menu = QtWidgets.QMenu(header)
        action = menu.addAction(qta.icon('mdi6.magnify'), 'Filter…')
        action.triggered.connect(lambda *args: self.showFilterBar(columnKey=None))
        colKeys = self._model.columns
        if 0 <= logicalIndex < len(colKeys):
            colKey = colKeys[logicalIndex]
            colLabel = self._model.columnLabels.get(colKey, colKey)
            action = menu.addAction(f"Filter by '{colLabel}'…")
            action.triggered.connect(lambda *args, colKey=colKey: self.showFilterBar(columnKey=colKey))

        if self._doAllowSorting and header.sortIndicatorSection() >= 0:
            menu.addSeparator()
            action = menu.addAction('Clear sort')
            action.triggered.connect(lambda *args: header.setSortIndicator(-1, QtCore.Qt.SortOrder.AscendingOrder))

        menu.exec(header.mapToGlobal(pos))

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

    @property
    def model(self) -> FilteredCollectionModel | None:
        """
        The (proxy) model set on the table view. Row indices are in view coordinates (i.e. after filtering and sorting).
        """
        return self._model

    @property
    def sourceModel(self) -> CollectionTableModel | None:
        """
        The underlying collection model. Row indices are in collection order.
        """
        return self._sourceModel

    def _onSessionSet(self):
        model = self._Model(session=self._session, **self._modelKwargs)
        if isinstance(model, FilteredCollectionModel):
            # model already provides filtering/sorting (e.g. TargetsTableModel)
            self._model = model
            self._sourceModel = model.proxiedModel
        else:
            self._sourceModel = model
            self._model = FilteredCollectionModel(session=self._session, proxiedModel=model)

        self._model.sigSelectionChanged.connect(self._onModelSelectionChanged)
        self._model.sigItemEdited.connect(lambda *args: asyncCreateTask(self._resizeToContentsSoon))
        self._refreshSizeAdjustPolicy()
        self._tableView.setModel(self._model)
        self._tableView.selectionModel().currentChanged.connect(self._onTableCurrentChanged)
        self._tableView.selectionModel().selectionChanged.connect(self._onTableSelectionChanged)
        self._model.rowsInserted.connect(self._onTableRowsInserted)

        if self._doAllowSorting:
            header = self._tableView.horizontalHeader()
            # start unsorted (in collection order); without this, enabling sorting would immediately sort by first column
            header.setSortIndicator(-1, QtCore.Qt.SortOrder.AscendingOrder)
            # allow a third click on a header to clear sort and return to collection order
            header.setSortIndicatorClearable(True)
            self._tableView.setSortingEnabled(True)

        self._refreshFilterColumnComboBox()
        self._model.layoutChanged.connect(lambda *args: self._refreshFilterColumnComboBox())
        self._model.modelReset.connect(lambda *args: self._refreshFilterColumnComboBox())
        self._model.columnsInserted.connect(lambda *args: self._refreshFilterColumnComboBox())
        self._model.columnsRemoved.connect(lambda *args: self._refreshFilterColumnComboBox())

        self._tableView.resizeColumnsToContents()  # adjust regardless of doAdjustColumnWidthsToContents when initializing
        self._updateCornerFilterBtnGeometry()

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
        if index is None and self._model.isKeyHiddenByTextFilter(key):
            # item exists but is hidden by (temporary) text filter; clear filter to reveal it
            logger.info(f'Clearing table filter to reveal {key}')
            self.clearFilter()
            index = self._model.getIndexFromCollectionItemKey(key)
        if index is None:
            logger.warning(f'Cannot set current item to {key}, it is not in the model')
            raise KeyError(f'Key {key} not found in model')

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

    @selectedCollectionItemKeys.setter
    def selectedCollectionItemKeys(self, keys: list[K]):
        selection = QtCore.QItemSelection()
        for key in keys:
            row = self._model.getIndexFromCollectionItemKey(key)
            if row is None:
                if self._model.isKeyHiddenByTextFilter(key):
                    logger.debug(f'Cannot select item with key {key} in view, it is hidden by text filter')
                else:
                    logger.warning(f'Cannot select item with key {key}, it is not in the model')
                continue
            leftIndex = self._model.index(row, 0)
            rightIndex = self._model.index(row, self._model.columnCount() - 1)
            selection.merge(QtCore.QItemSelection(leftIndex, rightIndex), QtCore.QItemSelectionModel.Select)
        self._tableView.selectionModel().select(selection, QtCore.QItemSelectionModel.ClearAndSelect)

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
        index = self._model.index(last, 0)
        if index.isValid():
            self._tableView.scrollTo(index)
        self._needsResizeToContents.set()
        # re-evaluate size-adjust policy immediately: during continuous sampling the
        # resize-to-contents loop never reaches its refresh (it requires a 20 s quiet period),
        # so without this a session that grew from small would stay on the expensive
        # AdjustToContents policy no matter how many rows accumulate
        self._refreshSizeAdjustPolicy()

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

    def _refreshSizeAdjustPolicy(self):
        if self._model is None:
            return
        if self._doAdjustSizeToContents is None:
            if self._model.rowCount() > 200:
                policy = self._tableView.SizeAdjustPolicy.AdjustIgnored
            elif self._model.rowCount() > 50:
                policy = self._tableView.SizeAdjustPolicy.AdjustToContentsOnFirstShow
            else:
                policy = self._tableView.SizeAdjustPolicy.AdjustToContents
            if self._tableView.sizeAdjustPolicy() != policy:
                logger.debug(f'Changing size adjust policy to {policy} at {self._model.rowCount()} rows')
                self._tableView.setSizeAdjustPolicy(policy)

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

            if self._doAdjustColumnWidthsToContents:
                logger.debug('Resizing columns to contents')
                self._tableView.resizeColumnsToContents()
            self._refreshSizeAdjustPolicy()
            self._resizeToContentsPending = False

    async def _resizeToContentsSoon(self):
        """
        Schedule a resize to contents to happen soon (after a short delay).
        Multiple calls to this will only result in one resize.
        Note: this is an expensive operation
        """
        self._resizeToContentsPending = True
        await asyncio.sleep(0.5)
        if not self._resizeToContentsPending:
            # someone else already resized
            return
        logger.debug('Resizing columns to contents')
        self.resizeColumnsToContents()

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
    _defaultTargetColor: str = '#2222FF'
    _doShowColorColumn: bool = True

    def __attrs_post_init__(self):
        self._modelKwargs['defaultTargetColor'] = self._defaultTargetColor
        self._modelKwargs['doShowColorColumn'] = self._doShowColorColumn
        super().__attrs_post_init__()


@attrs.define
class TargetsTableWidget(CollectionTableWidget[str, Target, Targets, TargetsTableModel]):
    _Model: tp.Callable[[Session], TargetsTableModel] = TargetsTableModel
    _defaultTargetColor: str = '#2222FF'
    _doShowColorColumn: bool = False

    def __attrs_post_init__(self):
        self._modelKwargs['defaultTargetColor'] = self._defaultTargetColor
        self._modelKwargs['doShowColorColumn'] = self._doShowColorColumn
        super().__attrs_post_init__()


@attrs.define
class TargetGridsTableWidget(CollectionTableWidget[str, TargetGrid, TargetGrids, TargetGridsTableModel]):
    _Model: tp.Callable[[Session], TargetGridsTableModel] = TargetGridsTableModel

    def __attrs_post_init__(self):
        super().__attrs_post_init__()


@attrs.define
class ROIsTableWidget(CollectionTableWidget[str, ROI, ROIs, ROIsTableModel]):
    _Model: tp.Callable[[Session], ROIsTableModel] = ROIsTableModel

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
