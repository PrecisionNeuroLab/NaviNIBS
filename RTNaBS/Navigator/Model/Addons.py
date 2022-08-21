from __future__ import annotations

import attrs
import importlib
import logging
import typing as tp

from RTNaBS.util.Signaler import Signal
from RTNaBS.util.attrs import attrsAsDict

if tp.TYPE_CHECKING:
    from RTNaBS.Navigator.GUI.ViewPanels import MainViewPanel
    from RTNaBS.Navigator.GUI.ViewPanels.NavigatePanel.NavigationView import NavigationView
    from RTNaBS.Navigator.GUI.ViewPanels.NavigatePanel.ViewLayers import ViewLayer

logger = logging.getLogger(__name__)


ACE = tp.TypeVar('ACE')  # type (e.g. MainViewPanel) referenced by each addon class element


@attrs.define
class AddonClassElement(tp.Generic[ACE]):
    _ClassName: str
    _importModule: str
    _key: str = None  # if not specified, will be same as ClassName
    isBuiltIn: bool = False
    _Class: tp.Optional[tp.Type[ACE]] = attrs.field(init=False, default=None)

    def __attrs_post_init__(self):
        if self._importModule is not None:
            assert self._ClassName is not None  # need both importModule and ClassName to fully specify what to import

            if False:
                # disabled for now since it seems to cause some import errors / long delays
                # TODO: troubleshoot problems and set this to be optionally enabled by default
                logger.debug('Verifying specified importModule is in path')  # TODO: debug, delete log statements
                if importlib.util.find_spec(self._importModule) is None:
                    raise ModuleNotFoundError('Module "%s" not found in python path' % (self._importModule,))
                logger.debug('Verified')
        else:
            assert self._Class is not None
            if self._ClassName is None:
                self._ClassName = self._Class.__name__
            else:
                assert self._ClassName == self._Class.__name__

    @property
    def key(self):
        if self._key is None:
            return self._ClassName
        else:
            return self._key

    @property
    def ClassName(self):
        return self._ClassName

    @property
    def importModule(self):
        return self._importModule

    @property
    def Class(self):
        if self._Class is None:
            self.reload()
            assert self._Class is not None
        return self._Class

    def reload(self):
        assert self._importModule is not None
        logger.debug('Importing module %s' % (self._importModule,))
        module = importlib.import_module(self._importModule)
        self._Class = getattr(module, self._ClassName)

    def asDict(self) -> tp.Dict[str, tp.Any]:
        return attrsAsDict(self)

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any]):
        return cls(**d)


@attrs.define
class Addon:
    _key: str
    _MainViewPanels: tp.Dict[str, AddonClassElement[MainViewPanel]] = attrs.field(factory=dict)
    _NavigationViews: tp.Dict[str, AddonClassElement[NavigationView]] = attrs.field(factory=dict)
    _NavigationViewLayers: tp.Dict[str, AddonClassElement[ViewLayer]] = attrs.field(factory=dict)
    _SessionAttrs: tp.Dict[str, AddonClassElement[tp.Any]] = attrs.field(factory=dict)
    _isActive: bool = True

    @property
    def key(self):
        return self._key

    def asDict(self) -> tp.Dict[str, tp.Any]:
        d = dict(
            key=self._key,
        )
        if not self._isActive:
            d['isActive'] = self._isActive

        elementAttrs = ('MainViewPanels', 'NavigationViews', 'NavigationViewLayers', 'SessionAttrs')
        for elementAttr in elementAttrs:
            d[elementAttr] = {key: element.asDict() for key, element in getattr(self, '_' + elementAttr).items()}

        return d

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any]) -> Addon:
        elementAttrs = ('MainViewPanels', 'NavigationViews', 'NavigationViewLayers', 'SessionAttrs')
        for elementAttr in elementAttrs:
            if elementAttr in d:
                d[elementAttr] = AddonClassElement.fromDict(d[elementAttr])

        return cls(**d)


@attrs.define
class Addons:
    _addons: tp.Dict[str, Addon] = attrs.field(factory=dict)

    sigAddonsChanged: Signal = attrs.field(factory=lambda: Signal((tp.List[str], tp.Optional[tp.List[str]])))

    def __attrs_post_init__(self):
        for key, addon in self._addons.items():
            assert addon.key == key

    def __getitem__(self, item):
        return self._addons[item]

    def __iter__(self):
        return iter(self._addons)

    def __len__(self):
        return len(self._addons)

    def keys(self):
        return self._addons.keys()

    def items(self):
        return self._addons.items()

    def values(self):
        return self._addons.values()

    @property
    def addons(self):
        return self._addons  # note: result should not be modified directly

    def asList(self) -> tp.List[tp.Dict[str, tp.Any]]:
        return [addon.asDict() for addon in self._addons.values()]

    @classmethod
    def fromList(cls, addonList: tp.List[tp.Dict[str, tp.Any]]) -> Addons:
        addons = {}
        for addonDict in addonList:
            addon = Addon.fromDict(addonDict)
            addons[addon.key] = addon

        return cls(addons=addons)



