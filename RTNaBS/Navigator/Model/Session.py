from __future__ import annotations

import shutil

import attrs
import json
import logging
import numpy as np
import os
import tempfile
import typing as tp
from typing import ClassVar

from RTNaBS.util.Signaler import Signal


logger = logging.getLogger(__name__)


@attrs.define()
class MRI:
    _filepath: tp.Optional[str] = None

    sigFilepathChanged: Signal = attrs.field(init=False, factory=Signal)
    sigDataChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        self.sigFilepathChanged.connect(self.sigDataChanged.emit)
        self.validateFilepath(self._filepath)

    @property
    def filepath(self):
        return self._filepath

    @filepath.setter
    def filepath(self, newPath: str):
        if self._filepath == newPath:
            return
        self.validateFilepath(newPath)
        self._filepath = newPath
        self.sigFilepathChanged.emit()
        # TODO: here or with slots connected to sigDataChanged, make sure any cached MRI data or metadata is cleared/reloaded

    @property
    def isSet(self):
        return self._filepath is not None

    def asDict(self, filepathRelTo: str) -> tp.Dict[str, tp.Any]:
        d = dict(
            filepath=self._filepath
        )
        if d['filepath'] is not None:
            d['filepath'] = os.path.relpath(d['filepath'], filepathRelTo)

        return d

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any], filepathRelTo: str) -> MRI:
        # TODO: validate against schema
        if 'filepath' in d:
            d['filepath'] = os.path.join(filepathRelTo, d['filepath'])
            cls.validateFilepath(d['filepath'])
        return cls(**d)

    @classmethod
    def validateFilepath(cls, filepath: tp.Optional[str]) -> None:
        if filepath is None:
            return
        assert filepath.endswith('.nii') or filepath.endswith('.nii.gz')
        assert os.path.exists(filepath), 'File not found at {}'.format(filepath)

@attrs.define()
class MNIRegistration:
    pass


@attrs.define()
class SubjectRegistration:
    pass


@attrs.define()
class HeadModel:
    _filepath: tp.Optional[str] = None  # path to .msh file in simnibs folder
    # (note that .msh file and other nested files in same parent dir will be used)

    sigFilepathChanged: Signal = attrs.field(init=False, factory=Signal)
    sigDataChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        self.sigFilepathChanged.connect(self.sigDataChanged.emit)
        self.validateFilepath(self._filepath)

    @property
    def filepath(self):
        return self._filepath

    @filepath.setter
    def filepath(self, newPath: str):
        if self._filepath == newPath:
            return
        self.validateFilepath(newPath)
        self._filepath = newPath
        self.sigFilepathChanged.emit()
        # TODO: here or with slots connected to sigDataChanged, make sure any cached MRI data or metadata is cleared/reloaded

    @property
    def isSet(self):
        return self._filepath is not None

    def asDict(self, filepathRelTo: str) -> tp.Dict[str, tp.Any]:
        d = dict(
            filepath=self._filepath
        )
        if d['filepath'] is not None:
            d['filepath'] = os.path.relpath(d['filepath'], filepathRelTo)

        return d

    @classmethod
    def fromDict(cls, d: tp.Dict[str, tp.Any], filepathRelTo: str) -> HeadModel:
        # TODO: validate against schema
        if 'filepath' in d:
            d['filepath'] = os.path.join(filepathRelTo, d['filepath'])
            cls.validateFilepath(d['filepath'])
        return cls(**d)

    @classmethod
    def validateFilepath(cls, filepath: tp.Optional[str]) -> None:
        if filepath is None:
            return
        assert filepath.endswith('.msh')
        assert os.path.exists(filepath), 'File not found at {}'.format(filepath)
        # TODO: also verify that expected related files (e.g. m2m_* folder) are next to the referenced .msh filepath

@attrs.define()
class Target:
    pass


@attrs.define()
class Session:
    _filepath: str  # path to compressed session file
    _subjectID: tp.Optional[str] = attrs.field(default=None)
    _sessionID: tp.Optional[str] = None
    _MRI: MRI = attrs.field(factory=MRI)
    _headModel: HeadModel = attrs.field(factory=HeadModel)
    MNIRegistration: tp.Optional[MNIRegistration] = None
    subjectRegistration: tp.Optional[SubjectRegistration] = None
    targets: tp.Dict[str, Target] = None

    _dirtyKeys: tp.Set[str] = attrs.field(init=False, factory=set)
    _compressedFileIsDirty: bool = True

    _sessionConfigFilename: ClassVar[str] = 'SessionConfig.json'
    _latestConfigFormatVersion: ClassVar[str] = '0.0.1'
    _unpackedSessionDir: tp.Optional[tp.Union[tempfile.TemporaryDirectory, str]] = attrs.field(default=None)

    sigInfoChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        if self._unpackedSessionDir is None:
            self._unpackedSessionDir = tempfile.TemporaryDirectory(prefix='RTNaBSSession_')

        if not os.path.isdir(self.unpackedSessionDir):
            logger.debug('Creating dir for unpacking session at {}'.format(self.unpackedSessionDir))
            os.makedirs(self.unpackedSessionDir)

        self.sigInfoChanged.connect(lambda: self._dirtyKeys.add('info'))
        self.MRI.sigFilepathChanged.connect(lambda: self._dirtyKeys.add('MRI'))
        self.headModel.sigFilepathChanged.connect(lambda: self._dirtyKeys.add('headModel'))

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

    @property
    def MRI(self):
        return self._MRI

    @property
    def headModel(self):
        return self._headModel

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

    @classmethod
    def createNew(cls, filepath: str, unpackedSessionDir: tp.Optional[str] = None):
        self = cls(filepath=filepath, unpackedSessionDir=unpackedSessionDir)
        self.saveToUnpackedDir(saveDirtyOnly=False)
        return self

    @classmethod
    def loadFromFile(cls, filepath: str, unpackedSessionDir: tp.Optional[str] = None):
        logger.debug('Unpacking archive from {}\nto {}'.format(filepath, unpackedSessionDir))
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

        # TODO: load other available fields

        logger.debug('Loaded from unpacked dir:\n{}'.format(kwargs))

        return cls(**kwargs)
