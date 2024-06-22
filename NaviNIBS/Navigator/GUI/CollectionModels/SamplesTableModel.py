import attrs
import logging

from NaviNIBS.Navigator.GUI.CollectionModels import CollectionTableModel, K
from NaviNIBS.Navigator.Model.Samples import Samples, Sample


logger = logging.getLogger(__name__)


@attrs.define(slots=False)
class SamplesTableModel(CollectionTableModel[str, Samples, Sample]):
    _collection: Samples = attrs.field(init=False, repr=False)
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

        self._collection.sigItemsAboutToChange.connect(self._onSamplesAboutToChange)
        self._collection.sigItemsChanged.connect(self._onSamplesChanged)

        super().__attrs_post_init__()

        # simulate a change to initialize metadata columns
        self._onSamplesAboutToChange(None, None)
        self._onSamplesChanged(None, None)

    def _onSamplesAboutToChange(self, keys: list[str] | None, attrKeys: list[str] | None = None):
        self._onCollectionAboutToChange(keys, attrKeys)

    def _onSamplesChanged(self, keys: list[str] | None, attrKeys: list[str] | None = None):
        self._onCollectionChanged(keys, attrKeys)

        if attrKeys is None or 'metadata' in attrKeys:
            # may have added new metadata field, check if we should add a derived column for it
            sampleKeys = keys
            if sampleKeys is None:
                sampleKeys = self._collection.keys()
            for sampleKey in sampleKeys:
                sample = self._collection[sampleKey]
                for metadataKey in sample.metadata.keys():
                    if metadataKey not in self.derivedColumns:
                        assert metadataKey not in self.columns
                        with self.modifyingColumns():
                            self.derivedColumns[metadataKey] = lambda sampleKey, metadataKey=metadataKey: self._collection[sampleKey].metadata.get(metadataKey, None)
                            self.columns.append(metadataKey)


