import attrs
import logging
import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore
import pyqtgraph as pg
import typing as tp

from RTNaBS.util.GUI.QAppWithAsyncioLoop import RunnableAsApp


logger = logging.getLogger(__name__)


@attrs.define()
class NavigatorGUI(RunnableAsApp):
    _appName: str = 'RTNaBS Navigator GUI'

    _mainViewStackedWdgt: QtWidgets.QStackedWidget = attrs.field(init=False)
    _mainViewWdgts: tp.Dict[str, QtWidgets.QWidget] = attrs.field(init=False, factory=dict)
    _toolbarWdgt: QtWidgets.QToolBar = attrs.field(init=False)
    _toolbarBtnActions: tp.Dict[str, QtWidgets.QAction] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        logger.info('Initializing {}'.format(self.__class__.__name__))

        super().__attrs_post_init__()

        rootWdgt = QtWidgets.QWidget()
        rootWdgt.setLayout(QtWidgets.QVBoxLayout())
        self._win.setCentralWidget(rootWdgt)

        self._toolbarWdgt = QtWidgets.QToolBar()
        self._toolbarWdgt.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
        rootWdgt.layout().addWidget(self._toolbarWdgt)

        self._mainViewStackedWdgt = QtWidgets.QStackedWidget()
        rootWdgt.layout().addWidget(self._mainViewStackedWdgt)

        def createViewWdgt(key: str, icon: tp.Optional[QtGui.QIcon]=None):
            viewWdgt = QtWidgets.QWidget()
            self._mainViewWdgts[key] = viewWdgt
            self._mainViewStackedWdgt.addWidget(viewWdgt)
            self._toolbarBtnActions[key] = self._toolbarWdgt.addAction(key) if icon is None else self._toolbarWdgt.addAction(icon, key)
            self._toolbarBtnActions[key].setCheckable(True)
            self._toolbarBtnActions[key].triggered.connect(lambda checked=False, key=key: self._activateView(viewKey=key))

        createViewWdgt('Set MRI', icon=qta.icon('mdi6.image'))
        # TODO: set up MRI widget

        createViewWdgt('Set head model', icon=qta.icon('mdi6.head-cog-outline'))
        # TODO: set up head model widget

        createViewWdgt('Set fiducials', icon=qta.icon('mdi6.head-snowflake-outline'))
        # TODO: set up fiducials widget

        createViewWdgt('Set transforms', icon=qta.icon('mdi6.head-sync-outline'))
        # TODO: set up transforms widget

        createViewWdgt('Set targets', icon=qta.icon('mdi6.head-flash-outline'))
        # TODO: set up targets widget

        # set initial view widget visibility
        # TODO: default to MRI if new session, otherwise default to something else...
        self._activateView('Set MRI')

        if self._doRunAsApp:
            logger.debug('Showing window')
            self._win.show()

    def _activateView(self, viewKey: str):
        toolbarAction = self._toolbarBtnActions[viewKey]
        toolbarBtn = self._toolbarWdgt.widgetForAction(toolbarAction)

        prevViewWdgt = self._mainViewStackedWdgt.currentWidget()
        prevViewKeys = [key for key, val in self._mainViewWdgts.items() if val is prevViewWdgt]
        assert len(prevViewKeys) == 1
        prevViewKey = prevViewKeys[0]
        self._toolbarBtnActions[prevViewKey].setChecked(False)
        self._toolbarBtnActions[viewKey].setChecked(True)

        viewWdgt = self._mainViewWdgts[viewKey]
        self._mainViewStackedWdgt.setCurrentWidget(viewWdgt)

        logger.info('Switched to view "{}"'.format(viewKey))


if __name__ == '__main__':
    NavigatorGUI.createAndRun()




