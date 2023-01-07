import attrs

from RTNaBS.Navigator.GUI.CollectionModels import CollectionTableModel, K, logger
from RTNaBS.Navigator.Model.Targets import Targets, Target


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

        self._collection.sigItemsAboutToChange.connect(self._onCollectionAboutToChange)
        self._collection.sigItemsChanged.connect(self._onCollectionChanged)

        super().__attrs_post_init__()

    def setWhichItemsSelected(self, selectedKeys: list[K]):
        if self._pendingChangeType is not None:
            return  # ignore pending changes, assume will be handled later
        logger.debug(f'setWhichItemsSelected: {selectedKeys}')
        self._collection.setWhichTargetsSelected(selectedKeys)
