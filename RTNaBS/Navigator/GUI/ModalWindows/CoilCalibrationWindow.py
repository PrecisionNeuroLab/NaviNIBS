import asyncio
import attrs
import logging
import numpy as np
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp

from RTNaBS.Navigator.GUI.ModalWindows.ToolCalibrationWindow import ToolCalibrationWindow
from RTNaBS.util.Transforms import invertTransform, concatenateTransforms


logger = logging.getLogger(__name__)


@attrs.define
class CoilCalibrationWindow(ToolCalibrationWindow):

    _calibrateBtn: QtWidgets.QPushButton = attrs.field(init=False)
    _pendingNewTransf: tp.Optional[np.ndarray] = attrs.field(init=False, default=None)

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

        self.sigFinished.connect(self._onDialogFinished)

    def calibrate(self):
        # TODO: spin-wait positions client to make sure we have most up-to-date information
        coilTrackerToCameraTransf = self._positionsClient.getLatestTransf(self._toolKeyToCalibrate)
        calibrationPlateTrackerToCameraTransf = self._positionsClient.getLatestTransf(self._session.tools.calibrationPlate.key)
        calibrationPlateToTrackerTransf = self._session.tools.calibrationPlate.toolToTrackerTransf

        self._pendingNewTransf = concatenateTransforms([
            calibrationPlateToTrackerTransf,
            calibrationPlateTrackerToCameraTransf,
            invertTransform(coilTrackerToCameraTransf)
        ])

        logger.info('Calibrated {} transform: {}'.format(self._toolKeyToCalibrate, self._pendingNewTransf))

    def _onDialogFinished(self, wasAccepted: bool):
        if self._pendingNewTransf is not None:
            self._session.tools[self._toolKeyToCalibrate].toolToTrackerTransf = self._pendingNewTransf
            logger.info('Saved {} calibration: {}'.format(self._toolKeyToCalibrate, self.toolToCalibrate.toolToTrackerTransf))

    def _onLatestPositionsChanged(self):
        super()._onLatestPositionsChanged()
        calibrationPlate = self._session.tools.calibrationPlate
        if self.toolToCalibrate.isActive and calibrationPlate is not None \
                and self._positionsClient.getLatestTransf(self._toolKeyToCalibrate, None) is not None \
                and self._positionsClient.getLatestTransf(calibrationPlate.key, None) is not None:
            self._calibrateBtn.setEnabled(True)
        else:
            self._calibrateBtn.setEnabled(False)