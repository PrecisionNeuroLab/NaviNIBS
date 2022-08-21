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
from RTNaBS.Navigator.Model.Addons import Addons, Addon
from RTNaBS.util.Signaler import Signal
from RTNaBS.util.numpy import array_equalish


logger = logging.getLogger(__name__)


@attrs.define
class MNIRegistration:
    pass


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
    _addons: Addons = attrs.field(factory=Addons)

    _dirtyKeys: tp.Set[str] = attrs.field(init=False, factory=set)
    _compressedFileIsDirty: bool = True

    _sessionConfigFilename: ClassVar[str] = 'SessionConfig.json'
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

        self.sigInfoChanged.connect(lambda: self._dirtyKeys.add('info'))
        self.MRI.sigFilepathChanged.connect(lambda: self._dirtyKeys.add('MRI'))
        self.headModel.sigFilepathChanged.connect(lambda: self._dirtyKeys.add('headModel'))
        self.subjectRegistration.sigPlannedFiducialsChanged.connect(lambda: self._dirtyKeys.add('subjectRegistration'))
        self.subjectRegistration.sigSampledFiducialsChanged.connect(lambda: self._dirtyKeys.add('subjectRegistration'))
        self.subjectRegistration.sigSampledHeadPointsChanged.connect(lambda: self._dirtyKeys.add('subjectRegistration'))
        self.subjectRegistration.sigTrackerToMRITransfChanged.connect(lambda: self._dirtyKeys.add('subjectRegistration'))
        self.targets.sigTargetsChanged.connect(lambda targetKeys, attribKeys: self._dirtyKeys.add('targets'))
        self.samples.sigSamplesChanged.connect(lambda sampleTimestamps, attribKeys: self._dirtyKeys.add('samples'))
        self.tools.sigToolsChanged.connect(lambda toolKeys: self._dirtyKeys.add('tools'))
        self.tools.sigPositionsServerInfoChanged.connect(lambda infoKeys: self._dirtyKeys.add('tools'))
        self.addons.sigAddonsChanged.connect(lambda addonKeys, attribKeys: self._dirtyKeys.add('addons'))

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

    def saveToUnpackedDir(self, saveDirtyOnly: bool = True):

        keysToSave = self._dirtyKeys.copy()

        if not saveDirtyOnly:
            keysToSave.update(['info'])

        if len(keysToSave) == 0:
            logger.debug('Nothing to save')
            return

        self._compressedFileIsDirty = True

        configPath = os.path.join(self.unpackedSessionDir, self._sessionConfigFilename)
        if os.path.exists(configPath):
            with open(configPath, 'r') as f:
                config = json.load(f)
            assert config['formatVersion'] == self._latestConfigFormatVersion
        else:
            config = dict(formatVersion=self._latestConfigFormatVersion)

        if 'info' in keysToSave:
            logger.debug('Writing session info')
            infoFields = ('filepath', 'subjectID', 'sessionID')
            for field in infoFields:
                config[field] = getattr(self, field)
            keysToSave.remove('info')

        otherPathsRelTo = os.path.dirname(self.filepath)

        if 'MRI' in keysToSave:
            # save MRI path relative to location of compressed file
            logger.debug('Writing MRI info')
            config['MRI'] = self.MRI.asDict(filepathRelTo=otherPathsRelTo)
            keysToSave.remove('MRI')

        if 'headModel' in keysToSave:
            # save head model path relative to location of compressed file
            logger.debug('Writing headModel info')
            config['headModel'] = self.headModel.asDict(filepathRelTo=otherPathsRelTo)
            keysToSave.remove('headModel')

        if 'subjectRegistration' in keysToSave:
            logger.debug('Writing subjectRegistration info')
            config['subjectRegistration'] = self.subjectRegistration.asDict()
            # TODO: save contents of potentially larger *History fields to separate file(s)
            keysToSave.remove('subjectRegistration')

        if 'targets' in keysToSave:
            logger.debug('Writing targets info')
            config['targets'] = self.targets.asList()
            keysToSave.remove('targets')

        if 'samples' in keysToSave:
            logger.debug('Writing samples info')
            config['samples'] = self.samples.asList()
            keysToSave.remove('samples')

        if 'tools' in keysToSave:
            logger.debug('Writing tools info')
            config['tools'] = self.tools.asList()
            keysToSave.remove('tools')

        # TODO: loop through any addons to give them a chance to save to config as needed

        if 'addons' in keysToSave:
            logger.debug('Writing addons info')
            config['addons'] = self.addons.asList()
            keysToSave.remove('addons')

        # TODO: save other fields
        assert len(keysToSave) == 0

        with open(configPath, 'w') as f:
            json.dump(config, f)
            logger.debug('Wrote updated session config')

        self._dirtyKeys.clear()

    def saveToFile(self):
        self.saveToUnpackedDir()
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
                assert 'targets' in d, 'Targets to import/merge should be in json with "targets" as a field in a root-level dict'
                newTargets = Targets.fromList(d['targets'])
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
        configPath = os.path.join(unpackedSessionDir, cls._sessionConfigFilename)
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
            kwargs['MRI'] = MRI.fromDict(config['MRI'], filepathRelTo=otherPathsRelTo)

        if 'headModel' in config:
            kwargs['headModel'] = HeadModel.fromDict(config['headModel'], filepathRelTo=otherPathsRelTo)

        if 'subjectRegistration' in config:
            kwargs['subjectRegistration'] = SubjectRegistration.fromDict(config['subjectRegistration'])

        if 'targets' in config:
            kwargs['targets'] = Targets.fromList(config['targets'])

        if 'samples' in config:
            kwargs['samples'] = Samples.fromList(config['samples'])

        if 'tools' in config:
            kwargs['tools'] = Tools.fromList(config['tools'], sessionPath=otherPathsRelTo)

        if 'addons' in config:
            kwargs['addons'] = Addons.fromList(config['addons'])

        # TODO: loop through any addons to give them a chance to load from unpacked dir as needed

        # TODO: load other available fields

        logger.debug('Loaded from unpacked dir:\n{}'.format(kwargs))

        return cls(**kwargs)

    @classmethod
    def getTempUnpackDir(cls):
        return tempfile.TemporaryDirectory(prefix='RTNaBSSession_').name
