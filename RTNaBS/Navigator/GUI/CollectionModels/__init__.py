import attrs
from collections.abc import Sequence, Mapping
import logging
import typing as tp
from qtpy import QtWidgets, QtCore, QtGui

from RTNaBS.Navigator.Model import Session
from RTNaBS.Navigator.Model.Samples import Sample, Samples
from RTNaBS.Navigator.Model.Targets import Target, Targets
from RTNaBS.Navigator.Model.SubjectRegistration import HeadPoint, HeadPoints
from RTNaBS.util.Signaler import Signal


logger = logging.getLogger(__name__)


K = tp.TypeVar('K', int, str)  # collection item key type
C = tp.TypeVar('C')  # collection type
CI = tp.TypeVar('CI')  # collection item type


@attrs.define(slots=False)
class CollectionTableModel(QtCore.QAbstractTableModel, tp.Generic[K, C, CI]):
    _session: Session
    _columns: list[str] = attrs.field(factory=list)
    _attrColumns: list[str] = attrs.field(factory=list)
    _derivedColumns: list[str] = attrs.field(factory=list)
    _boolColumns: list[str] = attrs.field(factory=list)
    _editableColumns: list[str] = attrs.field(factory=list)
    _columnLabels: dict[str, str] = attrs.field(factory=dict)  # mapping from column key to nice label; if a key is not included, will be used directly as a label
    _editableColumnValidators: dict[str, tp.Callable[[tp.Any, tp.Any], bool]] = attrs.field(factory=dict)  # mapping from column key to validator function which returns True if passed (prevVal, newVal) is valid
    _isSelectedAttr: tp.Optional[str] = None  # key of attr in collection indicating selection status; if None, selection state will not be synced to model; partially reliant on connected CollectionTableWidget to implement

    _collection: tp.Union[Sequence[CI], Mapping[K, CI]] = attrs.field(init=False)

    _pendingChangeType: tp.Optional[str] = attrs.field(init=False, default=None)

    sigSelectionChanged: Signal = attrs.field(init=False, factory=lambda: Signal((list[str],)))

    def __attrs_post_init__(self):
        QtCore.QAbstractTableModel.__init__(self)

    @property
    def collectionIsDict(self):
        return hasattr(self._collection, 'keys')

    def rowCount(self, parent: tp.Union[QtCore.QModelIndex, QtCore.QPersistentModelIndex]=...) -> int:
        return len(self._collection)

    def columnCount(self, parent: tp.Union[QtCore.QModelIndex, QtCore.QPersistentModelIndex]=...) -> int:
        return len(self._columns)

    def flags(self, index:tp.Union[QtCore.QModelIndex, QtCore.QPersistentModelIndex]) -> QtCore.Qt.ItemFlags:
        colKey = self._columns[index.column()]
        flags = super().flags(index)
        if colKey in self._boolColumns and colKey in self._editableColumns:
            flags |= QtCore.Qt.ItemIsUserCheckable

        if colKey in self._editableColumns and colKey not in self._boolColumns:
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
        item = self.getCollectionItemFromIndex(index=index.row())
        #logger.debug(f'Getting data for {self.getCollectionItemKeyFromIndex(index=index.row())} {colKey} role {role}')
        match role:
            case QtCore.Qt.DisplayRole | QtCore.Qt.ToolTipRole:
                if colKey in self._boolColumns:
                    # will be handled by CheckStateRole instead
                    return None
                elif colKey in self._attrColumns:
                    colVal = getattr(item, colKey)
                    return str(colVal)
                elif colKey in self._derivedColumns:
                    raise NotImplementedError  # TODO
                else:
                    raise KeyError
            case QtCore.Qt.CheckStateRole:
                if colKey in self._boolColumns:
                    colVal = getattr(item, colKey)
                    assert isinstance(colVal, bool)
                    if colVal:
                        return int(QtCore.Qt.Checked)
                    else:
                        return int(QtCore.Qt.Unchecked)
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
                    if colKey in self._editableColumnValidators:
                        oldValue = self.data(index, role)
                        isValid = self._editableColumnValidators[colKey](oldValue, value)
                        if not isValid:
                            logger.warning('Attempted to set invalid value, rejecting change.')
                            return False
                    setattr(self.getCollectionItemFromIndex(index.row()), colKey, value)
                    return True
                else:
                    return False
            case QtCore.Qt.CheckStateRole:
                if colKey in self._boolColumns:
                    if colKey in self._attrColumns:
                        isChecked = value == QtCore.Qt.CheckState.Checked
                        setattr(self.getCollectionItemFromIndex(index.row()), colKey, isChecked)
                        self.dataChanged.emit(index, index, [role])
                        return True
                    else:
                        raise NotImplementedError
                else:
                    return False
            case _:
                return False

    def getCollectionItemFromIndex(self, index: int) -> CI:
        return self._collection[self.getCollectionItemKeyFromIndex(index)]

    def getCollectionItemKeyFromIndex(self, index: int) -> K:
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
        if self._isSelectedAttr is None:
           pass  # ignore
        else:
            raise NotImplementedError  # should be implemented by subclass

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

    def _onCollectionAboutToChange(self, keys: tp.Optional[list[K]], attrKeys: tp.Optional[list[str]]):
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
                indices = list(set(self.getIndexFromCollectionItemKey(key) for key in keys))
                assert all(index is None for index in indices)
                # assume being added at end of collection
                self.beginInsertRows(QtCore.QModelIndex(), len(self._collection), len(self._collection) + len(indices)-1)

            case 'modifyExisting':
                pass  # do nothing here (will emit dataChanged after change complete)

            case _:
                raise NotImplementedError

    def _onCollectionChanged(self, keys: tp.Optional[list[K]], attrKeys: tp.Optional[list[str]]):
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


