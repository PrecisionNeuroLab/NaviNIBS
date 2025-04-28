from __future__ import annotations
import attrs
import logging
import typing as tp

from NaviNIBS.util.attrs import attrsAsDict
from NaviNIBS.util.Signaler import Signal

logger = logging.getLogger(__name__)


@attrs.define
class MiscSettings:
    _mainFontSize: float | None = None
    _theme: str = 'light'

    sigAttribsChanged: Signal[list[str] | None] = attrs.field(init=False, factory=Signal)
    """
    Includes list of keys of attributes that changed, or None if any/all should be assumed to have changed.
    """

    def __attrs_post_init__(self):
        pass

    def asDict(self):
        return attrsAsDict(self)

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any]):
        """
        Create a MiscSettings object from a dictionary.
        """
        return cls(**d)

    @property
    def mainFontSize(self):
        return self._mainFontSize

    @mainFontSize.setter
    def mainFontSize(self, value: float | None):
        if value == self._mainFontSize:
            return
        logger.info(f'Changing mainFontSize to {value}')
        self._mainFontSize = value
        self.sigAttribsChanged.emit(['mainFontSize'])

    @property
    def theme(self):
        return self._theme

    @theme.setter
    def theme(self, value: str):
        if value == self._theme:
            return
        logger.info(f'Changing theme to {value}')
        self._theme = value
        self.sigAttribsChanged.emit(['theme'])
