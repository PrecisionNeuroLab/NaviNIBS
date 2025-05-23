import attrs
from collections.abc import Sequence, Mapping
import contextlib
import logging
import typing as tp
from qtpy import QtCore, QtGui, QtWidgets

from NaviNIBS.Navigator.Model.GenericCollection import GenericCollection, GenericCollectionDictItem
from NaviNIBS.Navigator.Model import Session
from NaviNIBS.util.Signaler import Signal


logger = logging.getLogger(__name__)


K = tp.TypeVar('K', int, str)  # collection item key type
C = tp.TypeVar('C', bound=GenericCollection)  # collection type
CI = tp.TypeVar('CI', bound=GenericCollectionDictItem)  # collection item type


@attrs.define(slots=False, kw_only=True)
class CollectionTableModelBase(tp.Generic[K, C, CI]):
    _session: Session = attrs.field(repr=False)

    sigSelectionChanged: Signal = attrs.field(init=False, factory=lambda: Signal((list[K],)))  # emits which keys had selection changed (including both newly selected AND newly deselected keys)

    def __attrs_post_init__(self):
        pass


@attrs.define(slots=False)
class CollectionTableModel(CollectionTableModelBase[K, C, CI], QtCore.QAbstractTableModel):
    _columns: list[str] = attrs.field(factory=list)
    _attrColumns: list[str] = attrs.field(factory=list)
    _derivedColumns: dict[str, tp.Callable[[K], tp.Any]] = attrs.field(factory=dict)
    """
    Mapping from column key to function which generates derived value for given key/index
    """
    _boolColumns: list[str] = attrs.field(factory=list)
    """
    Columns (attr or derived) that should be represented by a checkbox
    """
    _decoratedColumns: list[str] = attrs.field(factory=list)
    """
    Columns (attr or derived) that include an icon. Values of referenced columns should be (QIcon, text) tuples. Usually, it will be necessary to set up a derived column for proper formatting.
    """
    _editableColumns: list[str] = attrs.field(factory=list)
    _columnLabels: dict[str, str] = attrs.field(factory=dict)
    """
    Mapping from column key to nice label; if a key is not included, will be used directly as a label
    """
    _editableColumnValidators: dict[str, tp.Callable[[K, tp.Any, tp.Any], bool]] = attrs.field(factory=dict)
    """
    Mapping from column key to validator function which returns True if passed (prevVal, newVal) is valid
    """
    _derivedColumnSetters: dict[str, tp.Callable[[K, str], None]] = attrs.field(factory=dict)
    """"
    Mapping from (editable, derived) column key to a setter function which, given a key/index 
    and a newly-edited string value, applies changes to the underlying collection.
    
    E.g. may be used for converting string text field to float for a numeric attribute.
    """
    _isSelectedAttr: tp.Optional[str] = None
    """
    Key of attr in collection indicating selection status; if None, selection state will not be synced to model; partially reliant on connected CollectionTableWidget to implement
    """

    _hasPlaceholderNewRow: bool = False
    """
    Whether to include a row at end of table as a placeholder for adding a new entry
    """
    _placeholderNewRowDefaults: dict[str, tp.Any] = attrs.field(factory=dict)
    """
    When including a row at end of table as a placeholder for adding a new entry, what defaults should be shown.
    This is a mapping from columnKey -> default value. If a column is not present in mapping, its default
    will be empty or false.
    """
    _addNewRowFromEditedPlaceholder: tp.Optional[tp.Callable[[tp.Any,...], K | None]] = attrs.field(default=None)
    """
    When user tries editing the placeholder row, a new row should be added to the collection. Specify how to
    create a new collection entry by providing a function that accepts as arguments any edited kwargs (e.g.
    a new key), creates an instance of a collection item, and adds this to the model, and returns the corresponding key.     
    
    Only used if `hasPlaceholderNewRow` is True. If not specified, columns in the placeholder row will not
    be editable even if they are editable in other rows.
    
    For example, using typical GenericCollection and GenericCollectionItem subclasses::
        
        def addNewRow(**kwargs) -> str:
            item = CollectionItem(**kwargs)
            collection.addItem(item)
            return item.key
            
        addNewRowFromEditedPlaceholder=addNewRow 
    """

    _collection: C = attrs.field(init=False, repr=False)

    _pendingChangeType: tp.Optional[str] = attrs.field(init=False, default=None)

    _lastSelectedKeys: list[K] = attrs.field(init=False, factory=list)  # only used if _isSelectedAttr is None

    def __attrs_post_init__(self):
        CollectionTableModelBase.__attrs_post_init__(self)
        QtCore.QAbstractTableModel.__init__(self)

        doAutoCollateColumns = len(self._columns) == 0

        for seq in (self._attrColumns,
                    self._derivedColumns.keys(),
                    self._boolColumns,
                    self._decoratedColumns,
                    self._editableColumns,
                    self._columnLabels.keys(),
                    self._editableColumnValidators.keys(),
                    self._derivedColumnSetters.keys(),
                    self._placeholderNewRowDefaults.keys()):
            for key in seq:
                if doAutoCollateColumns:
                    if key not in self._columns:
                        self._columns.append(key)
                else:
                    assert key in self._columns, f'{key} not in main columns list'

    @property
    def collection(self):
        return self._collection

    @property
    def collectionIsDict(self):
        return hasattr(self._collection, 'keys')

    @property
    def columns(self):
        """
        Note: result may be modified in place within modifyingColumns() context
        """
        return self._columns

    @columns.setter
    def columns(self, columns: list[str]):
        assert self._pendingChangeType == 'columns', 'Can only modify columns within a modifyingColumns() context'
        self._columns = columns
        # validation handled at end of modifyingColumns context

    @property
    def attrColumns(self):
        """
        Note: result may be modified in place within modifyingColumns() context
        """
        return self._attrColumns

    @attrColumns.setter
    def attrColumns(self, attrColumns: list[str]):
        assert self._pendingChangeType == 'columns', 'Can only modify columns within a modifyingColumns() context'
        self._attrColumns = attrColumns
        # validation handled at end of modifyingColumns context

    @property
    def derivedColumns(self):
        """
        Note: result may be modified in place within modifyingColumns() context
        """
        return self._derivedColumns

    @derivedColumns.setter
    def derivedColumns(self, derivedColumns: dict[str, tp.Callable[[K], tp.Any]]):
        assert self._pendingChangeType == 'columns', 'Can only modify columns within a modifyingColumns() context'
        self._derivedColumns = derivedColumns
        # validation handled at end of modifyingColumns context

    # TODO: write other column getter/setters in similar format (asserting modifyingColumns context)

    @contextlib.contextmanager
    def modifyingColumns(self):

        if self._pendingChangeType:
            logger.error(f'Previous change still pending: {self._pendingChangeType}')
            raise RuntimeError
        self._pendingChangeType = 'columns'
        self.layoutAboutToBeChanged.emit()

        yield

        unreferencedColumns = set(self._columns)
        for seq in (self._attrColumns,
                    self._derivedColumns.keys()):
            for key in seq:
                try:
                    unreferencedColumns.remove(key)
                except KeyError:
                    pass
        if len(unreferencedColumns) > 0:
            raise ValueError(f'Unreferenced columns: {unreferencedColumns}')

        for seq in (self._attrColumns,
                    self._derivedColumns.keys(),
                    self._boolColumns,
                    self._decoratedColumns,
                    self._editableColumns,
                    self._columnLabels.keys(),
                    self._editableColumnValidators.keys(),
                    self._derivedColumnSetters.keys(),
                    self._placeholderNewRowDefaults.keys()):
            for key in seq:
                assert key in self._columns, f'{key} not in main columns list'

        assert self._pendingChangeType == 'columns'
        self.layoutChanged.emit()
        self._pendingChangeType = None

        self._updateSelection(keys=None, attrKeys=None)

    def rowCount(self, parent: tp.Union[QtCore.QModelIndex, QtCore.QPersistentModelIndex]=...) -> int:
        return len(self._collection) + (1 if self._hasPlaceholderNewRow else 0)

    def columnCount(self, parent: tp.Union[QtCore.QModelIndex, QtCore.QPersistentModelIndex]=...) -> int:
        return len(self._columns)

    def flags(self, index:tp.Union[QtCore.QModelIndex, QtCore.QPersistentModelIndex]) -> QtCore.Qt.ItemFlags:
        colKey = self._columns[index.column()]
        flags = super().flags(index)
        if colKey in self._boolColumns and colKey in self._editableColumns:
            flags |= QtCore.Qt.ItemIsUserCheckable

        if colKey in self._editableColumns and colKey not in self._boolColumns:
            if self._hasPlaceholderNewRow and self._addNewRowFromEditedPlaceholder is None:
                # editing columns not allowed if in placeholder row
                itemKey = self.getCollectionItemKeyFromIndex(index.row())
                if itemKey is None:
                    # in placeholder row
                    pass  # don't set flag
                else:
                    flags |= QtCore.Qt.ItemIsEditable
            else:
                flags |= QtCore.Qt.ItemIsEditable

        return flags

    def headerData(self, section: int, orientation: QtCore.Qt.Orientation, role: int=...) -> tp.Any:

        #logger.debug(f'{__name__} Getting headerData section {section} orientation {orientation} role {role}')

        match orientation:
            case QtCore.Qt.Horizontal:
                # column headers
                match role:
                    case QtCore.Qt.DisplayRole:
                        colKey = self._columns[section]
                        colLabel = self._columnLabels.get(colKey, colKey)
                        return colLabel
                    case _:
                        return None

            case QtCore.Qt.Vertical:
                # row headers
                match role:
                    case QtCore.Qt.DisplayRole:
                        return f'{section}'  # include row numbers
                    case _:
                        return None

            case _:
                raise NotImplementedError

    def data(self, index: tp.Union[QtCore.QModelIndex, QtCore.QPersistentModelIndex], role: int = ...) -> tp.Any:
        colKey = self._columns[index.column()]
        try:
            item = self.getCollectionItemFromIndex(index=index.row())
        except IndexError:
            return None  # TODO: double check what would be correct to return here

        if item is None and not self._hasPlaceholderNewRow:
            return None

        #logger.debug(f'Getting data for {self.getCollectionItemKeyFromIndex(index=index.row())} {colKey} role {role}')
        match role:
            case QtCore.Qt.DisplayRole | QtCore.Qt.ToolTipRole | QtCore.Qt.EditRole:
                if colKey in self._boolColumns:
                    # will be handled by CheckStateRole instead
                    return None
                elif colKey in self._attrColumns:
                    if item is None:
                        colVal = self._placeholderNewRowDefaults.get(colKey, (None, '') if colKey in self._decoratedColumns else '')
                    else:
                        colVal = getattr(item, colKey)
                    if colKey in self._decoratedColumns:
                        # assume val above is an (icon, text) tuple
                        assert len(colVal) == 2
                        colVal = colVal[1]
                    return str(colVal)
                elif colKey in self._derivedColumns:
                    colVal = self._derivedColumns[colKey](self.getCollectionItemKeyFromIndex(index=index.row()))
                    if colKey in self._decoratedColumns:
                        # assume val above is an (icon, text) tuple
                        assert len(colVal) == 2
                        colVal = colVal[1]
                    if isinstance(colVal, float):
                        colVal = f'{colVal:.4g}'  # TODO: make display precision (or format str) configurable rather than hardcoded
                    return str(colVal)
                else:
                    raise KeyError
            case QtCore.Qt.DecorationRole:
                if colKey in self._decoratedColumns:
                    if colKey in self._attrColumns:
                        if item is None:
                            colVal = self._placeholderNewRowDefaults.get(colKey, (None, ''))
                        else:
                            colVal = getattr(item, colKey)

                    elif colKey in self._derivedColumns:
                        colVal = self._derivedColumns[colKey](self.getCollectionItemKeyFromIndex(index=index.row()))

                    else:
                        raise KeyError

                    # assume val above is an (icon, text) tuple
                    assert len(colVal) == 2
                    colIcon = colVal[0]
                    assert colIcon is None or isinstance(colIcon, (QtGui.QColor, QtGui.QIcon, QtGui.QPixmap))
                    return colIcon

            case QtCore.Qt.CheckStateRole:
                if colKey in self._boolColumns:
                    if colKey in self._attrColumns:
                        if item is None:
                            colVal = self._placeholderNewRowDefaults.get(colKey, None)
                            if colVal is None:
                                return None  # don't show a checkbox for placeholder without a default
                        else:
                            colVal = getattr(item, colKey)
                    elif colKey in self._derivedColumns:
                        colVal = self._derivedColumns[colKey](self.getCollectionItemKeyFromIndex(index=index.row()))
                    else:
                        raise KeyError
                    assert isinstance(colVal, bool)
                    if colVal:
                        return int(QtCore.Qt.CheckState.Checked.value)
                    else:
                        return int(QtCore.Qt.CheckState.Unchecked.value)
                else:
                    return None
            case _:
                return None

    def setData(self, index:tp.Union[QtCore.QModelIndex, QtCore.QPersistentModelIndex], value:tp.Any, role: int = ...) -> bool:
        colKey = self._columns[index.column()]
        if colKey not in self._editableColumns:
            return False

        match role:
            case QtCore.Qt.EditRole:
                if colKey not in self._boolColumns:
                    logger.info(f'Editing column {colKey} to value {value}')
                    itemKey = self.getCollectionItemKeyFromIndex(index.row())

                    if colKey in self._editableColumnValidators:
                        oldValue = self.data(index, role)
                        isValid = self._editableColumnValidators[colKey](itemKey, oldValue, value)
                        if not isValid:
                            logger.warning('Attempted to set invalid value, rejecting change.')
                            return False

                    if self._hasPlaceholderNewRow and itemKey is None:
                        # tried to edit placeholder row, so create a new row and apply the edit to it
                        assert self._addNewRowFromEditedPlaceholder is not None
                        if (colKey in self._placeholderNewRowDefaults and value == self._placeholderNewRowDefaults[colKey]) \
                                or (colKey not in self._placeholderNewRowDefaults and len(value) == 0):
                            # no change from default, assume addition was cancelled
                            return False
                        newItemKey = self._addNewRowFromEditedPlaceholder(**{colKey: value})
                        if newItemKey is None:
                            # problem adding new row, reject change
                            return False
                        # TODO: change current item to newly created row
                        return True

                    if colKey in self._derivedColumnSetters:
                        self._derivedColumnSetters[colKey](itemKey, value)
                    elif colKey in self._attrColumns:
                        setattr(self._collection[itemKey], colKey, value)
                    else:
                        raise KeyError
                    return True
                else:
                    return False
            case QtCore.Qt.CheckStateRole:
                if colKey in self._boolColumns:
                    if colKey in self._attrColumns:
                        isChecked = value == QtCore.Qt.CheckState.Checked.value
                        itemKey = self.getCollectionItemKeyFromIndex(index.row())
                        if self._isSelectedAttr is None:
                            lastSelectedKeys = self._lastSelectedKeys
                        else:
                            lastSelectedKeys = [key for key in self._collection.keys() if getattr(self._collection[key], self._isSelectedAttr)]
                        if itemKey in lastSelectedKeys and True:
                            # apply edited checkbox value to all selected rows
                            self._collection.setAttribForItems(keys=lastSelectedKeys,
                                                               attribsAndValues={colKey: [isChecked] * len(lastSelectedKeys)})
                        else:
                            # apply edited checkbox value to current row only
                            logger.debug(f'Editing column {colKey} to value {isChecked}')
                            setattr(self.getCollectionItemFromIndex(index.row()), colKey, isChecked)
                            self.dataChanged.emit(index, index, [role])
                        return True
                    elif colKey in self._derivedColumns:
                        raise NotImplementedError
                    else:
                        raise KeyError
                else:
                    return False
            case _:
                return False

    def getCollectionItemFromIndex(self, index: int) -> tp.Optional[CI]:
        key = self.getCollectionItemKeyFromIndex(index)
        if key is None:
            return None  # return None as indicator of being in placeholder row

        return self._collection[key]

    def getCollectionItemKeyFromIndex(self, index: int) -> tp.Optional[K]:
        if index >= len(self._collection):
            if index == len(self._collection) and self._hasPlaceholderNewRow:
                return None  # return None as indicator of being in placeholder row
            elif index == 0:
                # this can sometimes happen after deleting all entries in a collection
                return None
            else:
                raise IndexError

        if self.collectionIsDict:
            key = list(self._collection.keys())[index]
            return key
        else:
            return index

    def getIndexFromCollectionItemKey(self, key: K) -> tp.Optional[int]:
        if self.collectionIsDict:
            try:
                index = list(self._collection.keys()).index(key)
            except ValueError:
                index = None
        else:
            if key < len(self._collection):
                index = key
            else:
                index = None
        return index

    def setWhichItemsSelected(self, selectedKeys: list[K]):
        """
        To be called by CollectionTableWidget to update model tracking of selection state.
        Does not directly change actual view selection state.
        """

        if self._pendingChangeType is not None:
            logger.debug(f'Ignoring setWhichItemsSelected ({selectedKeys}) since another change is pending ({self._pendingChangeType})')
            return  # ignore pending changes, assume will be handled later

        if self._isSelectedAttr is None:
            self._lastSelectedKeys = selectedKeys
        else:
            self._collection.setAttribForItems(self._collection.keys(),
                                               {self._isSelectedAttr:[key in selectedKeys for key in self._collection.keys()]})

    def _inferCollectionChangeType(self, keys: tp.Optional[list[K]], attrKeys: tp.Optional[list[str]]) -> str:

        changeType = None

        #return 'full'  # TODO: debug, delete

        if keys is None:
            changeType = 'full'
        else:
            indices = list(set(self.getIndexFromCollectionItemKey(key) for key in keys))
            if any(index is None for index in indices):
                if any(index is not None for index in indices):
                    # mix of previous items changing and new items being added; just do a full layout change
                    changeType = 'full'
                else:
                    # new items only
                    changeType = 'insertRows'
            else:
                # no new items
                # since we can't tell the difference at this point between a remove, a reorder, or a modify
                #  existing, just assume if all attributes are changing that we should do a full update
                #  (to handle any of these cases)
                # TODO: add a way to tell if we are going to do a remove and do more efficient partial removeRows instead
                if attrKeys is None:
                    changeType = 'full'
                else:
                    changeType = 'modifyExisting'


        return changeType

    def _onCollectionAboutToChange(self, keys: tp.Optional[list[K]], attrKeys: tp.Optional[list[str]] = None):
        """
        Should be called by subclass (e.g. via connected signal) whenever underlying collection is about to change
        :param keys: keys (or indices for non-dict collections) of items about to change; if None, all items are assumed to be about to change
        :param attrKeys: optional keys of attributes about to change; if None, all attributes are assumed to be about to change
        """

        if self._pendingChangeType:
            logger.error(f'Previous change still pending: {self._pendingChangeType}')
            raise RuntimeError
        self._pendingChangeType = self._inferCollectionChangeType(keys=keys, attrKeys=attrKeys)

        logger.debug(f'Signaling start of {self._pendingChangeType} change for keys {keys}, attrKeys {attrKeys}')

        match self._pendingChangeType:
            case 'full':
                self.layoutAboutToBeChanged.emit()

            case 'insertRows':
                indices = list(self.getIndexFromCollectionItemKey(key) for key in keys)
                assert all(index is None for index in indices)
                # assume being added at end of collection
                self.beginInsertRows(QtCore.QModelIndex(), len(self._collection), len(self._collection) + len(indices)-1)

            case 'modifyExisting':
                pass  # do nothing here (will emit dataChanged after change complete)

            case _:
                raise NotImplementedError

    def _onCollectionChanged(self, keys: tp.Optional[list[K]], attrKeys: tp.Optional[list[str]] = None):
        """
        Should be called by subclass (e.g. via connected signal) whenever underlying collection has changed
        :param keys: keys (or indices for non-dict collections) of items changed; if None, all items are assumed to be about to change
        :param attrKeys: optional keys of attributes changed; if None, all attributes are assumed to have changed
        """

        assert self._pendingChangeType is not None

        logger.debug(f'Signaling completion of {self._pendingChangeType} change for keys {keys}, attrKeys {attrKeys}')

        doUpdateSelection = False

        match self._pendingChangeType:
            case 'full':
                # TODO: update persistent indices with `changePersistentIndex` as required (?) by Qt model interface
                self.layoutChanged.emit()
                doUpdateSelection = True

            case 'insertRows':
                self.endInsertRows()

            case 'modifyExisting':
                indices = list(set(self.getIndexFromCollectionItemKey(key) for key in keys))
                assert all(index is not None for index in indices)
                assert len(indices) == len(keys), 'One or more keys repeated'
                minIndex = min(indices)
                maxIndex = max(indices)

                # TODO: also only emit for certain columns if attrKeys specified
                logger.debug(f'minIndex {minIndex}; maxIndex {maxIndex}, numColumns {len(self._columns)}')
                self.dataChanged.emit(self.index(minIndex, 0), self.index(maxIndex, len(self._columns)))

                doUpdateSelection = True

            case _:
                raise NotImplementedError

        self._pendingChangeType = None

        if doUpdateSelection:
            self._updateSelection(keys=keys, attrKeys=attrKeys)

        logger.debug(f'Done signaling completion for keys {keys}, attrKeys {attrKeys}')

    def _updateSelection(self, keys: list[str] | None, attrKeys: list[str] | None):
        logger.debug(f'Updating selection')
        if self._isSelectedAttr is not None and (attrKeys is None or self._isSelectedAttr in attrKeys):
            keysToEmit = keys
            if keysToEmit is None:
                if self.collectionIsDict:
                    keysToEmit = list(self._collection.keys())
                else:
                    keysToEmit = list(range(len(self._collection)))
            logger.debug(f'Keys with selection changing: {keysToEmit}')
            self.sigSelectionChanged.emit(keysToEmit)

    def getCollectionItemIsSelected(self, key: K) -> bool:
        if not self._isSelectedAttr:
            raise NotImplementedError

        return getattr(self._collection[key], self._isSelectedAttr)


