import asyncio
import attrs
import logging
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp

from RTNaBS.Navigator.GUI.ModalWindows.ToolCalibrationWindow import ToolCalibrationWindow


logger = logging.getLogger(__name__)


@attrs.define
class CoilCalibrationWindow(ToolCalibrationWindow):

    _calibrateBtn: QtWidgets.QPushButton = attrs.field(init=False)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._wdgt.setLayout(QtWidgets.QVBoxLayout())

        btnContainer = QtWidgets.QWidget()
        btnContainer.setLayout(QtWidgets.QHBoxLayout())
        self._wdgt.layout().addWidget(btnContainer)

        btn = QtWidgets.QPushButton('Calibrate coil')
        btn.clicked.connect(lambda checked: self.calibrate())
        btn.setEnabled(False)
        btnContainer.layout().addWidget(btn)
        self._calibrateBtn = btn

        btnContainer.layout().addStretch()

        self._wdgt.layout().addStretch()

        # TODO: add visualization of alignment after calibration

        # TODO: add error metrics (distance from previous calibration) for checking if need to recalibrate

    def calibrate(self):
        # TODO: spin-wait positions client to make sure we have most up-to-date information
        coilToolToCameraTransf = self._positionsClient.getLatestTransf(self._toolKeyToCalibrate)
        calibrationPlateToCameraTransf = self._positionsClient.getLatestTransf(self._session.tools.calibrationPlate.key)



        raise NotImplementedError()  # TODO

        self.toolToCalibrate.toolToTrackerTransf = 'todo'



    def _onLatestPositionsChanged(self):
        calibrationPlate = self._session.tools.calibrationPlate
        if self.toolToCalibrate.isActive and calibrationPlate is not None \
                and self._positionsClient.getLatestTransf(self._toolKeyToCalibrate, None) is not None \
                and self._positionsClient.getLatestTransf(calibrationPlate.key, None) is not None:
            self._calibrateBtn.setEnabled(True)
        else:
            self._calibrateBtn.setEnabled(False)