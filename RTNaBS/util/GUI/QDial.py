import attrs
import logging
from math import ceil, log10
from qtpy import QtWidgets, QtCore, QtGui

from RTNaBS.util.Signaler import Signal

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class _CustomQDial(QtWidgets.QDial):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return width


@attrs.define
class AngleDial:
    _value: float = 0.
    _resolution: float = 0.1
    _doInvert: bool = False
    """
    When doInvert is False, clockwise rotation from 0 increases the angle.
    """
    _offsetAngle: float = 0.
    """
    When offset is zero, value=0 is pointing down.
    When offset is +90 (and doInvert is False), value=0 is pointing left.
    """
    _centerAngle: float = 180.
    """
    When centerAngle is zero, values range from (-180, 180).
    When centerAngle is 180, values range from (0, 360).
    """

    _movingValue: float | None = attrs.field(init=False, default=None)
    _dialStartedMove: bool = attrs.field(init=False, default=False)
    _qdial: _CustomQDial = attrs.field(init=False)
    _numericField: QtWidgets.QDoubleSpinBox = attrs.field(init=False)
    _container: QtWidgets.QWidget = attrs.field(init=False, factory=QtWidgets.QWidget)

    _layout: QtWidgets.QBoxLayout | None = None

    sigValueMoved: Signal = attrs.field(init=False, factory=lambda: Signal((float,)))
    """
    Emitted frequentlny while dial is moved or while numeric field is edited.
    Connect to this if you want to respond immediately to any GUI changes.
    """
    sigValueChanged: Signal = attrs.field(init=False, factory=lambda: Signal((float,)))
    """
    Emitted less frequently while dial is moved, or after numeric field signals editingFinished.
    Connect to this if you want to only start responding after a change is finalized.
    """

    def __attrs_post_init__(self):
        if self._layout is None:
            self._layout = QtWidgets.QHBoxLayout()
            self._layout.setContentsMargins(0, 0, 0, 0)
        self._container.setLayout(self._layout)

        self._qdial = _CustomQDial()
        options = QtWidgets.QStyleOptionSlider()
        self._qdial.initStyleOption(options)

        self._qdial.setWrapping(True)
        self._qdial.setNotchesVisible(True)
        self._qdial.setRange(0, ceil(360/self._resolution) - 1)
        self._qdial.setTracking(False)
        self._qdial.valueChanged.connect(self._onDialValueChanged)
        self._qdial.sliderMoved.connect(self._onDialValueMoved)
        self._qdial.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self._layout.addWidget(self._qdial)

        self._numericField = QtWidgets.QDoubleSpinBox()
        self._numericField.setRange(-180 + self._centerAngle, 180 + self._centerAngle - self._resolution)
        self._numericField.setSingleStep(self._resolution)
        self._numericField.setDecimals(int(ceil(log10(1 / self._resolution))))
        self._numericField.setSuffix('°')
        self._numericField.valueChanged.connect(self._onNumericFieldValueChanged)
        self._numericField.editingFinished.connect(self._onNumericFieldEditingFinished)
        self._numericField.setWrapping(True)
        self._layout.addWidget(self._numericField)

        self._qdial.setValue(self._angleToQDialValue(self._value))
        self._numericField.setValue(self._value)

        self._layout.addStretch()

    @property
    def wdgt(self):
        return self._container

    def _qdialValueToAngle(self, val: int) -> float:
        logger.debug(f'qdial start value: {val}')
        val = val * self._resolution
        val -= self._offsetAngle
        if self._doInvert:
            val = 360 - val
        val = ((val + (self._centerAngle - 180)) % 360) + (self._centerAngle - 180)
        val = round(val, int(ceil(log10(1 / self._resolution))))
        return val

    def _angleToQDialValue(self, val: float) -> int:
        if self._doInvert:
            val = 360 - val
        val += self._offsetAngle
        val = val % 360
        val /= self._resolution
        return int(round(val))

    def _onDialValueMoved(self, newVal: int):
        newVal = self._qdialValueToAngle(newVal)
        if newVal == self._movingValue:
            return

        logger.debug(f'value moved: {newVal}')
        self._dialStartedMove = True
        self._movingValue = newVal
        self._numericField.setValue(newVal)
        self.sigValueMoved.emit(newVal)

    def _onNumericFieldValueChanged(self, newVal: float):
        if newVal == self._movingValue:
            return
        logger.debug(f'value moved: {newVal}')
        self._dialStartedMove = False
        self._movingValue = newVal
        self._qdial.setValue(self._angleToQDialValue(newVal))
        self.sigValueMoved.emit(newVal)

    def _onDialValueChanged(self, newVal: int):
        newVal = self._qdialValueToAngle(newVal)
        if self._movingValue == newVal and not self._dialStartedMove:
            return  # numeric field is being edited, don't finalize here
        self._movingValue = None
        if newVal == self._value:
            return

        logger.debug(f'value changed: {newVal}')
        self._value = newVal
        self._numericField.setValue(newVal)
        self.sigValueChanged.emit(newVal)

    def _onNumericFieldEditingFinished(self):
        newVal = self._numericField.value()
        self._movingValue = None
        if newVal == self._value:
            return
        logger.debug(f'value changed: {newVal}')
        self._value = newVal
        self._qdial.setValue(self._angleToQDialValue(newVal))
        self.sigValueChanged.emit(newVal)


if __name__ == '__main__':
    logger.setLevel(logging.DEBUG)
    app = QtWidgets.QApplication([])
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(QtWidgets.QWidget())
    layout = QtWidgets.QFormLayout()
    win.centralWidget().setLayout(layout)
    dial = AngleDial(value=45)
    layout.addRow('Default', dial.wdgt)

    dial2 = AngleDial(offsetAngle=180, doInvert=True, value=45)
    layout.addRow('Offset 180°, inverted', dial2.wdgt)

    dial3 = AngleDial(offsetAngle=90, centerAngle=0, value=45)
    layout.addRow('Offset 90°, center 0°', dial3.wdgt)

    win.show()
    app.exec_()
