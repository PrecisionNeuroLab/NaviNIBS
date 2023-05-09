from __future__ import annotations
import attrs
import json
import logging
import typing as tp

from RTNaBS.Navigator.Model.GenericCollection import GenericCollection, GenericCollectionDictItem
import RTNaBS.util.GUI.DockWidgets as dw

if tp.TYPE_CHECKING:
    from qtpy import QtWidgets, QtGui, QtCore

logger = logging.getLogger(__name__)


@attrs.define
class DockWidgetLayout(GenericCollectionDictItem[str]):
    _affinities: list[str]
    _layout: dict[str, tp.Any] | None = None

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @property
    def affinities(self):
        return self._affinities

    @property
    def layout(self):
        return self._layout

    @layout.setter
    def layout(self, layout: dict[str, tp.Any]):
        if self._layout == layout:
            return
        self.sigItemAboutToChange.emit(self.key, ['layout'])
        self._layout = layout
        self.sigItemChanged.emit(self.key, ['layout'])

    def saveLayout(self):
        """
        Save current layout to self.layout (note that this doesn't write to a file, just updates the session model)
        """
        saver = dw.LayoutSaver()
        saver.setAffinityNames(self.affinities)
        layoutStr = bytes(saver.serializeLayout()).decode('utf-8')
        self.layout = json.loads(layoutStr)

    def restoreLayout(self, wdgt: QtWidgets.QWidget):
        layoutStr = json.dumps(self.layout)

        # to work around some quirks of KDDockWidgets LayoutSaver, force visible to True to avoid hiding entire window
        # (maybe related to https://github.com/KDAB/KDDockWidgets/issues/343 but not with QtQuick)
        # TODO: just force lowest level to be visible (whatever is needed to avoid hiding entire window) rather than modifying every descendant marked as not visible
        layoutStr = layoutStr.replace('"isVisible": false', '"isVisible": true')

        logger.debug(f'Restoring child layout for {self.affinities}')
        saver = dw.LayoutSaver()
        saver.setAffinityNames(self.affinities)
        saver.restoreLayout(layoutStr.encode('utf-8'))


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
