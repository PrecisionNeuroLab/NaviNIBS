from __future__ import annotations

from abc import ABC
import attrs
import importlib
import json
import logging
import os
import sys
import typing as tp

from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.attrs import attrsAsDict
from NaviNIBS.util.json import jsonPrettyDumps

from NaviNIBS.Navigator.Model.GenericCollection import GenericCollection, GenericCollectionDictItem

if tp.TYPE_CHECKING:
    from NaviNIBS.Navigator.Model.Session import Session
    from NaviNIBS.Navigator.GUI.NavigatorGUI import NavigatorGUI
    from NaviNIBS.Navigator.GUI.ViewPanels import MainViewPanel
    from NaviNIBS.Navigator.GUI.ViewPanels.NavigatePanel.NavigationView import NavigationView
    from NaviNIBS.Navigator.GUI.ViewPanels.NavigatePanel.ViewLayers import ViewLayer

logger = logging.getLogger(__name__)


_installPath = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')


ACE = tp.TypeVar('ACE')  # type (e.g. MainViewPanel) referenced by each addon class element


@attrs.define
class AddonSessionConfig(ABC):
    """
    Base class to define an addon's configuration parameters that will be saved in session config
    """

    sigConfigAboutToChange: Signal = attrs.field(init=False, factory=lambda: Signal((tp.Optional[list[str]],)))
    # should be emitted whenever a field that is included in serialized asDict() is about to change
    sigConfigChanged: Signal = attrs.field(init=False, factory=lambda: Signal((tp.Optional[list[str]],)))
    # should be emitted whenever a field that is included in serialized asDict() has changed

    def __attrs_post_init__(self):
        pass

    def asDict(self) -> dict[str, tp.Any]:
        # can be overridden by subclass
        return attrsAsDict(self)

    @classmethod
    def fromDict(cls, d: dict[str, tp.Any]):
        # can be overridden by subclass
        return cls(**d)


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
    def Class(self) -> tp.Type[ACE]:
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
class AddonExtra(ABC):
    """
    Base class to define optional custom code for an addon.

    Use this when an addon needs to provide custom code not part of a main view panel, navigation view layer, etc. This may be used for code-only addon pieces (such as providing a data output stream), or for patching existing GUI functionality (using the reference to the root NavigatorGUI instance).

    Any AddonCode class elements defined in an addion configuration will be instantiated and passed references to the root NavigatorGUI instance and the session model when the addon is loaded.
    """
    _navigatorGUI: NavigatorGUI
    _session: Session

    def __attrs_post_init__(self):
        pass


