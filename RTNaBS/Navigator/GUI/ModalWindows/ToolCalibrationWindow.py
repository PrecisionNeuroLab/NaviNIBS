import asyncio
import attrs
import logging
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp

from RTNaBS.Devices.ToolPositionsClient import ToolPositionsClient
from RTNaBS.Navigator.Model.Session import Session, Tool


logger = logging.getLogger(__name__)


@attrs.define
class ToolCalibrationWindow:
    """
    Base class for more specific coil or pointer calibration classes.
    """
    _parent: QtWidgets.QWidget
    _toolKeyToCalibrate: str
    _session: Session

    _positionsClient: ToolPositionsClient = attrs.field(init=False)

    _wdgt: QtWidgets.QDialog = attrs.field(init=False)

    def __attrs_post_init__(self):
        self._positionsClient = ToolPositionsClient()
        self._positionsClient.sigLatestPositionsChanged.connect(self._onLatestPositionsChanged)

        self._wdgt = QtWidgets.QDialog(self._parent)
        self._wdgt.setModal(True)

        self._wdgt.setWindowTitle('{} calibration'.format(self._toolKeyToCalibrate))

        self._wdgt.setWindowModality(QtGui.Qt.WindowModal)

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