from __future__ import annotations

import shutil

import attrs
import json
import logging
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

    @property
    def filepath(self):
        return self._filepath

    @filepath.setter
    def filepath(self, newPath: str):
        if self._filepath == newPath:
            return
        assert os.path.exists(newPath), 'File not found at {}'.format(newPath)
        self._filepath = newPath
        self.sigFilepathChanged.emit()
        # TODO: here or with slots connected to sigDataChanged, make sure any cached MRI data or metadata is cleared/reloaded

    @property
    def isSet(self):
        return self._filepath is not None


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

    @property
    def filepath(self):
        return self._filepath

    @filepath.setter
    def filepath(self, newPath: str):
        if self._filepath == newPath:
            return
        assert os.path.exists(newPath), 'File not found at {}'.format(newPath)
        self._filepath = newPath
        self.sigFilepathChanged.emit()
        # TODO: here or with slots connected to sigDataChanged, make sure any cached MRI data or metadata is cleared/reloaded

    @property
    def isSet(self):
        return self._filepath is not None

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
    _MRIConfigFilename: ClassVar[str] = 'MRIConfig.json'
    _headModelConfigFilename: ClassVar[str] = 'HeadModelConfig.json'
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
        self._dirtyKeys.clear()

        if not saveDirtyOnly:
            keysToSave.update(['info'])

        if len(keysToSave) == 0:
            logger.debug('Nothing to save')
            return

        self._compressedFileIsDirty = True

        if 'info' in keysToSave:
            infoFields = ('filepath', 'subjectID', 'sessionID')
            toSave = {}
            for field in infoFields:
                toSave[field] = getattr(self, field)
            infoPath = os.path.join(self.unpackedSessionDir, self._sessionConfigFilename)
            with open(infoPath, 'w') as f:
                json.dump(toSave, f)
            logger.debug('Saved session info to {}'.format(infoPath))
            keysToSave.remove('info')

        otherPathsRelTo = os.path.dirname(self.filepath)

        if 'MRI' in keysToSave:
            # save MRI path relative to location of compressed file
            if self.MRI.filepath is None:
                mriRelPath = None
            else:
                mriRelPath = os.path.relpath(self.MRI.filepath, otherPathsRelTo)
                toSave = dict(
                    filepath=mriRelPath
                )
                mriMetaPath = os.path.join(self.unpackedSessionDir, self._MRIConfigFilename)
                # TODO: add user-selectable option on whether to save MRI file itself into compressed rtnabs file or not
                # for now, do not include MRI file itself, just a relative path reference
                with open(mriMetaPath, 'w') as f:
                    json.dump(toSave, f)
                logger.debug('Saved MRI config to {}'.format(mriMetaPath))
                keysToSave.remove('MRI')

        if 'headModel' in keysToSave:
            # save head model path relative to location of compressed file
            if self.headModel.filepath is None:
                headModelRelPath = None
            else:
                headModelRelPath = os.path.relpath(self.headModel.filepath, otherPathsRelTo)
                toSave = dict(
                    filepath=headModelRelPath
                )
                headModelMetaPath = os.path.join(self.unpackedSessionDir, self._headModelConfigFilename)
                # TODO: add user-selectable option on whether to save headModel file itself into compressed rtnabs file or not
                # for now, do not include headModel file itself, just a relative path reference
                with open(headModelMetaPath, 'w') as f:
                    json.dump(toSave, f)
                logger.debug('Saved headModel config to {}'.format(headModelMetaPath))
                keysToSave.remove('headModel')

        # TODO: save other fields
        assert len(keysToSave) == 0

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
        logger.debug('Unpacking archive from {}'.format(filepath))
        shutil.unpack_archive(filepath, unpackedSessionDir, 'zip')
        logger.debug('Done unpacking')
        return cls.loadFromUnpackedDir(unpackedSessionDir=unpackedSessionDir, filepath=filepath,
                                       compressedFileIsDirty=False)

    @classmethod
    def loadFromUnpackedDir(cls, unpackedSessionDir: str, filepath: tp.Optional[str] = None, **kwargs):
        infoPath = os.path.join(unpackedSessionDir, cls._sessionConfigFilename)
        with open(infoPath, 'r') as f:
            info = json.load(f)
            # TODO: validate against schema

        kwargs['unpackedSessionDir'] = unpackedSessionDir
        kwargs['filepath'] = filepath if filepath is not None else info['filepath']
        for key in ('subjectID', 'sessionID'):
            kwargs[key] = info[key]

        otherPathsRelTo = os.path.dirname(kwargs['filepath'])

        mriMetaPath = os.path.join(unpackedSessionDir, cls._MRIConfigFilename)
        if os.path.exists(mriMetaPath):
            with open(mriMetaPath, 'r') as f:
                info = json.load(f)
                # TODO: validate against schema
                mriFilepath = os.path.join(otherPathsRelTo, info['filepath'])
                assert os.path.exists(mriFilepath), 'MRI not found at {}'.format(mriFilepath)
                kwargs['MRI'] = MRI(filepath=mriFilepath)

        headModelMetaPath = os.path.join(unpackedSessionDir, cls._headModelConfigFilename)
        if os.path.exists(headModelMetaPath):
            with open(headModelMetaPath, 'r') as f:
                info = json.load(f)
                # TODO: validate against schema
                headModelFilepath = os.path.join(otherPathsRelTo, '..', info['filepath'])
                assert os.path.exists(headModelFilepath), 'headModel not found at {}'.format(headModelFilepath)
                kwargs['headModel'] = HeadModel(filepath=headModelFilepath)

        # TODO: load other available fields (head model, etc.)

        logger.debug('Loaded from unpacked dir:\n{}'.format(kwargs))

        return cls(**kwargs)
