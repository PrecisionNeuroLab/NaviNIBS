import attrs
import logging
import typing as tp
import qtawesome as qta
from qtpy import QtGui

from RTNaBS.Navigator.GUI.CollectionModels import CollectionTableModel, K, logger
from RTNaBS.Navigator.Model.DigitizedLocations import DigitizedLocations, DigitizedLocation


logger = logging.getLogger()


@attrs.define(slots=False)
class DigitizedLocationsTableModel(CollectionTableModel[str, DigitizedLocations, DigitizedLocation]):
    _collection: DigitizedLocations = attrs.field(init=False)

    _hasPlaceholderNewRow: bool = True
    _placeholderNewRowDefaults: dict[str, tp.Any] = attrs.field(factory=lambda: dict(key='<NewEntry>'))

    _checkIcon_planned: QtGui.QIcon = attrs.field(factory=lambda: qta.icon('mdi6.checkbox-marked-circle', color='blue'))
    _checkIcon_sampled: QtGui.QIcon = attrs.field(factory=lambda: qta.icon('mdi6.checkbox-marked-circle', color='green'))
    _xIcon: QtGui.QIcon = attrs.field(factory=lambda: qta.icon('mdi6.close-circle-outline', color='red'))

    def __attrs_post_init__(self):
        self._collection = self._session.digitizedLocations

        self._attrColumns = [
            'key',
            'type',
        ]

        self._derivedColumns = dict(
            plannedIsSet=self._getPlannedIsSet,
            sampledIsSet=self._getSampledIsSet
        )

        self._columns = self._attrColumns + list(self._derivedColumns.keys())

        self._decoratedColumns = [
            'plannedIsSet',
            'sampledIsSet'
        ]

        self._columnLabels = dict(
            plannedIsSet='Planned',
            sampledIsSet='Sampled'
        )

        self._editableColumns.extend(['key', 'type'])

        self._collection.sigItemsAboutToChange.connect(self._onCollectionAboutToChange)
        self._collection.sigItemsChanged.connect(self._onCollectionChanged)

        super().__attrs_post_init__()

    def _getPlannedIsSet(self, key: tp.Optional[str]) -> tuple[tp.Optional[QtGui.QIcon], str]:
        if key is None:
            return None, ''
        if self._collection[key].plannedCoord is None:
            return self._xIcon, ''
        else:
            return self._checkIcon_planned, ''

    def _getSampledIsSet(self, key: tp.Optional[str]) -> tuple[tp.Optional[QtGui.QIcon], str]:
        if key is None:
            return None, ''
        if self._collection[key].sampledCoord is None:
            return self._xIcon, ''
        else:
            return self._checkIcon_sampled, ''
