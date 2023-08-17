import attrs
import logging
from math import ceil
import qtawesome as qta
from qtpy import QtWidgets, QtCore, QtGui
import typing as tp

from RTNaBS.Devices.ToolPositionsClient import ToolPositionsClient
from RTNaBS.Navigator.Model.Session import Session, Tool
from RTNaBS.util.GUI.IconWidget import IconWidget

logger = logging.getLogger(__name__)


ToolStatusWidget = IconWidget


@attrs.define
class TrackingStatusWidget:
    _session: tp.Optional[Session] = attrs.field(default=None, repr=False)
    _positionsClient: ToolPositionsClient = attrs.field(init=False)
    _wdgt: QtWidgets.QWidget = attrs.field(default=None)
    _hideInactiveTools: bool = True
    _hideToolTypes: tp.List[tp.Type[Tool]] = attrs.field(factory=list)
    _numColumns: int = 2
    _columnContainers: tp.List[QtWidgets.QWidget] = attrs.field(init=False, factory=list)

    _toolWdgts: tp.Dict[str, ToolStatusWidget] = attrs.field(init=False, factory=dict)

    _prevHadTransf: dict[str, bool] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        if self._wdgt is None:
            self._wdgt = QtWidgets.QGroupBox('Tools tracking status')

        self._wdgt.setLayout(QtWidgets.QHBoxLayout())
        self._wdgt.layout().setContentsMargins(0, 0, 0, 0)
        self._wdgt.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)

        for iCol in range(self._numColumns):
            wdgt = QtWidgets.QWidget()
            wdgt.setLayout(QtWidgets.QFormLayout())
            self._columnContainers.append(wdgt)
            self._wdgt.layout().addWidget(wdgt)
        self._wdgt.layout().addStretch()

        if self._session is not None:
            self._initialize()

    @property
    def wdgt(self):
        return self._wdgt

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newSession: tp.Optional[Session]):
        if self._session is newSession:
            return
        self._session = newSession
        self._initialize()

    def _initialize(self):
        if self._session is not None:
            self._positionsClient = ToolPositionsClient()
            self._positionsClient.sigLatestPositionsChanged.connect(self._onLatestPositionsChanged)
            # TODO: connect to any necessary signals from session
            self._session.tools.sigItemsChanged.connect(lambda *args: self._initializeToolWidgets())
            self._initializeToolWidgets()
        else:
            raise NotImplementedError

    def _initializeToolWidgets(self):
        if len(self._toolWdgts) > 0:
            # clean up from previous initialization
            for column in self._columnContainers:
                while (child := column.layout().takeAt(0)) is not None:
                    assert isinstance(child, QtWidgets.QLayoutItem)
                    child.widget().deleteLater()
            self._toolWdgts = {}

        toolsToShow = []
        for toolKey, tool in self.session.tools.items():
            if (self._hideInactiveTools and not tool.isActive) \
                    or not tool.doShowTrackingState \
                    or any(isinstance(tool, ToolCls) for ToolCls in self._hideToolTypes):
                continue
            toolsToShow.append(tool)

        maxNumToolsPerCol = ceil(len(toolsToShow) / self._numColumns)
        for iTool, tool in enumerate(toolsToShow):
            iCol = iTool // maxNumToolsPerCol
            wdgt = IconWidget(icon=qta.icon('mdi6.circle-outline', color='gray'))
            self._columnContainers[iCol].layout().addRow(tool.label, wdgt)
            self._toolWdgts[tool.key] = wdgt

    def _onLatestPositionsChanged(self):
        for toolKey, tool in self.session.tools.items():
            if (self._hideInactiveTools and not tool.isActive) \
                    or not tool.doShowTrackingState \
                    or any(isinstance(tool, ToolCls) for ToolCls in self._hideToolTypes):
                assert toolKey not in self._toolWdgts
                continue

            hasTransf = self._positionsClient.getLatestTransf(toolKey, None) is not None

            if self._prevHadTransf.get(toolKey, None) in (None, ~hasTransf):
                # only update icon if status changed
                wdgt = self._toolWdgts[toolKey]
                if hasTransf:
                    wdgt.icon = qta.icon('mdi6.circle', color='blue')
                else:
                    wdgt.icon = qta.icon('mdi6.help-circle', color='red')
                self._prevHadTransf[toolKey] = hasTransf