# TODO: move these to separate files
@attrs.define(slots=False)
class SamplesTableModel(CollectionTableModel[str, Samples, Sample]):
    _collection: Samples = attrs.field(init=False)
    _isSelectedAttr: str = 'isSelected'

    def __attrs_post_init__(self):
        self._collection = self._session.samples

        self._boolColumns.extend(['isVisible', 'hasTransf'])
        self._editableColumns.extend(['isVisible'])

        self._columns = [
            'key',
            'isVisible',
            'hasTransf',
            'targetKey',
            'timestamp',
            # TODO: add color
        ]
        self._attrColumns = self._columns.copy()
        self._columnLabels = dict(
            key='Key',
            isVisible='Visibility',
            hasTransf='Has pose',
            targetKey='Target',
            timestamp='Time'
        )

        self._collection.sigSamplesAboutToChange.connect(self._onCollectionAboutToChange)
        self._collection.sigSamplesChanged.connect(self._onCollectionChanged)

        super().__attrs_post_init__()

    def setWhichItemsSelected(self, selectedKeys: list[K]):
        if self._pendingChangeType is not None:
            return  # ignore pending changes, assume will be handled later
        self._collection.setWhichSamplesSelected(selectedKeys)


@attrs.define(slots=False)
class TargetsTableModel(CollectionTableModel[str, Targets, Target]):
    _collection: Targets = attrs.field(init=False)
    _isSelectedAttr: str = 'isSelected'

    def __attrs_post_init__(self):
        self._collection = self._session.targets

        self._boolColumns.extend(['isVisible'])
        self._editableColumns.extend(['isVisible', 'key'])

        self._columns = [
            'key',
            'isVisible'
            # TODO: add color
            # TODO: add angle, depthOffset, target coord, entry coord as optional but hidden by default
        ]
        self._attrColumns = self._columns.copy()
        self._columnLabels = dict(
            key='Key',
            isVisible='Visibility',
        )

        self._editableColumnValidators = dict(
            key=lambda oldKey, newKey: len(newKey) > 0 and (oldKey == newKey or newKey not in self._session.targets),  # don't allow setting one target to key of another target
        )

        self._collection.sigTargetsAboutToChange.connect(self._onCollectionAboutToChange)
        self._collection.sigTargetsChanged.connect(self._onCollectionChanged)

        super().__attrs_post_init__()

    def setWhichItemsSelected(self, selectedKeys: list[K]):
        if self._pendingChangeType is not None:
            return  # ignore pending changes, assume will be handled later
        logger.debug(f'setWhichItemsSelected: {selectedKeys}')
        self._collection.setWhichTargetsSelected(selectedKeys)