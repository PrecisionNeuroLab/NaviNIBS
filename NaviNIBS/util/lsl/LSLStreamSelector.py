import asyncio
import attrs
import logging
import pylsl as lsl
import qtawesome as qta
from qtpy import QtWidgets, QtCore, QtGui
import typing as tp

from NaviNIBS.util.lsl.LSLStreamResolver import ThreadedLSLStreamResolver
from NaviNIBS.util.GUI.Icons import getIcon
from NaviNIBS.util.Signaler import Signal


logger = logging.getLogger(__name__)


@attrs.define
class LSLStreamSelector:
    _wdgt: QtWidgets.QWidget = attrs.field(factory=QtWidgets.QWidget)
    _streamKeys: list[str] = attrs.field(factory=list)
    _selectedStreamKey: tp.Optional[str] = None

    _resolver: ThreadedLSLStreamResolver = attrs.field(init=False)
    _comboBox: QtWidgets.QComboBox = attrs.field(init=False)

    _icon_available: QtGui.QIcon = attrs.field(factory=lambda: getIcon('mdi6.eye'))
    _icon_unavailable: QtGui.QIcon = attrs.field(factory=lambda: qta.icon('mdi6.eye-off', color='gray'))

    sigSelectedStreamKeyChanged: Signal = attrs.field(init=False, factory=lambda: Signal((str,)))
    sigSelectedStreamAvailabilityChanged: Signal = attrs.field(init=False, factory=Signal)  # (note: not emitted when key changes to a stream with different availability)

    def __attrs_post_init__(self):
        self._resolver = ThreadedLSLStreamResolver()
        self._resolver.sigStreamDetected.connect(self._onStreamDetected)
        self._resolver.sigStreamLost.connect(self._onStreamLost)

        self._wdgt.setLayout(QtWidgets.QVBoxLayout())

        self._comboBox = QtWidgets.QComboBox()
        self._comboBox.setEditable(True)
        self._comboBox.setInsertPolicy(self._comboBox.InsertPolicy.InsertAtBottom)
        self._comboBox.currentIndexChanged.connect(self._onComboBoxIndexChanged)
        self._wdgt.layout().addWidget(self._comboBox)

        if self._selectedStreamKey is not None and self._selectedStreamKey not in self._streamKeys:
            self._streamKeys.append(self._selectedStreamKey)

        for key in self._resolver.availableStreams:
            if key not in self._streamKeys:
                self._streamKeys.append(key)

        self._updateComboBox()

    @property
    def wdgt(self):
        return self._wdgt

    @property
    def selectedStreamKey(self):
        return self._selectedStreamKey

    @selectedStreamKey.setter
    def selectedStreamKey(self, key: tp.Optional[str]):
        if self._selectedStreamKey == key:
            return
        logger.info(f'Selected stream key changed: {key}')
        if key not in self._streamKeys:
            self._streamKeys.append(key)
        self._selectedStreamKey = key
        self._updateComboBox()

        self.sigSelectedStreamKeyChanged.emit(key)

    @property
    def selectedStreamIsAvailable(self):
        return self._selectedStreamKey is not None and self._selectedStreamKey in self._resolver.availableStreams

    @property
    def selectedStreamInfo(self) -> tp.Optional[lsl.StreamInfo]:
        return self._resolver.availableStreams.get(self._selectedStreamKey, None)

    def markStreamAsLost(self, streamKey: str):
        self._resolver.markStreamAsLost(streamKey=streamKey)

    def _updateComboBox(self):
        for key in self._streamKeys:
            isAvailable = key in self._resolver.availableStreams
            icon = self._icon_available if isAvailable else self._icon_unavailable
            index = self._comboBox.findText(key)
            if index == -1:
                # key not already in combo box
                self._comboBox.addItem(icon, key)
            else:
                # update available icon
                self._comboBox.setItemIcon(index, icon)
        # note: we don't handle case where a key is removed from streamKeys but is still in combo box (for now)

        if self._selectedStreamKey is None:
            if self._comboBox.currentIndex() != -1:
                self._comboBox.setCurrentIndex(-1)
        else:
            assert self._selectedStreamKey in self._streamKeys
            index = self._comboBox.findText(self._selectedStreamKey)
            if self._comboBox.currentIndex() != index:
                self._comboBox.setCurrentIndex(index)

    def _onStreamDetected(self, key: str, info: lsl.StreamInfo):
        if key not in self._streamKeys:
            self._streamKeys.append(key)
        self._updateComboBox()
        if key == self._selectedStreamKey:
            self.sigSelectedStreamAvailabilityChanged.emit()

    def _onStreamLost(self, key: str, info: lsl.StreamInfo):
        self._updateComboBox()
        if key == self._selectedStreamKey:
            self.sigSelectedStreamAvailabilityChanged.emit()

    def _onComboBoxIndexChanged(self, index: int):
        newStreamKey = self._comboBox.itemText(index)
        logger.debug(f'Combo box changed selected stream key from {self._selectedStreamKey} to {newStreamKey}')
        self.selectedStreamKey = newStreamKey
