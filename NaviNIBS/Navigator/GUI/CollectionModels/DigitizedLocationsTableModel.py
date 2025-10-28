import attrs
import logging
import typing as tp
import qtawesome as qta
from qtpy import QtGui

from NaviNIBS.Navigator.GUI.CollectionModels import CollectionTableModel, K, logger
from NaviNIBS.Navigator.Model.DigitizedLocations import DigitizedLocations, DigitizedLocation
from NaviNIBS.util import makeStrUnique


logger = logging.getLogger()


@attrs.define(slots=False)
class DigitizedLocationsTableModel(CollectionTableModel[str, DigitizedLocations, DigitizedLocation]):
    _collection: DigitizedLocations = attrs.field(init=False, repr=False)

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

        self._collection.sigItemsAboutToChange.connect(self._onCollectionAboutToChange, priority=-2)
        self._collection.sigItemsChanged.connect(self._onCollectionChanged, priority=2)

        self._addNewRowFromEditedPlaceholder = self.__addNewRowFromEditedPlaceholder

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

    def __addNewRowFromEditedPlaceholder(self, **kwargs) -> str | None:
        if 'key' not in kwargs:
            if len(kwargs) == 0:
                # nothing set, assume adding new row was cancelled
                return None
            kwargs['key'] = makeStrUnique('Loc', self._collection.keys())
        item = DigitizedLocation(**kwargs)
        if item.key in self._collection:
            # key already existed, reject
            return None
        self._collection.addItem(item)
        return item.key