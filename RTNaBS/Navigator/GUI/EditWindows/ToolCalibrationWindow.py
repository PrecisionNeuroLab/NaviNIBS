import asyncio
import attrs
import logging
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp

from NaviNIBS.Devices.ToolPositionsClient import ToolPositionsClient
from NaviNIBS.Navigator.Model.Session import Session, Tool
from NaviNIBS.util.Signaler import Signal

logger = logging.getLogger(__name__)


@attrs.define
class ToolCalibrationWindow:
    """
    Base class for more specific coil or pointer calibration classes.
    """
    _parent: QtWidgets.QWidget
    _toolKeyToCalibrate: str
    _session: Session = attrs.field(repr=False)

    _positionsClient: ToolPositionsClient = attrs.field(init=False)

    _wdgt: QtWidgets.QDialog = attrs.field(init=False)

    sigFinished: Signal = attrs.field(init=False, factory=lambda: Signal((bool,)))  # includes False if calibration was cancelled, else True

    def __attrs_post_init__(self):
        self._positionsClient = ToolPositionsClient()
        self._positionsClient.sigLatestPositionsChanged.connect(self._onLatestPositionsChanged)

        self._wdgt = QtWidgets.QDialog(self._parent)
        self._wdgt.setModal(True)

        self._wdgt.setWindowTitle('{} calibration'.format(self._toolKeyToCalibrate))

        self._wdgt.setWindowModality(QtGui.Qt.WindowModal)

        self._wdgt.finished.connect(self._onDlgFinished)

    def _onDlgFinished(self, result: int):
        self._positionsClient.stopReceivingPositions()
        self.sigFinished.emit(result == QtWidgets.QDialog.Accepted)

    @property
    def toolToCalibrate(self) -> Tool:
        return self._session.tools[self._toolKeyToCalibrate]

    def show(self):
        self._wdgt.show()

    @property
    def wdgt(self):
        return self._wdgt

    @property
    def session(self):
        return self._session

    def _onLatestPositionsChanged(self):
        pass