@attrs.define(slots=False)
class FilteredCollectionModel(CollectionTableModelBase[K, C, CI], QtCore.QSortFilterProxyModel):
    """
    Base class for models that filter a collection model, e.g. Targets subset
    """

    _proxiedModel: CollectionTableModel = attrs.field(init=False, default=None)

    def __attrs_post_init__(self):
        CollectionTableModelBase.__attrs_post_init__(self)
        QtCore.QSortFilterProxyModel.__init__(self)
        assert self._proxiedModel is not None, 'Should be set by subclass before calling super().__attrs_post_init__'
        self._proxiedModel.sigSelectionChanged.connect(self._onFullSelectionChanged, priority=-1)
        self.setSourceModel(self._proxiedModel)

    def filterAcceptsRow(self, sourceRow: int, sourceParent: QtCore.QModelIndex) -> bool:
        raise NotImplementedError  # should be implemented by subclass

    def filterAcceptsColumn(self, source_column: int, source_parent: QtCore.QModelIndex) -> bool:
        return True  # can be implemented by subclass to filter specific columns

    def getCollectionItemFromIndex(self, index: int) -> CI | None:
        return self._proxiedModel.getCollectionItemFromIndex(self.mapToSource(self.index(index, 0)).row())

    def getCollectionItemKeyFromIndex(self, index: int) -> str | None:
        return self._proxiedModel.getCollectionItemKeyFromIndex(self.mapToSource(self.index(index, 0)).row())

    def getIndexFromCollectionItemKey(self, key: str) -> int | None:
        proxiedRow = self._proxiedModel.getIndexFromCollectionItemKey(key)
        if proxiedRow is None:
            return None
        return self.mapFromSource(self._proxiedModel.index(proxiedRow, 0)).row()

    def getCollectionItemIsSelected(self, key: str) -> bool:
        return self._proxiedModel.getCollectionItemIsSelected(key)

    def setWhichItemsSelected(self, selectedKeys: list[K]):
        logger.debug(f'setWhichItemsSelected: {selectedKeys}')
        if True:
            # keep any keys in full model that were filtered out here still selected
            selectedKeys = set(selectedKeys)
            for sourceRow in range(self._proxiedModel.rowCount()):
                if not self.filterAcceptsRow(sourceRow=sourceRow, sourceParent=QtCore.QModelIndex()):
                    key = self._proxiedModel.getCollectionItemKeyFromIndex(sourceRow)
                    if self._proxiedModel.getCollectionItemIsSelected(key=key):
                        selectedKeys.add(key)

        self._proxiedModel.setWhichItemsSelected(selectedKeys)

    def _onFullSelectionChanged(self, keys: list[str]):
        logger.debug(f'onFullSelectionChanged: {keys}')
        self.sigSelectionChanged.emit(keys)  # TODO: filter to just subset of keys

