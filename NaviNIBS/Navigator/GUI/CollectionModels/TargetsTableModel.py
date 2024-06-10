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
    _collection: Targets = attrs.field(init=False)
    _isSelectedAttr: str = 'isSelected'

    def __attrs_post_init__(self):
        self._collection = self._session.targets

        self._boolColumns.extend(['isVisible', 'isHistorical'])
        self._editableColumns.extend(['isVisible', 'key'])

        self._columns = [
            'key',
            'isVisible',
            'isHistorical',
            # TODO: add color
            # TODO: add angle, depthOffset, target coord, entry coord as optional but hidden by default
        ]
        self._attrColumns = self._columns.copy()
        self._columnLabels = dict(
            key='Key',
            isVisible='Visibility',
            isHistorical='Historical',
        )

        self._editableColumnValidators = dict(
            key=lambda _, oldKey, newKey: len(newKey) > 0 and (oldKey == newKey or newKey not in self._session.targets),  # don't allow setting one target to key of another target
        )

        self._collection.sigItemsAboutToChange.connect(self._onCollectionAboutToChange)
        self._collection.sigItemsChanged.connect(self._onCollectionChanged)

        super().__attrs_post_init__()


@attrs.define(slots=False, kw_only=True)
class TargetsTableModel(FilteredCollectionModel[str, Targets, Target]):
    """
    Non-historical targets only
    """
    _proxiedModel: FullTargetsTableModel = attrs.field(init=False)

    def __attrs_post_init__(self):
        self._proxiedModel = FullTargetsTableModel(session=self._session)

        FilteredCollectionModel.__attrs_post_init__(self)

    def filterAcceptsRow(self, sourceRow: int, sourceParent: QtCore.QModelIndex) -> bool:
        target = self._proxiedModel.getCollectionItemFromIndex(sourceRow)
        return not target.isHistorical

    def filterAcceptsColumn(self, source_column: int, source_parent: QtCore.QModelIndex):
        columnKey = self._proxiedModel.columns[source_column]
        return columnKey not in ('isHistorical',)


