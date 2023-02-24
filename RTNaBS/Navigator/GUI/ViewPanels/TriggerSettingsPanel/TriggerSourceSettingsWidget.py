from __future__ import annotations

import typing as tp

import attrs
from qtpy import QtWidgets

from RTNaBS.Navigator.Model.Session import Session
from RTNaBS.util.GUI import DockWidgets as dw


TS = tp.TypeVar('TS')   # TriggerSource class (e.g. LSLTriggerSource) referenced by a settings widget


@attrs.define(kw_only=True)
class TriggerSourceSettingsWidget(tp.Generic[TS]):
    _dockKey: str
    _title: str
    _cdw: dw.DockWidget = attrs.field(init=False)
    _wdgt: QtWidgets.QWidget = attrs.field(factory=QtWidgets.QWidget)
    _triggerSourceKey: str
    _session: Session

    def __attrs_post_init__(self):
        self._cdw = dw.DockWidget(
            uniqueName=self._dockKey + self._title,
            options=dw.DockWidgetOptions(notClosable=True),
            title=self._title,
            affinities=[self._dockKey]
        )
        self._cdw.setWidget(self._wdgt)

    @property
    def cdw(self):
        return self._cdw

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newSession: Session):
        self._session = newSession
        self._onSessionSet()

    def _onSessionSet(self):
        pass

    @property
    def triggerSource(self) -> tp.Optional[TS]:
        if self.session is not None and self._triggerSourceKey in self.session.triggerSources:
            return self.session.triggerSources[self._triggerSourceKey]
        else:
            return None
