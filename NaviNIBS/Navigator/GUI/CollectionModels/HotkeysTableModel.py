import attrs
import logging
import typing as tp
from qtpy import QtGui

from NaviNIBS.Navigator.GUI.CollectionModels import CollectionTableModel, K
from NaviNIBS.Navigator.Model.Triggering import Hotkey, Hotkeys, HotkeyTriggerSource
from NaviNIBS.util import makeStrUnique

logger = logging.getLogger()


@attrs.define(slots=False, kw_only=True)
class HotkeysTableModel(CollectionTableModel[str, Hotkeys, Hotkey]):
    _triggerSourceKey: str
    _collection: Hotkeys = attrs.field(init=False, repr=False)

    _hasPlaceholderNewRow: bool = True
    _placeholderNewRowDefaults: dict[str, tp.Any] = attrs.field(factory=lambda: dict(key='<NewHotkey>'))

    def __attrs_post_init__(self):
        triggerSource = self._session.triggerSources[self._triggerSourceKey]
        assert isinstance(triggerSource, HotkeyTriggerSource)
        self._collection = triggerSource.hotkeys

        # TODO: subscribe to changes in triggerSource

        self._attrColumns = [
            'key',
            'action',
            'keyboardDeviceID'
        ]

        self._editableColumns = self._attrColumns.copy()

        self._collection.sigItemsAboutToChange.connect(self._onCollectionAboutToChange)
        self._collection.sigItemsChanged.connect(self._onCollectionChanged)

        self._addNewRowFromEditedPlaceholder = self.__addNewRowFromEditedPlaceholder

        super().__attrs_post_init__()

    def __addNewRowFromEditedPlaceholder(self, **kwargs) -> str:
        if 'key' not in kwargs:
            kwargs['key'] = makeStrUnique('Hotkey', self._collection.keys())
        if 'action' not in kwargs:
            kwargs['action'] = 'sample'
        item = Hotkey(**kwargs)
        self._collection.addItem(item)
        return item.key