from __future__ import annotations
import attrs
import json
import logging
import typing as tp

from RTNaBS.Navigator.Model.GenericCollection import GenericCollection, GenericCollectionDictItem

if tp.TYPE_CHECKING:
    from qtpy import QtWidgets, QtGui, QtCore

logger = logging.getLogger(__name__)


@attrs.define
class DockWidgetLayout(GenericCollectionDictItem[str]):
    _affinities: list[str]
    _state: dict[str, tp.Any] | None = None

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @property
    def affinities(self):
        return self._affinities

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, state: dict[str, tp.Any]):
        if self._state == state:
            return
        self.sigItemAboutToChange.emit(self.key, ['state'])
        self._state = state
        self.sigItemChanged.emit(self.key, ['state'])


@attrs.define
class DockWidgetLayouts(GenericCollection[str, DockWidgetLayout]):
    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @classmethod
    def fromList(cls, itemList: list[dict[str, tp.Any]]) -> DockWidgetLayouts:
        items = {}
        for itemDict in itemList:
            item = DockWidgetLayout.fromDict(itemDict)
            items[item.key] = item

        return cls(items=items)
