import attrs
import logging
import numpy as np
import qtawesome as qta
from qtpy import QtWidgets, QtCore, QtGui
import typing as tp

from RTNaBS.Navigator.Model.Session import Session, Target
from RTNaBS.util.Signaler import Signal


logger = logging.getLogger(__name__)


@attrs.define
class TargetsTreeWidget:
    """
    Widget to display list of targets in a tree view
    """
    _session: tp.Optional[Session] = None
    _treeWdgt: QtWidgets.QTreeWidget = attrs.field(init=False)
    _treeItems: tp.Dict[str, QtWidgets.QTreeWidgetItem] = attrs.field(init=False, factory=dict)

    sigCurrentTargetChanged: Signal = attrs.field(init=False, factory=lambda: Signal((str,)))
    """
    Includes key of newly selected target.
    
    Note: this is emitted when the selection changes (i.e. a different target is selected), NOT when a property of the currently selected target changes.
    Note: this is Qt's "current" item. If multiple targets are selected, the "current" target will just be the last target added to the selection
    """

    _updateInProgress: bool = attrs.field(init=False, default=False)

    def __attrs_post_init__(self):
        self._treeWdgt = QtWidgets.QTreeWidget()
        self._treeWdgt.setHeaderLabels(['Target', 'Info'])
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
        self._session.targets.sigTargetsChanged.connect(self._onTargetsChanged)
        self._onTargetsChanged()

    @property
    def wdgt(self):
        return self._treeWdgt

    @property
    def currentTargetKey(self) -> tp.Optional[str]:
        curItem = self._treeWdgt.currentItem()
        if curItem is not None:
            return _getRootItem(curItem).text(0)
        elif self.session is None:
            return None
        elif len(self.session.targets) > 0:
            return list(self.session.targets.values())[0].key
        else:
            return None

    @currentTargetKey.setter
    def currentTargetKey(self, targetKey: str):
        if self.currentTargetKey == targetKey:
            return
        logger.debug(f'Current target key changed to {targetKey}')
        item = self._treeItems[targetKey]
        self._treeWdgt.setCurrentItem(item)

    @property
    def selectedTargetKeys(self) -> tp.List[str]:
        selItems = self._treeWdgt.selectedItems()
        selKeys = [_getRootItem(item).text(0) for item in selItems]
        selKeys = list(dict.fromkeys(selKeys))  # remove duplicates, keep order stable
        return selKeys

    def _onTargetVisibleBtnClicked(self, key: str):
        target = self._session.targets[key]
        target.isVisible = not target.isVisible

    def _onTargetsChanged(self, changedTargetKeys: tp.Optional[tp.List[str]] = None, changedTargetAttrs: tp.Optional[tp.List[str]] = None):
        logger.debug('Targets changed. Updating tree view')

        if changedTargetKeys is None:
            changedTargetKeys = self.session.targets.keys()

        if changedTargetAttrs == ['isVisible']:
            # only visibility changed

            for targetKey in changedTargetKeys:
                btn = self._treeWdgt.itemWidget(self._treeItems[targetKey], 1)
                assert isinstance(btn, QtWidgets.QPushButton)
                target = self._session.targets[targetKey]
                if target.isVisible:
                    btn.setIcon(qta.icon('mdi6.eye'))
                else:
                    btn.setIcon(qta.icon('mdi6.eye-off-outline'))

        elif changedTargetAttrs == ['isSelected']:
            # only selection changed
            logger.debug(f'Updating targets selection for keys {changedTargetKeys}')
            selection = QtCore.QItemSelection()
            selection.merge(self._treeWdgt.selectionModel().selection(), QtCore.QItemSelectionModel.Select)

            for key in changedTargetKeys:
                target = self.session.targets[key]
                targetIndex = self._treeWdgt.indexFromItem(self._treeItems[key])
                if target.isSelected:
                    cmd = QtCore.QItemSelectionModel.Select
                else:
                    cmd = QtCore.QItemSelectionModel.Deselect
                selection.merge(QtCore.QItemSelection(targetIndex, targetIndex), cmd)

            self._treeWdgt.selectionModel().select(selection, QtCore.QItemSelectionModel.Select)
            logger.debug('Done updating targets selection')

        else:
            # assume anything/everything changed, clear target and start over

            # update tree view
            # TODO: do incremental tree updates rather than entirely clearing and rebuilding
            assert not self._updateInProgress
            self._updateInProgress = True
            prevSelectedItem = self._treeWdgt.currentItem()
            self._treeWdgt.clear()
            self._treeItems = {}
            for iT, (key, target) in enumerate(self.session.targets.items()):
                targetItem = QtWidgets.QTreeWidgetItem([key, ''])
                self._treeItems[key] = targetItem
                for field in ('targetCoord', 'entryCoord', 'angle', 'depthOffset', 'coilToMRITransf'):
                    with np.printoptions(precision=2):
                        fieldItem = QtWidgets.QTreeWidgetItem(targetItem, [field, '{}'.format(getattr(target, field))])
            self._treeWdgt.insertTopLevelItems(0, list(self._treeItems.values()))
            for iT, (key, target) in enumerate(self.session.targets.items()):
                if target.isVisible:
                    initialIconKey = 'mdi6.eye'
                else:
                    initialIconKey = 'mdi6.eye-off-outline'
                btn = QtWidgets.QPushButton(icon=qta.icon(initialIconKey), text='')
                btn.setFlat(True)
                btn.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Maximum)
                self._treeWdgt.setItemWidget(self._treeItems[key], 1, btn)
                btn.clicked.connect(lambda checked=False, key=key: self._onTargetVisibleBtnClicked(key=key))
            self._updateInProgress = False

            self._onTargetsChanged(None, changedTargetAttrs=['isSelected'])

    def _onTreeCurrentItemChanged(self, current: QtWidgets.QTreeWidgetItem, previous: QtWidgets.QTreeWidgetItem):
        self.sigCurrentTargetChanged.emit(self.currentTargetKey)

    def _onTreeItemSelectionChanged(self):
        if self._updateInProgress:
            return  # assume selection will be updated as needed after update
        logger.debug(f'Targets selection changed: {self.selectedTargetKeys}')
        self.session.targets.setWhichTargetsSelected(self.selectedTargetKeys)


def _getRootItem(treeItem: QtWidgets.QTreeWidgetItem) -> QtWidgets.QTreeWidgetItem:
    if treeItem.parent() is None:
        return treeItem
    else:
        return _getRootItem(treeItem.parent())