import attrs
import logging

from RTNaBS.Navigator.GUI.CollectionModels import CollectionTableModel, K
from RTNaBS.Navigator.Model.Samples import Samples, Sample


logger = logging.getLogger(__name__)


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

        self._collection.sigItemsAboutToChange.connect(self._onCollectionAboutToChange)
        self._collection.sigItemsChanged.connect(self._onCollectionChanged)

        super().__attrs_post_init__()

    