@attrs.define
class Addon(GenericCollectionDictItem[str]):
    _addonInstallPath: str
    _MainViewPanels: tp.Dict[str, AddonClassElement[MainViewPanel]] = attrs.field(factory=dict)
    _NavigationViews: tp.Dict[str, AddonClassElement[NavigationView]] = attrs.field(factory=dict)
    _NavigationViewLayers: tp.Dict[str, AddonClassElement[ViewLayer]] = attrs.field(factory=dict)
    _Extras: tp.Dict[str, AddonClassElement[AddonExtra]] = attrs.field(factory=dict)
    _SessionAttrs: tp.Dict[str, AddonClassElement[AddonSessionConfig]] = attrs.field(factory=dict)

    _sessionAttrs: dict[str, AddonSessionConfig] = attrs.field(factory=dict)
    _extras: dict[str, AddonExtra] = attrs.field(init=False, factory=dict)
    _isActive: bool = True

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        for key, SessionAttr in self._SessionAttrs.items():
            if key not in self._sessionAttrs:
                self._sessionAttrs[key] = SessionAttr.Class()

        for key, sessionAttr in self._sessionAttrs.items():
            assert isinstance(sessionAttr, self._SessionAttrs[key].Class)
            sessionAttr.sigConfigAboutToChange.connect(lambda *args: self.sigItemAboutToChange.emit(self.key, [key]))
            sessionAttr.sigConfigChanged.connect(lambda *args: self.sigItemChanged.emit(self.key, [key]))

    @property
    def MainViewPanels(self):
        return self._MainViewPanels

    @property
    def addonInstallPath(self):
        return self._addonInstallPath

    @property
    def SessionAttrs(self):
        return self._SessionAttrs

    @property
    def needsToInstantiateExtras(self):
        return len(self._Extras) > 0 and len(self._extras) == 0

    def instantiateExtras(self, navigatorGUI: NavigatorGUI, session: Session):
        if len(self._extras) > 0:
            raise RuntimeError('Addon extras already instantiated')

        for key, Extra in self._Extras.items():
            logger.info(f'Instantiating addon extra {key}')
            self._extras[key] = Extra.Class(navigatorGUI=navigatorGUI,
                                            session=session)

    def __getattr__(self, key: str):
        """
        Allow accessing session attributes with addon.attributeName
        """
        try:
            return self._sessionAttrs[key]
        except KeyError:
            raise AttributeError

    def asDict(self) -> tp.Dict[str, tp.Any]:
        predefinedAttrs = ['key', 'MainViewPanels', 'Extras', 'NavigationViews', 'NavigationViewLayers', 'SessionAttrs']  # these should all be defined in fixed addon_configuration.json file

        d = attrsAsDict(self, exclude=predefinedAttrs + ['sessionAttrs'])
        for key, sessionAttr in self._sessionAttrs.items():
            d[key] = sessionAttr.asDict()

        d['addonInstallPath'] = os.path.relpath(self.addonInstallPath, _installPath)

        return d

    def getConfigFilename(self, unpackedSessionDir: str, filenamePrefix: str = '') -> str:
        return filenamePrefix + 'Addon_' + self.key + '.json'

    def writeConfig(self, unpackedSessionDir: str, filenamePrefix: str = '') -> str:
        configFilename_addon = self.getConfigFilename(
            unpackedSessionDir=unpackedSessionDir,
            filenamePrefix=filenamePrefix)
        toWrite = jsonPrettyDumps(self.asDict())
        with open(os.path.join(unpackedSessionDir, configFilename_addon), 'w') as f:
            f.write(toWrite)
        return configFilename_addon

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any]) -> Addon:
        d = d.copy()
        # assume only path to addon_configuration and optionally SessionAttrs are specified

        assert 'addonInstallPath' in d

        # specified path in config is relative to root NaviNIBS installPath
        addonInstallPath = os.path.join(_installPath, d['addonInstallPath'])
        del d['addonInstallPath']
        addonConfigPath = os.path.join(addonInstallPath, 'addon_configuration.json')

        if not os.path.exists(addonConfigPath):
            raise FileNotFoundError(f'Addon configuration not found at {addonConfigPath}')

        with open(addonConfigPath, 'r') as f:
            dc = json.load(f)

        if not os.path.split(addonInstallPath)[0] in sys.path:
            sys.path.append(os.path.split(addonInstallPath)[0])

        elementAttrs = ('MainViewPanels', 'Extras', 'NavigationViews', 'NavigationViewLayers', 'SessionAttrs')

        initKwargs = dict(
            addonInstallPath=addonInstallPath,
            key=dc['key']
        )

        for elementAttr in elementAttrs:
            if elementAttr in dc:
                initKwargs[elementAttr] = dict()
                for elementKey, elementDict in dc[elementAttr].items():
                    initKwargs[elementAttr][elementKey] = AddonClassElement.fromDict(elementDict)

        if len(d) > 0:
            sessionAttrs = dict()
            for key, sessionAttrDict in d.items():
                try:
                    SessionAttr = initKwargs['SessionAttrs'][key].Class
                except KeyError:
                    raise KeyError(f'Addon session config included session attributes defined for {key}, but this was not found in addon_configuration.json')

                assert issubclass(SessionAttr, AddonSessionConfig)
                sessionAttrs[key] = SessionAttr.fromDict(sessionAttrDict)

            initKwargs['sessionAttrs'] = sessionAttrs
        else:
            pass  # no other addon-specific attrs defined in session config

        return cls(**initKwargs)


@attrs.define
class Addons(GenericCollection[str, Addon]):

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def asList(self, unpackedSessionDir: str,
               filenamePrefix: str = '',
               doWriteAddonConfigs: bool = True) -> tp.List[str]:
        addonSessionConfigFilenames = []
        for addonKey, addon in self.items():
            if doWriteAddonConfigs:
                configFilename_addon = addon.writeConfig(unpackedSessionDir=unpackedSessionDir,
                                                         filenamePrefix=filenamePrefix)
            else:
                configFilename_addon = addon.getConfigFilename(unpackedSessionDir=unpackedSessionDir,
                                                               filenamePrefix=filenamePrefix)
            addonSessionConfigFilenames.append(configFilename_addon)
        return addonSessionConfigFilenames

    @classmethod
    def fromList(cls, addonList: tp.List[str], unpackedSessionDir: str) -> Addons:
        addons = {}
        for addonSessionConfigFilename in addonList:
            with open(os.path.join(unpackedSessionDir, addonSessionConfigFilename), 'r') as f:
                addonDict = json.load(f)
            addon = Addon.fromDict(addonDict)
            addons[addon.key] = addon

        return cls(items=addons)



