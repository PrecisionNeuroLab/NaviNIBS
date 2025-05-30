from __future__ import annotations

import asyncio
import attrs
import logging
from qtpy import QtWidgets, QtGui, QtCore
import typing as tp

from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.util.GUI.Dock import Dock, DockArea
from NaviNIBS.util.Signaler import Signal


logger = logging.getLogger(__name__)


@attrs.define()
class MainViewPanel:
    _key: str
    _session: tp.Optional[Session] = attrs.field(default=None, repr=False)

    _label: tp.Optional[str] = None
    _icon: tp.Optional[QtGui.QIcon] = attrs.field(default=None)
    _iconFn: tp.Optional[tp.Callable[..., QtGui.QIcon]] = attrs.field(default=None, repr=False)
    """
    To be set by subclass. 
    If iconFn is set, icon will be ignored and iconFn will be called to generate the icon.
    This allows regenerating the icon automatically, e.g. after color palette changes.
    """

    _wdgt: QtWidgets.QWidget = attrs.field(init=False, factory=QtWidgets.QWidget)
    _dockWdgt: Dock = attrs.field(init=False)

    _hasInitialized: bool = attrs.field(init=False, default=False)
    _isInitializing: bool = attrs.field(init=False, default=False)  # True while in the middle of finishing initializing

    _isShown: bool = attrs.field(init=False, default=False)
    sigPanelShown: Signal = attrs.field(init=False, factory=Signal)
    sigPanelHidden: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        logger.info(f'Initializing {self.__class__.__name__}')
        self._dockWdgt = Dock(name=self._key,
                              title=self.label,
                              affinities=['MainViewPanel'],
                              icon=self._icon,
                              iconFn=self._iconFn)
        self._dockWdgt.addWidget(self._wdgt)

        self._dockWdgt.sigHidden.connect(self.sigPanelHidden.emit)
        self._dockWdgt.sigShown.connect(self.sigPanelShown.emit)

        self.sigPanelShown.connect(self._onPanelShown)
        self.sigPanelHidden.connect(self._onPanelHidden)

        self._wdgt.setEnabled(False)
        self._wdgt.setVisible(False)

    @property
    def key(self):
        return self._key

    @property
    def label(self):
        return self._label if self._label is not None else self._key

    @property
    def wdgt(self):
        return self._wdgt

    @property
    def dockWdgt(self):
        return self._dockWdgt

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, newVal: tp.Optional[session]):
        if self._session is newVal:
            return
        self._session = newVal
        self._onSessionSet()

    @property
    def isVisible(self):
        return self._dockWdgt.isVisible()

    @property
    def hasInitialized(self):
        return self._hasInitialized

    @property
    def isInitializing(self):
        return self._isInitializing

    def _onSessionSet(self):
        logger.debug(f'{self.__class__.__name__} onSessionSet')
        pass  # to be implemented by subclass

    def _onPanelShown(self):
        logger.info(f'Panel {self.key} shown')
        self._isShown = True
        if not self._hasInitialized and self.canBeEnabled()[0]:
            self.finishInitialization()

    def _onPanelHidden(self):
        logger.info(f'Panel {self.key} hidden')
        self._isShown = False

    def canBeEnabled(self) -> tuple[bool, str | None]:
        """
        Should return (True, None) if panel can be enabled, and (False, '<Reason why cannot be enabled>') otherwise.
        The second output in the latter case should be a string explaining why the panel cannot be enabled,
        e.g. 'Registration must be completed before navigating'
        """
        return True, None  # can be implemented by subclass to indicate when panel is missing critical information and can't yet show anything useful

    def updateEnabled(self):
        if self.canBeEnabled()[0]:
            self.dockWdgt.setEnabled(True)
            if self.isVisible:
                if not self.hasInitialized and not self.isInitializing:
                    self.finishInitialization()
            else:
                self.wdgt.setEnabled(True)
            self.dockWdgt.label.setToolTip(self.label)
        else:
            self.wdgt.setEnabled(False)
            self.dockWdgt.setEnabled(False)
            self.dockWdgt.label.setToolTip(f'{self.label}\n({self.canBeEnabled()[1]})')

    def finishInitialization(self):
        if self._isInitializing:
            logger.warning(f"{self.key} is already finishing initializing, skipping.")
            return

        if self._hasInitialized:
            logger.warning(f"{self.key} finish initialization requested, but has already initialized. Not running again.")
            return

        canBeEnabled, reason = self.canBeEnabled()
        if not canBeEnabled:
            logger.warning(f"{self.key} finish initialization requested, but not yet ready to be enabled ({reason}). Skipping.")
            return

        self._isInitializing = True
        self._finishInitialization()
        self._isInitializing = False

        self._wdgt.setEnabled(True)
        self._wdgt.setVisible(True)
        self._hasInitialized = True

    def _finishInitialization(self):
        logger.debug(f'{self.__class__.__name__} _finishInitialization')
        pass  # can be implemented by subclass

    def _onPanelDeactivated(self):
        pass

    def close(self):
        self._dockWdgt.close()