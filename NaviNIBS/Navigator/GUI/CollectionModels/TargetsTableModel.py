import attrs
import typing as tp

from qtpy import QtWidgets, QtCore, QtGui

from NaviNIBS.Navigator.GUI.CollectionModels import \
    CollectionTableModel, CollectionTableModelBase,\
    FilteredCollectionModel, \
    K, logger
from NaviNIBS.Navigator.Model.Targets import Targets, Target


@attrs.define(slots=False, kw_only=True)
class FullTargetsTableModel(CollectionTableModel[str, Targets, Target]):
    _collection: Targets = attrs.field(init=False, repr=False)
    _isSelectedAttr: str = 'isSelected'
    _defaultTargetColor: str = '#2222FF'
    _doShowColorColumn: bool = True
    _iconCache: dict[str, QtGui.QIcon] = attrs.field(init=False, factory=dict, repr=False)

    def __attrs_post_init__(self):
        self._collection = self._session.targets

        self._boolColumns.extend(['isVisible', 'isHistorical'])
        self._editableColumns.extend(['isVisible', 'key'])
        # TODO: make color editable

        self._attrColumns = [
            'key',
            'isVisible',
            'isHistorical',
            # TODO: add angle, depthOffset, target coord, entry coord as optional but hidden by default
        ]
        self._columnLabels = dict(
            key='Key',
            isVisible='Visibility',
            isHistorical='Historical',
        )
        if self._doShowColorColumn:
            self._derivedColumns = dict(
                color=self._getColor,
            )
            self._decoratedColumns = ['color']
            self._columns = ['key', 'isVisible' , 'color', 'isHistorical']
            self._columnLabels['color'] = 'Color'
        else:
            self._columns = self._attrColumns.copy()

        self._editableColumnValidators = dict(
            key=lambda _, oldKey, newKey: len(newKey) > 0 and (oldKey == newKey or newKey not in self._session.targets),  # don't allow setting one target to key of another target
        )

        self._collection.sigItemsAboutToChange.connect(self._onCollectionAboutToChange, priority=-2)
        self._collection.sigItemsChanged.connect(self._onCollectionChanged, priority=2)

        super().__attrs_post_init__()

    # cached icon generator
    def _getIconWithColor(self, color: str) -> QtGui.QIcon:
        if color in self._iconCache:
            return self._iconCache[color]
        pixmap = QtGui.QPixmap(16, 16)
        pixmap.fill(QtGui.QColor(color))
        icon = QtGui.QIcon(pixmap)
        self._iconCache[color] = icon
        return icon

    def _getColor(self, key: str | None) -> tuple[QtGui.QIcon | None, str]:
        if key is None:
            return None, ''
        target = self._collection[key]
        color = target.color if target.color is not None else self._defaultTargetColor
        return self._getIconWithColor(color), ''

@attrs.define(slots=False, kw_only=True)
class TargetsTableModel(FilteredCollectionModel[str, Targets, Target]):
    """
    Non-historical targets only
    """
    _proxiedModel: FullTargetsTableModel = attrs.field(init=False)
    _defaultTargetColor: str = '#2222FF'
    _doShowColorColumn: bool = True

    def __attrs_post_init__(self):
        self._proxiedModel = FullTargetsTableModel(session=self._session,
                                                   defaultTargetColor=self._defaultTargetColor,
                                                   doShowColorColumn=self._doShowColorColumn)

        FilteredCollectionModel.__attrs_post_init__(self)

    def filterAcceptsRow(self, sourceRow: int, sourceParent: QtCore.QModelIndex) -> bool:
        target = self._proxiedModel.getCollectionItemFromIndex(sourceRow)
        return not target.isHistorical

    def filterAcceptsColumn(self, source_column: int, source_parent: QtCore.QModelIndex):
        columnKey = self._proxiedModel.columns[source_column]
        return columnKey not in ('isHistorical',)


