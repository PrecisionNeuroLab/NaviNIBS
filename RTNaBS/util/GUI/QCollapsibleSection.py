import attrs
from qtpy import QtWidgets, QtCore, QtGui


@attrs.define
class QCollapsibleSection:
    _title: str
    _innerWdgt: QtWidgets.QWidget = attrs.field(factory=QtWidgets.QWidget)
    _animationDuration: int = 300  # in ms
    _doStartCollapsed: bool = False

    _button: QtWidgets.QToolButton = attrs.field(init=False, factory=QtWidgets.QToolButton)
    _outerWdgt: QtWidgets.QWidget = attrs.field(init=False, factory=QtWidgets.QWidget)
    _animation: QtCore.QPropertyAnimation = attrs.field(init=False)

    def __attrs_post_init__(self):
        layout = QtWidgets.QVBoxLayout()
        self._outerWdgt.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        self._button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self._button.setIconSize(QtCore.QSize(8, 8))
        self._button.setText(" " + self._title)
        self._button.setCheckable(True)
        self._button.setStyleSheet("""
        QToolButton {
            background: none;
            border: none
            
        }
        QToolButton:pressed {
            background: none;
            border: none
        }   
        """)
        self._button.setChecked(not self._doStartCollapsed)
        if self._doStartCollapsed:
            self._innerWdgt.setMaximumHeight(0)

        self._button.setArrowType(QtCore.Qt.DownArrow if self._button.isChecked() else QtCore.Qt.RightArrow)

        self._button.toggled.connect(self._onButtonToggled)

        layout.addWidget(self._button)
        layout.addWidget(self._innerWdgt)

        animation = QtCore.QPropertyAnimation(self._innerWdgt, b"maximumHeight")
        animation.setStartValue(0)
        animation.setEasingCurve(QtCore.QEasingCurve.InOutCubic)
        animation.setDuration(self._animationDuration)  # TODO: change to shorter duration by default
        animation.setEndValue(self._innerWdgt.geometry().height() + 10)
        self._animation = animation

    @property
    def outerWdgt(self):
        return self._outerWdgt

    @property
    def innerWdgt(self):
        return self._innerWdgt

    def setLayout(self, layout: QtWidgets.QLayout):
        """
        Set layout of innerWdgt
        """
        self._innerWdgt.setLayout(layout)

    def _onButtonToggled(self, checked: bool):
        self._button.setArrowType(QtCore.Qt.DownArrow if checked else QtCore.Qt.RightArrow)
        if checked:
            self.showContent()
        else:
            self.hideContent()

    def showContent(self):
        self._animation.setDirection(QtCore.QAbstractAnimation.Forward)
        self._animation.setEndValue(self._innerWdgt.sizeHint().height() + 10)
        self._animation.start()

    def hideContent(self):
        self._animation.setDirection(QtCore.QAbstractAnimation.Backward)
        self._animation.setEndValue(self._innerWdgt.sizeHint().height() + 10)
        self._animation.start()

