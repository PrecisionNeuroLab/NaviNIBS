from __future__ import annotations

import shutil

import attrs
from copy import deepcopy
from datetime import datetime
import nibabel as nib
import json
import logging
import numpy as np
import os
import pandas as pd
import pyvista as pv
import tempfile
import typing as tp
from typing import ClassVar

from RTNaBS.Navigator.Model.MRI import MRI
from RTNaBS.Navigator.Model.HeadModel import HeadModel
from RTNaBS.Navigator.Model.CoordinateSystems import CoordinateSystems, CoordinateSystem
from RTNaBS.Navigator.Model.Targets import Targets, Target
from RTNaBS.Navigator.Model.Samples import Samples, Sample
from RTNaBS.Navigator.Model.SubjectRegistration import SubjectRegistration
from RTNaBS.Navigator.Model.Tools import Tools, Tool, CoilTool, Pointer, SubjectTracker, CalibrationPlate
from RTNaBS.Navigator.Model.Triggering import TriggerSources
from RTNaBS.Navigator.Model.DigitizedLocations import DigitizedLocations, DigitizedLocation
from RTNaBS.Navigator.Model.Addons import Addons, Addon
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.json import jsonPrettyDumps
from RTNaBS.util.numpy import array_equalish


logger = logging.getLogger(__name__)


@attrs.define
class MNIRegistration:
    sigTransformChanged: Signal = attrs.field(init=False, factory=Signal)
    # TODO


