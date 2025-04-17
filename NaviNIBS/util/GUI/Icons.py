from __future__ import annotations

import qtawesome as qta
from qtpy import QtWidgets, QtGui, QtCore


def getIcon(name: str, **kwargs) -> QtGui.QIcon:
    """
    Wrapper around qta.icon but with colors that update from QPalette with each call
    instead of using qta's cached initial colors.
    """
    if True:
        return DynamicColorIconicFont.getSingleton().icon(name, **kwargs)
    else:
        kwargs.setdefault('color', qta.iconic_font.text_color())
        kwargs.setdefault('color_disabled', qta.iconic_font.text_color_disabled())
        return qta.icon(name, **kwargs)


_dynamicColorIconicFontSingleton = None


class DynamicColorIconicFont(qta.IconicFont):
    """
    Extend qta.IconicFont to automatically update to use current palette colors (if color is not manually specified)
    """

    def __init__(self, *args):
        # override init to substitute in our custom dynamic color painter
        QtCore.QObject.__init__(self)
        self.painter = DynamicColorCharIconPainter()
        self.painters = {}
        self.fontname = {}
        self.fontdata = {}
        self.fontids = {}
        self.charmap = {}
        self.icon_cache = {}
        self.rawfont_cache = {}
        for fargs in args:
            self.load_font(*fargs)

    def _parse_options(self, specific_options, general_options, name):
        # override default colors to be "None" to indicate they should be dynamically updated when painting
        override_defaults = dict(
            color=None,
            color_disabled=None
        )
        general_options = dict(override_defaults, **general_options)

        return super()._parse_options(specific_options, general_options, name)

    @classmethod
    def getSingleton(cls) -> DynamicColorIconicFont:
        global _dynamicColorIconicFontSingleton
        if _dynamicColorIconicFontSingleton is None:
            _dynamicColorIconicFontSingleton = cls(*qta._BUNDLED_FONTS)
        return _dynamicColorIconicFontSingleton


class DynamicColorCharIconPainter(qta.iconic_font.CharIconPainter):
    def _paint_icon(self, iconic, painter, rect, mode, state, options):
        options = options.copy()
        for key in ('color', 'color_on', 'color_active', 'color_selected', 'color_on_active', 'color_on_selected',
                    'color_off', 'color_off_active', 'color_off_selected'):
            if key in options and options[key] is None:
                options[key] = qta.iconic_font.text_color()
        for key in ('color_on_disabled', 'color_off_disabled'):
            if key in options and options[key] is None:
                options[key] = qta.iconic_font.text_color_disabled()
        return super()._paint_icon(iconic, painter, rect, mode, state, options)



