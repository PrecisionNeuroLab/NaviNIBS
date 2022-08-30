from __future__ import annotations

import shutil

import attrs
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
from RTNaBS.Navigator.Model.Targets import Targets, Target
from RTNaBS.Navigator.Model.Samples import Samples, Sample
from RTNaBS.Navigator.Model.SubjectRegistration import SubjectRegistration
from RTNaBS.Navigator.Model.Tools import Tools, Tool, CoilTool, Pointer, SubjectTracker, CalibrationPlate
from RTNaBS.Navigator.Model.Triggering import TriggerSources
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
    MNIRegistration: tp.Optional[MNIRegistration] = None
    _targets: Targets = attrs.field(factory=Targets)
    _tools: Tools = attrs.field(default=None)
    _samples: Samples = attrs.field(factory=Samples)
    _triggerSources: TriggerSources = attrs.field(factory=TriggerSources)
    _addons: Addons = attrs.field(factory=Addons)

    _dirtyKeys: tp.Set[str] = attrs.field(init=False, factory=set)  # session info parts that have changed since last save
    _dirtyKeys_autosave: tp.Set[str] = attrs.field(init=False, factory=set)  # session info parts that have changed since last autosave
    _compressedFileIsDirty: bool = True

    _sessionConfigFilename: ClassVar[str] = 'SessionConfig'
    _lastAutosaveFilenamePrefix: tp.Optional[str] = None
    _latestConfigFormatVersion: ClassVar[str] = '0.0.1'
    _unpackedSessionDir: tp.Optional[tp.Union[tempfile.TemporaryDirectory, str]] = attrs.field(default=None)

    sigInfoChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        if self._unpackedSessionDir is None:
            self._unpackedSessionDir = self.getTempUnpackDir()

        if not os.path.isdir(self.unpackedSessionDir):
            logger.debug('Creating dir for unpacking session at {}'.format(self.unpackedSessionDir))
            os.makedirs(self.unpackedSessionDir)

        if self._tools is None:
            self._tools = Tools(sessionPath=os.path.dirname(self._filepath))

        self.sigInfoChanged.connect(lambda: self.flagKeyAsDirty('info'))
        self.MRI.sigFilepathChanged.connect(lambda: self.flagKeyAsDirty('MRI'))
        self.headModel.sigFilepathChanged.connect(lambda: self.flagKeyAsDirty('headModel'))
        self.subjectRegistration.sigPlannedFiducialsChanged.connect(lambda: self.flagKeyAsDirty('subjectRegistration'))
        self.subjectRegistration.sigSampledFiducialsChanged.connect(lambda: self.flagKeyAsDirty('subjectRegistration'))
        self.subjectRegistration.sigSampledHeadPointsChanged.connect(lambda: self.flagKeyAsDirty('subjectRegistration'))
        self.subjectRegistration.sigTrackerToMRITransfChanged.connect(lambda: self.flagKeyAsDirty('subjectRegistration'))
        self.targets.sigTargetsChanged.connect(lambda targetKeys, attribKeys: self.flagKeyAsDirty('targets'))
        self.samples.sigSamplesChanged.connect(lambda sampleTimestamps, attribKeys: self.flagKeyAsDirty('samples'))
        self.tools.sigToolsChanged.connect(lambda toolKeys: self.flagKeyAsDirty('tools'))
        self.tools.sigPositionsServerInfoChanged.connect(lambda infoKeys: self.flagKeyAsDirty('tools'))
        self.triggerSources.sigTriggerSettingChanged.connect(lambda sourceKey: self.flagKeyAsDirty('triggerSources'))
        self.addons.sigAddonsChanged.connect(lambda addonKeys, attribKeys: self.flagKeyAsDirty('addons'))

        # TODO

    @property
    def subjectID(self):
        return self._subjectID

    @subjectID.setter
    def subjectID(self, newVal: str):
        if newVal != self._subjectID:
            self._subjectID = newVal
            self.sigInfoChanged.emit()

    @property
    def sessionID(self):
        return self._sessionID

    @sessionID.setter
    def sessionID(self, newVal: str):
        if newVal != self._sessionID:
            self._sessionID = newVal
            self.sigInfoChanged.emit()

    @property
    def filepath(self):
        return self._filepath

    @filepath.setter
    def filepath(self, newVal: str):
        if newVal != self._filepath:
            self._filepath = newVal
            self.sigInfoChanged.emit()
            self.tools.sessionPath = os.path.dirname(self._filepath)

    @property
    def MRI(self):
        return self._MRI

    @property
    def headModel(self):
        return self._headModel

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

        otherPathsRelTo = os.path.dirname(self.filepath)

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
            config['triggerSources'] = self.triggerSources.asDict()
            keysToSave.discard('triggerSources')

        # TODO: loop through any addons to give them a chance to save to config as needed

        if 'addons' in keysToSave or not saveDirtyOnly:
            logger.debug('Writing addons info')
            config['addons'] = self.addons.asList()
            keysToSave.discard('addons')

        # TODO: save other fields
        assert len(keysToSave) == 0

        with open(configPath, 'w') as f:
            if False:
                json.dump(config, f)
            else:
                f.write(jsonPrettyDumps(config))
            logger.debug('Wrote updated session config')

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

        otherPathsRelTo = os.path.dirname(kwargs['filepath'])

        if 'MRI' in config:
            configFilename_MRI = config['MRI']
            with open(os.path.join(unpackedSessionDir, configFilename_MRI), 'r') as f:
                kwargs['MRI'] = MRI.fromDict(json.load(f), filepathRelTo=otherPathsRelTo)

        if 'headModel' in config:
            configFilename_headModel = config['headModel']
            with open(os.path.join(unpackedSessionDir, configFilename_headModel), 'r') as f:
                kwargs['headModel'] = HeadModel.fromDict(json.load(f), filepathRelTo=otherPathsRelTo)

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
            kwargs['triggerSources'] = TriggerSources.fromDict(config['triggerSources'])

        if 'addons' in config:
            kwargs['addons'] = Addons.fromList(config['addons'])

        # TODO: loop through any addons to give them a chance to load from unpacked dir as needed

        # TODO: load other available fields

        logger.debug('Loaded from unpacked dir:\n{}'.format(kwargs))

        return cls(**kwargs)

    @classmethod
    def getTempUnpackDir(cls):
        return tempfile.TemporaryDirectory(prefix='RTNaBSSession_').name
