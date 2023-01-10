import attrs
import qtawesome as qta
from qtpy import QtGui

from RTNaBS.Navigator.GUI.CollectionModels import CollectionTableModel, K, logger
from RTNaBS.Navigator.Model.SubjectRegistration import Fiducials, Fiducial


@attrs.define(slots=False)
class RegistrationFiducialsTableModel(CollectionTableModel[str, Fiducials, Fiducial]):
    _collection: Fiducials = attrs.field(init=False)

    _checkIcon_planned: QtGui.QIcon = attrs.field(factory=lambda: qta.icon('mdi6.checkbox-marked-circle', color='blue'))
    _checkIcon_sampled: QtGui.QIcon = attrs.field(factory=lambda: qta.icon('mdi6.checkbox-marked-circle', color='green'))
    _xIcon: QtGui.QIcon = attrs.field(factory=lambda: qta.icon('mdi6.close-circle-outline', color='red'))

    def __attrs_post_init__(self):
        self._collection = self._session.subjectRegistration.fiducials

        self._attrColumns = [
            'key'
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
            key='Fiducial',
            plannedIsSet='Planned',
            sampledIsSet='Sampled'
        )

        self._editableColumns.append('key')

        self._collection.sigItemsAboutToChange.connect(self._onCollectionAboutToChange)
        self._collection.sigItemsChanged.connect(self._onCollectionChanged)

        super().__attrs_post_init__()

    def _getPlannedIsSet(self, fidKey: str) -> tuple[QtGui.QIcon, str]:
        if self._collection[fidKey].plannedCoord is None:
            return self._xIcon, ''
        else:
            return self._checkIcon_planned, ''

    def _getSampledIsSet(self, fidKey: str) -> tuple[QtGui.QIcon, str]:
        if self._collection[fidKey].sampledCoord is None:
            return self._xIcon, ''
        numPtsSampled = self._collection[fidKey].sampledCoords.shape[0]
        if numPtsSampled == 1:
            return self._checkIcon_sampled, ''
        else:
            return self._checkIcon_sampled, f'{numPtsSampled}'  # show an indicator of how many points have been sampled


# TODO: implement PlanningFiducialsTableModel for planning fiducials tab (not showing sampled fiducials at all)

@attrs.define(slots=False)
class PlanningFiducialsTableModel(CollectionTableModel[str, Fiducials, Fiducial]):
    _collection: Fiducials = attrs.field(init=False)

    def __attrs_post_init__(self):
        self._collection = self._session.subjectRegistration.fiducials

        self._attrColumns = [
            'key'
        ]

        self._derivedColumns = dict(
            plannedCoord=self._getPlannedCoordText,
        )

        self._columns = self._attrColumns + list(self._derivedColumns.keys())

        self._columnLabels = dict(
            key='Label',
            plannedCoord='XYZ'
        )

        self._editableColumns.append('key')
        # TODO: make XYZ coord field editable too
        # (just need to implement support for editable derived fields in CollectionTableModel)

        self._collection.sigItemsAboutToChange.connect(self._onCollectionAboutToChange)
        self._collection.sigItemsChanged.connect(self._onCollectionChanged)

        super().__attrs_post_init__()


    def _getPlannedCoordText(self, fidKey: str) -> str:
        coord = self._session.subjectRegistration.fiducials[fidKey].plannedCoord
        if coord is None:
            return ''
        else:
            return '{:.1f}, {:.1f}, {:.1f}'.format(*coord)