@attrs.define
class Session:
    _filepath: str  # path to compressed session file
    _subjectID: tp.Optional[str] = attrs.field(default=None)
    _sessionID: tp.Optional[str] = None
    _MRI: MRI = attrs.field(factory=MRI)
    _headModel: HeadModel = attrs.field(factory=HeadModel)
    _subjectRegistration: SubjectRegistration = attrs.field(factory=SubjectRegistration)
    _coordinateSystems: CoordinateSystems = attrs.field(factory=CoordinateSystems)
    _targets: Targets = attrs.field(factory=Targets)
    _tools: Tools = attrs.field(default=None)
    _samples: Samples = attrs.field(factory=Samples)
    _digitizedLocations: DigitizedLocations = attrs.field(factory=DigitizedLocations)
    _triggerSources: TriggerSources = attrs.field(factory=TriggerSources)
    _addons: Addons = attrs.field(factory=Addons)

    _dirtyKeys: tp.Set[str] = attrs.field(init=False, factory=set)  # session info parts that have changed since last save
    _dirtyKeys_autosave: tp.Set[str] = attrs.field(init=False, factory=set)  # session info parts that have changed since last autosave
    _compressedFileIsDirty: bool = True

    _sessionConfigFilename: ClassVar[str] = 'SessionConfig'
    _lastAutosaveFilenamePrefix: tp.Optional[str] = None
    _latestConfigFormatVersion: ClassVar[str] = '0.0.2'
    """
    Config format version history:
    - 0.0.1: Initial format, went through a number of iterations
    - 0.0.2:
        - Switched from dict to list of trigger sources
        - Switched fiducial format
    """
    _unpackedSessionDir: tp.Optional[tp.Union[tempfile.TemporaryDirectory, str]] = attrs.field(default=None)

    sigInfoChanged: Signal = attrs.field(init=False, factory=lambda: Signal((tp.Optional[list[str]])))
    """
    Includes list of keys in info that were changed. If list is None, subscribers should assume that all info changed.
    """

    def __attrs_post_init__(self):
        if self._unpackedSessionDir is None:
            self._unpackedSessionDir = self.getTempUnpackDir()

        if not os.path.isdir(self.unpackedSessionDir):
            logger.debug('Creating dir for unpacking session at {}'.format(self.unpackedSessionDir))
            os.makedirs(self.unpackedSessionDir)

        if self._tools is None:
            self._tools = Tools(sessionPath=self._filepath)

        self.sigInfoChanged.connect(lambda *args: self.flagKeyAsDirty('info'))
        self.MRI.sigFilepathChanged.connect(lambda: self.flagKeyAsDirty('MRI'))
        self.headModel.sigFilepathChanged.connect(lambda: self.flagKeyAsDirty('headModel'))
        self.subjectRegistration.fiducials.sigItemsChanged.connect(lambda *args: self.flagKeyAsDirty('subjectRegistration'))
        self.subjectRegistration.sampledHeadPoints.sigHeadpointsChanged.connect(lambda *args: self.flagKeyAsDirty('subjectRegistration'))
        self.subjectRegistration.sampledHeadPoints.sigAttribsChanged.connect(lambda *args: self.flagKeyAsDirty('subjectRegistration'))
        self.subjectRegistration.sigTrackerToMRITransfChanged.connect(lambda: self.flagKeyAsDirty('subjectRegistration'))
        self.targets.sigItemsChanged.connect(lambda targetKeys, attribKeys: self.flagKeyAsDirty('targets'))
        self.samples.sigItemsChanged.connect(lambda sampleTimestamps, attribKeys: self.flagKeyAsDirty('samples'))
        self.tools.sigItemsChanged.connect(lambda *args: self.flagKeyAsDirty('tools'))
        self.tools.sigPositionsServerInfoChanged.connect(lambda *args: self.flagKeyAsDirty('tools'))
        self.triggerSources.sigItemsChanged.connect(lambda *args: self.flagKeyAsDirty('triggerSources'))
        self.addons.sigItemsChanged.connect(self._onAddonsChanged)
        self.targets.sigItemKeyChanged.connect(self._updateSamplesForNewTargetKey)
        self.targets.sigItemsAboutToChange.connect(self._onTargetsAboutToChange, priority=1)  # use higher priority to make sure we handle adding historical samples before notifying GUIs of this change
        self.targets.sigItemsChanged.connect(self._onTargetsChanged)
        self.samples.sigItemsChanged.connect(self._onSamplesChanged)
        self.coordinateSystems.sigItemsChanged.connect(self._onCoordinateSystemsChanged)
        self.digitizedLocations.sigItemsChanged.connect(lambda *args: self.flagKeyAsDirty('digitizedLocations'))

        self.coordinateSystems.session = self
        self.targets.session = self

        # TODO

    @property
    def subjectID(self):
        return self._subjectID

    @subjectID.setter
    def subjectID(self, newVal: str):
        if newVal != self._subjectID:
            self._subjectID = newVal
            self.sigInfoChanged.emit(['subjectID'])

    @property
    def sessionID(self):
        return self._sessionID

    @sessionID.setter
    def sessionID(self, newVal: str):
        if newVal != self._sessionID:
            self._sessionID = newVal
            self.sigInfoChanged.emit(['sessionID'])

    @property
    def filepath(self):
        return self._filepath

    @filepath.setter
    def filepath(self, newVal: str):
        if newVal != self._filepath:
            self._filepath = newVal
            self.sigInfoChanged.emit(['filepath'])
            self.tools.sessionPath = self._filepath

    @property
    def MRI(self):
        return self._MRI

    @property
    def headModel(self):
        return self._headModel

    @property
    def coordinateSystems(self):
        return self._coordinateSystems

    @property
    def digitizedLocations(self):
        return self._digitizedLocations

    @property
    def subjectRegistration(self):
        return self._subjectRegistration

    @property
    def targets(self):
        return self._targets

    @property
    def samples(self):
        return self._samples

    @property
    def tools(self):
        return self._tools

    @property
    def triggerSources(self):
        return self._triggerSources

    @property
    def addons(self):
        return self._addons

    @property
    def compressedFileIsDirty(self):
        return len(self._dirtyKeys) > 0 or self._compressedFileIsDirty

    @property
    def unpackedSessionDir(self):
        if isinstance(self._unpackedSessionDir, tempfile.TemporaryDirectory):
            return self._unpackedSessionDir.name
        else:
            return self._unpackedSessionDir

    @unpackedSessionDir.setter
    def unpackedSessionDir(self, newUnpackedSessionDir: str):
        if newUnpackedSessionDir == self.unpackedSessionDir:
            # no change
            return

        # rather than just changing directory and resaving, copy contents of entire previous directory
        #  (to make sure we bring any dependencies like session-specific images with us)
        logger.info('Copying contents of previous session dir to new location')
        shutil.copytree(self.unpackedSessionDir, newUnpackedSessionDir, dirs_exist_ok=True)
        # note that any existing files in new folder that don't also exist in old folder will remain in place (not be deleted)
        logger.debug('Done copying')

        self._unpackedSessionDir = newUnpackedSessionDir

    def flagKeyAsDirty(self, key: str):
        self._dirtyKeys.add(key)
        self._dirtyKeys_autosave.add(key)

    def saveToUnpackedDir(self, saveDirtyOnly: bool = True, asAutosave: bool = False):

        if asAutosave:
            keysToSave = self._dirtyKeys_autosave.copy()
            self._dirtyKeys_autosave.clear()
            autosaveFilenamePrefix = 'autosaved-' + datetime.today().strftime('%y%m%d%H%M%S.%f') + '_'
        else:
            keysToSave = self._dirtyKeys.copy()
            self._dirtyKeys.clear()
            self._dirtyKeys_autosave.clear()
            self._lastAutosaveFilenamePrefix = None
            autosaveFilenamePrefix = ''

        if saveDirtyOnly and len(keysToSave) == 0:
            logger.debug('Nothing to save')
            return

        self._compressedFileIsDirty = True

        if asAutosave:
            configPath = os.path.join(self.unpackedSessionDir, autosaveFilenamePrefix + self._sessionConfigFilename + '.json')
            if self._lastAutosaveFilenamePrefix is not None:
                prevConfigPath = os.path.join(self.unpackedSessionDir, self._lastAutosaveFilenamePrefix + self._sessionConfigFilename + '.json')
            else:
                prevConfigPath = os.path.join(self.unpackedSessionDir, self._sessionConfigFilename + '.json')
            self._lastAutosaveFilenamePrefix = autosaveFilenamePrefix
        else:
            configPath = os.path.join(self.unpackedSessionDir, self._sessionConfigFilename + '.json')
            prevConfigPath = configPath

        if os.path.exists(prevConfigPath):
            with open(prevConfigPath, 'r') as f:
                config = json.load(f)
            assert config['formatVersion'] == self._latestConfigFormatVersion
        else:
            config = dict(formatVersion=self._latestConfigFormatVersion)

        if 'info' in keysToSave or not saveDirtyOnly:
            logger.debug('Writing session info')
            infoFields = ('filepath', 'subjectID', 'sessionID')
            for field in infoFields:
                config[field] = getattr(self, field)
            keysToSave.discard('info')

        otherPathsRelTo = self.filepath

        if 'MRI' in keysToSave or not saveDirtyOnly:
            # save MRI path relative to location of compressed file
            logger.debug('Writing MRI info')
            configFilename_MRI =  autosaveFilenamePrefix + self._sessionConfigFilename + '_MRI' + '.json'
            config['MRI'] = configFilename_MRI
            with open(os.path.join(self.unpackedSessionDir, configFilename_MRI), 'w') as f:
                f.write(jsonPrettyDumps(self.MRI.asDict(filepathRelTo=otherPathsRelTo)))
            keysToSave.discard('MRI')

        if 'headModel' in keysToSave or not saveDirtyOnly:
            # save head model path relative to location of compressed file
            logger.debug('Writing headModel info')
            configFilename_headModel = autosaveFilenamePrefix + self._sessionConfigFilename + '_HeadModel' + '.json'
            config['headModel'] = configFilename_headModel
            with open(os.path.join(self.unpackedSessionDir, configFilename_headModel), 'w') as f:
                f.write(jsonPrettyDumps(self.headModel.asDict(filepathRelTo=otherPathsRelTo)))
            keysToSave.discard('headModel')

        if 'coordinateSystems' in keysToSave or not saveDirtyOnly:
            # save coordinate systems path relative to location of compressed file
            logger.debug('Writing coordinateSystems info')
            configFilename_coordinateSystems = autosaveFilenamePrefix + self._sessionConfigFilename + '_CoordinateSystems' + '.json'
            config['coordinateSystems'] = configFilename_coordinateSystems
            with open(os.path.join(self.unpackedSessionDir, configFilename_coordinateSystems), 'w') as f:
                f.write(jsonPrettyDumps(self.coordinateSystems.asList()))
            keysToSave.discard('coordinateSystems')

        if 'digitizedLocations' in keysToSave or not saveDirtyOnly:
            # save digitized locations path relative to location of compressed file
            logger.debug('Writing digitizedLocations info')
            configFilename_digitizedLocations = autosaveFilenamePrefix + self._sessionConfigFilename + '_DigitizedLocations' + '.json'
            config['digitizedLocations'] = configFilename_digitizedLocations
            with open(os.path.join(self.unpackedSessionDir, configFilename_digitizedLocations), 'w') as f:
                f.write(jsonPrettyDumps(self.digitizedLocations.asList()))
            keysToSave.discard('digitizedLocations')

        if 'subjectRegistration' in keysToSave or not saveDirtyOnly:
            logger.debug('Writing subjectRegistration info')
            configFilename_subjectReg = autosaveFilenamePrefix + self._sessionConfigFilename + '_SubjectRegistration' + '.json'
            config['subjectRegistration'] = configFilename_subjectReg
            with open(os.path.join(self.unpackedSessionDir, configFilename_subjectReg), 'w') as f:
                f.write(jsonPrettyDumps(self.subjectRegistration.asDict()))
            # TODO: save contents of potentially larger *History fields to separate file(s)
            keysToSave.discard('subjectRegistration')

        if 'targets' in keysToSave or not saveDirtyOnly:
            logger.debug('Writing targets info')
            configFilename_targets = autosaveFilenamePrefix + self._sessionConfigFilename + '_Targets' + '.json'
            config['targets'] = configFilename_targets
            with open(os.path.join(self.unpackedSessionDir, configFilename_targets), 'w') as f:
                f.write(jsonPrettyDumps(self.targets.asList()))
            keysToSave.discard('targets')

        if 'samples' in keysToSave or not saveDirtyOnly:
            logger.debug('Writing samples info')
            configFilename_samples = autosaveFilenamePrefix + self._sessionConfigFilename + '_Samples' + '.json'
            config['samples'] = configFilename_samples
            with open(os.path.join(self.unpackedSessionDir, configFilename_samples), 'w') as f:
                f.write(jsonPrettyDumps(self.samples.asList()))
            keysToSave.discard('samples')

        if 'tools' in keysToSave or not saveDirtyOnly:
            logger.debug('Writing tools info')
            configFilename_tools = autosaveFilenamePrefix + self._sessionConfigFilename + '_Tools' + '.json'
            config['tools'] = configFilename_tools
            with open(os.path.join(self.unpackedSessionDir, configFilename_tools), 'w') as f:
                f.write(jsonPrettyDumps(self.tools.asList()))
            keysToSave.discard('tools')

        if 'triggerSources' in keysToSave or not saveDirtyOnly:
            logger.debug('Writing triggerSources info')
            config['triggerSources'] = self.triggerSources.asList()
            keysToSave.discard('triggerSources')

        # TODO: loop through any addons to give them a chance to save to config as needed

        if 'addons' in keysToSave or not saveDirtyOnly:
            logger.debug('Writing addons info')
            config['addons'] = self.addons.asList(unpackedSessionDir=self.unpackedSessionDir)
            keysToDiscard = {'addons'}
            for key in keysToSave:
                if key.startswith('addon.'):
                    keysToDiscard.add(key)
            keysToSave -= keysToDiscard
        else:
            # write config for changed addons only, not all addons
            for addonKey, addon in self.addons.items():
                flagKey = f'addon.{addonKey}'
                if flagKey in keysToSave:
                    configFilename_addon = addon.writeConfig(unpackedSessionDir=self.unpackedSessionDir)
                    assert configFilename_addon in config['addons']
                    keysToSave.discard(flagKey)

        # TODO: save other fields
        assert len(keysToSave) == 0

        with open(configPath, 'w') as f:
            if False:
                json.dump(config, f)
            else:
                f.write(jsonPrettyDumps(config))
            logger.debug('Wrote updated session config')

    def _updateSamplesForNewTargetKey(self, fromKey: str, toKey: str):
        # update any referenced target IDs in samples to use the new key
        target = self.targets[toKey]
        if not target.mayBeADependency:
            # assume this flag would be set if any samples reference the target,
            # so no need to check samples
            return
        for sampleKey, sample in self.samples.items():
            if sample.targetKey == fromKey:
                logger.debug(f'Updating associated targetKey for {sampleKey} from {fromKey} to {toKey}')
                sample.targetKey = toKey

    def _onTargetsAboutToChange(self, keys: list[str], changingAttrs: tp.Optional[list[str]] = None):
        for targetKey in keys:
            if targetKey not in self._targets:
                # probably a completely new target
                continue

            target = self._targets[targetKey]
            if not target.mayBeADependency:
                continue
            if changingAttrs is None or any(x in changingAttrs for x in ('targetCoord',
                                                                         'entryCoord',
                                                                         'angle',
                                                                         'depthOffset',
                                                                         'coilToMRITransf')):
                # targeting info is about to change, and this target may be referenced by samples
                # so make a copy to keep in history
                logger.info('Creating historical copy of target')
                historicalTarget = Target.fromDict(deepcopy(target.asDict()))
                historicalTarget.key = targetKey + ' pre ' + datetime.today().strftime('%y%m%d%H%M%S.%f')
                historicalTarget.isHistorical = True
                target.mayBeADependency = False
                self._targets.addItem(historicalTarget)
                self._updateSamplesForNewTargetKey(fromKey=target.key, toKey=historicalTarget.key)

    def _onTargetsChanged(self, keys: list[str], changedAttrs: tp.Optional[list[str]] = None):
        pass

    def _onSamplesChanged(self, keys: list[str], changedAttrs: tp.Optional[list[str]] = None):
        if changedAttrs is None or 'targetKey' in changedAttrs:
            for sampleKey in keys:
                sample = self.samples[sampleKey]
                targetKey = sample.targetKey
                if targetKey is not None and targetKey in self._targets:
                    # mark that if there are future changes in the target, a copy may need to be kept (or this sample may need to be notified)
                    self._targets[targetKey].mayBeADependency = True

    def _onCoordinateSystemsChanged(self, keys: list[str], changedAttrs: tp.Optional[list[str]] = None):
        # only mark coordinate systems as dirty if a non-autogenerated coordSys changed
        # (changes in autogenerated coordinate systems don't require config file updates)
        isDirty = False
        for key in keys:
            if not self.coordinateSystems[key].isAutogenerated:
                isDirty = True
                break
        if isDirty:
            self.flagKeyAsDirty('coordinateSystems')

    def _onAddonsChanged(self, addonKeys: list[str], attribKeys: tp.Optional[list[str]] = None):
        for addonKey in addonKeys:
            self.flagKeyAsDirty(f'addon.{addonKey}')

    def saveToFile(self, updateDirtyOnly: bool = True):
        self.saveToUnpackedDir(saveDirtyOnly=updateDirtyOnly)
        if self._filepath == self._unpackedSessionDir:
            # original session file was already an unpacked dir, don't need to compress now
            logger.info('Saving to unpacked session dir only, skipping save of compressed session file.')
            return

        if not self._compressedFileIsDirty:
            logger.warning('Nothing to save')
            return

        logger.debug('Making archive')
        shutil.make_archive(
            base_name=self._filepath,
            format='zip',
            root_dir=self.unpackedSessionDir,
            base_dir='.',
        )
        shutil.move(self._filepath + '.zip', self._filepath)
        logger.debug('Done saving')

        self._compressedFileIsDirty = False

    def mergeFromFile(self, filepath: str, sections: tp.Optional[tp.List[str]] = None):
        """
        Import session elements from another file. Specify `sections` to only read a subset of elements from the file to merge, e.g. `sections=['targets']` to ignore everything but the targets section in the loaded file.
        """

        logger.info('Merge {} from file: {}'.format('all' if sections is None else sections, filepath))

        _, ext = os.path.splitext(filepath)

        if ext == '.json':
            if sections == ['targets']:
                with open(filepath, 'r') as f:
                    d = json.load(f)
                # TODO: validate against schema
                if isinstance(d, dict):
                    assert 'targets' in d, 'Targets to import/merge should be in json with "targets" as a field in a root-level dict'
                    newTargets = Targets.fromList(d['targets'])
                else:
                    assert isinstance(d, list)
                    newTargets = Targets.fromList(d)
                self.targets.merge(newTargets)
            else:
                raise NotImplementedError()
        else:
            raise NotImplementedError()  # TODO: implement more general merging of .rtnabs files

    @classmethod
    def createNew(cls, filepath: str, unpackedSessionDir: tp.Optional[str] = None):
        self = cls(filepath=filepath, unpackedSessionDir=unpackedSessionDir)
        self.saveToUnpackedDir(saveDirtyOnly=False)
        return self

    @classmethod
    def loadFromFile(cls, filepath: str, unpackedSessionDir: tp.Optional[str] = None):
        if unpackedSessionDir is None:
            unpackedSessionDir = cls.getTempUnpackDir()
        logger.debug('Unpacking archive from {}\nto {}'.format(filepath, unpackedSessionDir))
        assert os.path.exists(filepath)
        shutil.unpack_archive(filepath, unpackedSessionDir, 'zip')
        logger.debug('Done unpacking')
        return cls.loadFromUnpackedDir(unpackedSessionDir=unpackedSessionDir, filepath=filepath,
                                       compressedFileIsDirty=False)

    @classmethod
    def loadFromUnpackedDir(cls, unpackedSessionDir: str, filepath: tp.Optional[str] = None, **kwargs):
        configPath = os.path.join(unpackedSessionDir, cls._sessionConfigFilename + '.json')
        with open(configPath, 'r') as f:
            config = json.load(f)
        # TODO: validate against schema
        assert config['formatVersion'] == cls._latestConfigFormatVersion

        kwargs['unpackedSessionDir'] = unpackedSessionDir
        kwargs['filepath'] = filepath if filepath is not None else config['filepath']
        for key in ('subjectID', 'sessionID'):
            kwargs[key] = config[key]

        otherPathsRelTo = kwargs['filepath']

        if 'MRI' in config:
            configFilename_MRI = config['MRI']
            with open(os.path.join(unpackedSessionDir, configFilename_MRI), 'r') as f:
                kwargs['MRI'] = MRI.fromDict(json.load(f), filepathRelTo=otherPathsRelTo)

        if 'headModel' in config:
            configFilename_headModel = config['headModel']
            with open(os.path.join(unpackedSessionDir, configFilename_headModel), 'r') as f:
                kwargs['headModel'] = HeadModel.fromDict(json.load(f), filepathRelTo=otherPathsRelTo)

        if 'coordinateSystems' in config:
            configFilename_coordinateSystems = config['coordinateSystems']
            with open(os.path.join(unpackedSessionDir, configFilename_coordinateSystems), 'r') as f:
                kwargs['coordinateSystems'] = CoordinateSystems.fromList(json.load(f))

        if 'digitizedLocations' in config:
            configFilename_digitizedLocations = config['digitizedLocations']
            with open(os.path.join(unpackedSessionDir, configFilename_digitizedLocations), 'r') as f:
                kwargs['digitizedLocations'] = DigitizedLocations.fromList(json.load(f))

        if 'subjectRegistration' in config:
            configFilename_subjectReg = config['subjectRegistration']
            with open(os.path.join(unpackedSessionDir, configFilename_subjectReg), 'r') as f:
                kwargs['subjectRegistration'] = SubjectRegistration.fromDict(json.load(f))

        if 'targets' in config:
            configFilename_targets = config['targets']
            with open(os.path.join(unpackedSessionDir, configFilename_targets), 'r') as f:
                kwargs['targets'] = Targets.fromList(json.load(f))

        if 'samples' in config:
            configFilename_samples = config['samples']
            with open(os.path.join(unpackedSessionDir, configFilename_samples), 'r') as f:
                kwargs['samples'] = Samples.fromList(json.load(f))

        if 'tools' in config:
            configFilename_tools = config['tools']
            with open(os.path.join(unpackedSessionDir, configFilename_tools), 'r') as f:
                kwargs['tools'] = Tools.fromList(json.load(f), sessionPath=otherPathsRelTo)

        if 'triggerSources' in config:
            kwargs['triggerSources'] = TriggerSources.fromList(config['triggerSources'])

        if 'addons' in config:
            kwargs['addons'] = Addons.fromList(config['addons'], unpackedSessionDir=unpackedSessionDir)

        # TODO: loop through any addons to give them a chance to load from unpacked dir as needed

        # TODO: load other available fields

        logger.debug('Loaded from unpacked dir:\n{}'.format(kwargs))

        return cls(**kwargs)

    @classmethod
    def getTempUnpackDir(cls):
        return tempfile.TemporaryDirectory(prefix='RTNaBSSession_').name
