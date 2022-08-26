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
    
    Note: this is emitted when the selection changes (i.e. a different target is selected), NOT when a property of the
    currently selected target changes.
    """

    def __attrs_post_init__(self):
        self._treeWdgt = QtWidgets.QTreeWidget()
        self._treeWdgt.setHeaderLabels(['Target', 'Info'])
        self._treeWdgt.currentItemChanged.connect(self._onTreeCurrentItemChanged)

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
        def getRootItem(treeItem: QtWidgets.QTreeWidgetItem) -> QtWidgets.QTreeWidgetItem:
            if treeItem.parent() is None:
                return treeItem
            else:
                return getRootItem(treeItem.parent())

        curItem = self._treeWdgt.currentItem()
        if curItem is not None:
            return getRootItem(curItem).text(0)
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

    def _onTargetVisibleBtnClicked(self, key: str):
        target = self._session.targets[key]
        target.isVisible = not target.isVisible

    def _onTargetsChanged(self, changedTargetKeys: tp.Optional[tp.List[str]] = None, changedTargetAttrs: tp.Optional[tp.List[str]] = None):
        logger.debug('Targets changed. Updating tree view')

        if changedTargetAttrs == ['isVisible']:
            # only visibility changed

            if changedTargetKeys is None:
                changedTargetKeys = self.session.targets.keys()

            for targetKey in changedTargetKeys:
                btn = self._treeWdgt.itemWidget(self._treeItems[targetKey], 1)
                assert isinstance(btn, QtWidgets.QPushButton)
                target = self._session.targets[targetKey]
                if target.isVisible:
                    btn.setIcon(qta.icon('mdi6.eye'))
                else:
                    btn.setIcon(qta.icon('mdi6.eye-off-outline'))

        else:
            # assume anything/everything changed, clear target and start over

            # update tree view
            # TODO: do incremental tree updates rather than entirely clearing and rebuilding
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

    def _onTreeCurrentItemChanged(self, current: QtWidgets.QTreeWidgetItem, previous: QtWidgets.QTreeWidgetItem):
        self.sigCurrentTargetChanged.emit(self.currentTargetKey)
