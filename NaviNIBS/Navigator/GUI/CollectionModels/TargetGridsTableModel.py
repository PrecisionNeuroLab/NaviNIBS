import attrs
import typing as tp

from qtpy import QtWidgets, QtCore, QtGui

from NaviNIBS.Navigator.GUI.CollectionModels import \
    CollectionTableModel, CollectionTableModelBase,\
    FilteredCollectionModel, \
    K, logger
from NaviNIBS.Navigator.Model.TargetGrids import TargetGrids, TargetGrid


@attrs.define(slots=False, kw_only=True)
class TargetGridsTableModel(CollectionTableModel[str, TargetGrids, TargetGrid]):
    _collection: TargetGrids = attrs.field(init=False, repr=False)

    def __attrs_post_init__(self):
        self._collection = self._session.targetGrids

        self._editableColumns.extend(['key'])

        self._columns = [
            'key',
            'numGeneratedTargets',
            'seedTargetKey'
        ]
        self._attrColumns = self._columns.copy()
        self._columnLabels = dict(
            key='Key',
            seedTargetKey='Seed target',
            numGeneratedTargets='Num targets',  # note this will be reduced when generated targets are edited manually
        )

        self._editableColumnValidators = dict(
            key=lambda _, oldKey, newKey:
            len(newKey) > 0 and
            (oldKey == newKey or newKey not in self._session.targetGrids),  # don't allow setting one targetGrid to key of another targetGrid
        )

        self._collection.sigItemsAboutToChange.connect(self._onCollectionAboutToChange, priority=-2)
        self._collection.sigItemsChanged.connect(self._onCollectionChanged, priority=2)

        super().__attrs_post_init__()



