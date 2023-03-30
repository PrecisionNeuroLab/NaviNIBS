import attrs

from RTNaBS.Navigator.GUI.CollectionModels import CollectionTableModel, K, logger
from RTNaBS.Navigator.Model.Tools import Tool, Tools


@attrs.define(slots=False)
class ToolsTableModel(CollectionTableModel[int, Tools, Tool]):
    _collection: Tools = attrs.field(init=False)

    def __attrs_post_init__(self):
        self._collection = self._session.tools

        self._columns = [
            'label',
            'isActive',
            'doRenderTool',
            'doRenderTracker',
            'doShowTrackingState'
        ]

        self._attrColumns = self._columns.copy()

        self._columnLabels = dict(
            label='Label',
            isActive='Active',
            doRenderTool='Render tool',
            doRenderTracker='Render tracker',
            doShowTrackingState='Show tracking state'
        )

        self._boolColumns = [
            'isActive',
            'doRenderTool',
            'doRenderTracker',
            'doShowTrackingState'
        ]

        self._editableColumns = self._boolColumns.copy()

        self._collection.sigItemsAboutToChange.connect(self._onCollectionAboutToChange)
        self._collection.sigItemsChanged.connect(self._onCollectionChanged)

        super().__attrs_post_init__()





