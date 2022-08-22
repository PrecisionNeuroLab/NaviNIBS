import attrs
import logging
import numpy as np
import qtawesome as qta
from qtpy import QtWidgets, QtCore, QtGui
import typing as tp

from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.Navigator.Model.Samples import Samples, Sample, Timestamp
from RTNaBS.util.Signaler import Signal

logger = logging.getLogger(__name__)


@attrs.define
class SamplesTreeWidget:
    """
    Widget to display list of samples in a tree view
    """
    _session: tp.Optional[Session] = None
    _treeWdgt: QtWidgets.QTreeWidget = attrs.field(init=False)
    _treeItems: tp.Dict[str, QtWidgets.QTreeWidgetItem] = attrs.field(init=False, factory=dict)

    sigCurrentSampleChanged: Signal = attrs.field(init=False, factory=lambda: Signal((str,)))
    """
    Includes key of newly selected sample.

    Note: this is emitted when the selection changes (i.e. a different sample is selected), NOT when a property of the
    currently selected sample changes.
    
    Note: this is Qt's "current" item. If multiple samples are selected, the "current" sample will just be the last sample
    added to the selection
    """

    _updateInProgress: bool = attrs.field(init=False, default=False)

    def __attrs_post_init__(self):
        self._treeWdgt = QtWidgets.QTreeWidget()
        self._treeWdgt.setHeaderLabels(['Sample', 'Info'])
        self._treeWdgt.setSelectionBehavior(self._treeWdgt.SelectRows)  # TODO: decide whether we want this set or not
        self._treeWdgt.setSelectionMode(self._treeWdgt.ExtendedSelection)
        self._treeWdgt.currentItemChanged.connect(self._onTreeCurrentItemChanged)
        self._treeWdgt.itemSelectionChanged.connect(self._onTreeItemSelectionChanged)

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
        self._session.samples.sigSamplesChanged.connect(self._onSamplesChanged)
        self._onSamplesChanged()

    @property
    def wdgt(self):
        return self._treeWdgt

    @property
    def currentSampleKey(self) -> tp.Optional[str]:
        curItem = self._treeWdgt.currentItem()
        if curItem is not None:
            return _getRootItem(curItem).text(0)
        elif self.session is None:
            return None
        elif len(self.session.samples) > 0:
            return list(self.session.samples.values())[0].key
        else:
            return None

    @currentSampleKey.setter
    def currentSampleKey(self, sampleKey: str):
        if self.currentSampleKey == sampleKey:
            return

        item = self._treeItems[sampleKey]
        self._treeWdgt.setCurrentItem(item)

    @property
    def selectedSampleKeys(self) -> tp.List[str]:
        selItems = self._treeWdgt.selectedItems()
        selKeys = [_getRootItem(item).text(0) for item in selItems]
        selKeys = list(dict.fromkeys(selKeys))  # remove duplicates, keep order stable
        return selKeys

    def _onSampleVisibleBtnClicked(self, key: str):
        sample = self._session.samples[key]
        sample.isVisible = not sample.isVisible

    def _onSamplesChanged(self, changedSampleKeys: tp.Optional[tp.List[str]] = None,
                          changedSampleAttrs: tp.Optional[tp.List[str]] = None):
        logger.debug('Samples changed. Updating tree view')

        if changedSampleKeys is None:
            changedSampleKeys = self.session.samples.keys()

        if changedSampleAttrs == ['isVisible']:
            # only visibility changed

            for sampleKey in changedSampleKeys:
                btn = self._treeWdgt.itemWidget(self._treeItems[sampleKey], 1)
                assert isinstance(btn, QtWidgets.QPushButton)
                sample = self._session.samples[sampleKey]
                if sample.isVisible:
                    btn.setIcon(qta.icon('mdi6.eye'))
                else:
                    btn.setIcon(qta.icon('mdi6.eye-off-outline'))

        elif changedSampleAttrs == ['isSelected']:
            # only selection changed
            logger.debug('Updating samples selection')
            selection = QtCore.QItemSelection()
            selection.merge(self._treeWdgt.selectionModel().selection(), QtCore.QItemSelectionModel.Select)

            for key in changedSampleKeys:
                sample = self.session.samples[key]
                sampleIndex = self._treeWdgt.indexFromItem(self._treeItems[key])
                if sample.isSelected:
                    cmd = QtCore.QItemSelectionModel.Select
                else:
                    cmd = QtCore.QItemSelectionModel.Deselect
                selection.merge(QtCore.QItemSelection(sampleIndex, sampleIndex), cmd)

            self._treeWdgt.selectionModel().select(selection, QtCore.QItemSelectionModel.Select)

        else:
            # assume anything/everything changed, clear sample and start over

            # update tree view
            # TODO: do incremental tree updates rather than entirely clearing and rebuilding
            assert not self._updateInProgress
            self._updateInProgress = True
            prevSelectedItem = self._treeWdgt.currentItem()
            self._treeWdgt.clear()
            self._treeItems = {}
            for iT, (key, sample) in enumerate(self.session.samples.items()):
                sampleItem = QtWidgets.QTreeWidgetItem([key, ''])
                self._treeItems[key] = sampleItem
                for field in ('timestamp', 'targetKey', 'coilKey', 'coilToMRITransf'):
                    with np.printoptions(precision=2):
                        fieldItem = QtWidgets.QTreeWidgetItem(sampleItem, [field, '{}'.format(getattr(sample, field))])
            self._treeWdgt.insertTopLevelItems(0, list(self._treeItems.values()))
            for iT, (key, sample) in enumerate(self.session.samples.items()):
                if sample.isVisible:
                    initialIconKey = 'mdi6.eye'
                else:
                    initialIconKey = 'mdi6.eye-off-outline'
                btn = QtWidgets.QPushButton(icon=qta.icon(initialIconKey), text='')
                btn.setFlat(True)
                btn.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Maximum)
                self._treeWdgt.setItemWidget(self._treeItems[key], 1, btn)
                btn.clicked.connect(lambda checked=False, key=key: self._onSampleVisibleBtnClicked(key=key))
            self._updateInProgress = False

            self._onSamplesChanged(None, changedSampleAttrs=['isSelected'])

    def _onTreeCurrentItemChanged(self, current: QtWidgets.QTreeWidgetItem, previous: QtWidgets.QTreeWidgetItem):
        self.sigCurrentSampleChanged.emit(self.currentSampleKey)

    def _onTreeItemSelectionChanged(self):
        if self._updateInProgress:
            return  # assume selection will be updated as needed after update
        logger.debug('Samples selection changed')
        self.session.samples.setWhichSamplesSelected(self.selectedSampleKeys)

def _getRootItem(treeItem: QtWidgets.QTreeWidgetItem) -> QtWidgets.QTreeWidgetItem:
    if treeItem.parent() is None:
        return treeItem
    else:
        return _getRootItem(treeItem.parent())