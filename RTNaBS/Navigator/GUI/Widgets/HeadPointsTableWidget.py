import attrs
import logging
import numpy as np
import qtawesome as qta
from qtpy import QtWidgets, QtCore, QtGui
import typing as tp

from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.Navigator.Model.SubjectRegistration import SubjectRegistration
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.Transforms import applyTransform

logger = logging.getLogger(__name__)


@attrs.define
class HeadPointsTableWidget:
    """
    Widget to dispay list of head points in table view
    """
    _session: tp.Optional[Session] = None

    _tableWdgt: QtWidgets.QTableWidget = attrs.field(init=False)
    _tableItems: list[tuple[
        QtWidgets.QTableWidgetItem,
        QtWidgets.QTableWidgetItem,
        QtWidgets.QTableWidgetItem]] = attrs.field(init=False, factory=list)
    _updateInProgress: bool = attrs.field(init=False, default=False)

    sigCurrentPointChanged: Signal = attrs.field(init=False, factory=lambda: Signal((int,)))
    """
    Includes index of newly selected point.
    
    Note: this is emitted when the selection changes, NOT when a property of the currently selected point changes.
    
    Note: this is Qt's "current" item. If multiple samples are selected, the "current" point will just be the last point added to the selection
    """
    sigSelectedPointsChanged: Signal = attrs.field(init=False, factory=lambda: Signal((list[int],)))


    def __attrs_post_init__(self):
        self._tableWdgt = QtWidgets.QTableWidget(0, 3)
        self._tableWdgt.setHorizontalHeaderLabels(['#', 'XYZ', 'Dist from skin'])
        self._tableWdgt.setSelectionBehavior(self._tableWdgt.SelectRows)
        self._tableWdgt.setSelectionMode(self._tableWdgt.ExtendedSelection)
        self._tableWdgt.currentItemChanged.connect(self._onTableCurrentItemChanged)
        self._tableWdgt.itemSelectionChanged.connect(self._onTableItemSelectionChanged)

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newSes: tp.Optional[Session]):
        if self._session is newSes:
            return
        if self._session is not None:
            raise NotImplementedError()  # TODO: disconnect from previous signals
        self._session = newSes
        self._session.subjectRegistration.sigSampledHeadPointsChanged.connect(self._onHeadPointsChanged)
        self.session.subjectRegistration.sigTrackerToMRITransfChanged.connect(self._onHeadPointsChanged)
        self._onHeadPointsChanged()

    @property
    def wdgt(self):
        return self._tableWdgt

    @property
    def currentHeadPointIndex(self) -> tp.Optional[int]:
        curItem = self._tableWdgt.currentItem()
        if curItem is not None:
            return curItem.row()
        else:
            return None

    @currentHeadPointIndex.setter
    def currentHeadPointIndex(self, index: tp.Optional[int]):
        if self.currentHeadPointIndex == index:
            return

        rowItems = self._tableItems[index]
        self._tableWdgt.setCurrentItem(rowItems[0])

    @property
    def selectedHeadPointIndices(self) -> tp.List[int]:
        selItems = self._tableWdgt.selectedItems()
        selKeys = [item.row() for item in selItems]
        selKeys = list(dict.fromkeys(selKeys))  # remove duplicates, keep order stable
        return selKeys

    # TODO: continue implementing here

    def _onHeadPointsChanged(self, changedIndices: tp.Optional[list[str]] = None):
        logger.debug('Head points changed. Updating table view')

        headPts = self.session.subjectRegistration.sampledHeadPoints
        if headPts is None:
            headPts = []

        if changedIndices is None:
            changedIndices = range(len(headPts))

        # TODO: do incremental updates rather than entirely clearing and rebuilding
        assert not self._updateInProgress
        self._updateInProgress = True

        prevSelectedRow = self._tableWdgt.currentItem().row() if self._tableWdgt.currentItem() is not None else None
        self._tableWdgt.clearContents()
        self._tableItems = []
        self._tableWdgt.setRowCount(len(headPts))
        for iP, pt in enumerate(headPts):
            # index column
            item = QtWidgets.QTableWidgetItem()
            item.setText(f'{iP}')
            self._tableWdgt.setItem(iP, 0, item)
            colItem = item

            # coord column
            item = QtWidgets.QTableWidgetItem()
            x, y, z = pt
            item.setText(f'({x:.1f},{y:.1f},{z:.1f} mm)')
            self._tableWdgt.setItem(iP, 1, item)
            coordItem = item

            # dist from scalp column
            # TODO: subscribe to changes in subject registration trackerToMRITransf to update these columns when that changes
            item = QtWidgets.QTableWidgetItem()
            pt_MRISpace = applyTransform(self.session.subjectRegistration.trackerToMRITransf, pt)
            closestPtIndex = self.session.headModel.skinSurf.find_closest_point(pt_MRISpace)
            closestPt = self.session.headModel.skinSurf.points[closestPtIndex, :]
            dist = np.linalg.norm(closestPt - pt_MRISpace)
            item.setText(f'{dist:.1f} mm')
            self._tableWdgt.setItem(iP, 2, item)
            distItem = item

            self._tableItems.append((colItem, coordItem, distItem))

        self._updateInProgress = False

        if prevSelectedRow is not None and len(headPts) > prevSelectedRow:
            self._tableWdgt.setCurrentIndex(prevSelectedRow)

    def _onTableCurrentItemChanged(self, current: QtWidgets.QTableWidgetItem):
        self.sigCurrentPointChanged.emit(self.currentHeadPointIndex)

    def _onTableItemSelectionChanged(self):
        if self._updateInProgress:
            return  # assume selection will be updated as needed after update
        logger.debug(f'Head points selection changed: {self.selectedHeadPointIndices}')
        self.sigSelectedPointsChanged.emit(self.selectedHeadPointIndices)
