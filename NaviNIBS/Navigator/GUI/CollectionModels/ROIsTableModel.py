import attrs
import typing as tp

import qtawesome as qta
from qtpy import QtWidgets, QtCore, QtGui

from NaviNIBS.Navigator.GUI.CollectionModels import \
    CollectionTableModel, CollectionTableModelBase,\
    K, logger
from NaviNIBS.Navigator.Model.ROIs import ROIs, ROI


@attrs.define(slots=False, kw_only=True)
class ROIsTableModel(CollectionTableModel[str, ROIs, ROI]):

    _collection: ROIs = attrs.field(init=False, repr=False)

    def __attrs_post_init__(self):
        self._collection = self._session.ROIs

        self._boolColumns.extend(['isVisible'])
        self._editableColumns.extend(['isVisible', 'key'])

        self._columns = [
            'key',
            'isVisible',
            'color',
        ]
        self._attrColumns = [
            'key',
            'isVisible',
        ]
        self._derivedColumns = dict(
            color=self._getColor,
        )
        self._decoratedColumns = [
            'color',
        ]
        self._columnLabels = dict(
            key='Key',
            isVisible='Visibility',
            color='Color',
        )

        self._editableColumnValidators = dict(
            key=lambda _, oldKey, newKey: len(newKey) > 0 and (oldKey == newKey or newKey not in self._session.ROIs),  # don't allow setting one ROI to key of another
        )

        self._collection.sigItemsAboutToChange.connect(self._onCollectionAboutToChange, priority=-2)
        self._collection.sigItemsChanged.connect(self._onCollectionChanged, priority=2)

        super().__attrs_post_init__()

    def _getColor(self, roiKey: str) -> tuple[QtGui.QIcon | None, str]:
        roi = self._collection[roiKey]
        if not roi.isVisible:
            return None, ''

        # TODO: maybe cache this icon to avoid recreating it every time

        color = roi.color
        if color is None:
            color = roi.autoColor

        if color is None:
            return None, ''

        icon = QtGui.QIcon(qta.icon('mdi6.square', color=QtGui.QColor.fromRgbF(color[0], color[1], color[2])))

        return icon, ''

    def _onCollectionAboutToChange(self, keys: tp.Optional[list[K]], attrKeys: tp.Optional[list[str]] = None):
        if attrKeys is not None:
            if 'output' in attrKeys:
                # not reflected in table model, and nested change signals cause issues if not excluded here
                attrKeys.remove('output')

            if len(attrKeys) == 0:
                return

        return super()._onCollectionAboutToChange(keys, attrKeys)

    def _onCollectionChanged(self, keys: tp.Optional[list[K]], attrKeys: tp.Optional[list[str]] = None):
        if attrKeys is not None:
            if 'output' in attrKeys:
                # not reflected in table model, and nested change signals cause issues if not excluded here
                attrKeys.remove('output')

            if len(attrKeys) == 0:
                return

        return super()._onCollectionChanged(keys, attrKeys)
