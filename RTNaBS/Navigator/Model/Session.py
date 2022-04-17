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
    pass


@attrs.define()
class MNIRegistration:
    pass


@attrs.define()
class SubjectRegistration:
    pass


@attrs.define()
class HeadModel:
    pass

@attrs.define()
class Target:
    pass


@attrs.define()
class Session:
    _filepath: str  # path to compressed session file
    _subjectID: tp.Optional[str] = attrs.field(default=None)
    _sessionID: tp.Optional[str] = None
    MRI: tp.Optional[MRI] = None
    headModel: tp.Optional[HeadModel] = None
    MNIRegistration: tp.Optional[MNIRegistration] = None
    subjectRegistration: tp.Optional[SubjectRegistration] = None
    targets: tp.Dict[str, Target] = None

    _dirtyKeys: tp.Set[str] = attrs.field(init=False, factory=set)
    _compressedFileIsDirty: bool = True

    _sessionConfigFilename: ClassVar[str] = 'SessionConfig.json'
    _unpackedSessionDir: tp.Optional[tp.Union[tempfile.TemporaryDirectory, str]] = attrs.field(default=None)

    sigInfoChanged: Signal = attrs.field(init=False, factory=Signal)

    def __attrs_post_init__(self):
        if self._unpackedSessionDir is None:
            self._unpackedSessionDir = tempfile.TemporaryDirectory(prefix='RTNaBSSession_')

        if not os.path.isdir(self.unpackedSessionDir):
            logger.debug('Creating dir for unpacking session at {}'.format(self.unpackedSessionDir))
            os.makedirs(self.unpackedSessionDir)

        self.sigInfoChanged.connect(lambda: self._dirtyKeys.add('info'))

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
            root_dir=self._unpackedSessionDir,
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
        return cls.loadFromUnpackedDir(unpackedSessionDir=unpackedSessionDir, filepath=filepath)

    @classmethod
    def loadFromUnpackedDir(cls, unpackedSessionDir: str, filepath: tp.Optional[str] = None):
        infoPath = os.path.join(unpackedSessionDir, cls._sessionConfigFilename)
        with open(infoPath, 'r') as f:
            info = json.load(f)
            # TODO: validate against schema

        kwargs = {}
        kwargs['filepath'] = filepath if filepath is not None else info['filepath']
        for key in ('subjectID', 'sessionID'):
            kwargs[key] = info[key]

        # TODO: load other available fields (MRI, etc.)

        return cls(**kwargs)
