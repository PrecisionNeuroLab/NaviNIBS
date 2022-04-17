import attrs
import tempfile
import typing as tp


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
class Target:
    pass


@attrs.define()
class Session:
    filepath: str  # path to compressed session file
    subjectID: tp.Optional[str] = None
    sessionID: tp.Optional[str] = None
    MRI: tp.Optional[MRI] = None
    MNIRegistration: tp.Optional[MNIRegistration] = None
    SubjectRegistration: tp.Optional[SubjectRegistration] = None
    targets: tp.Dict[str, Target] = None

    sessionConfigFilename: str = 'SessionConfig.json'
    _unpackedSessionDir: tempfile.TemporaryDirectory = attrs.field(init=False, default=None)

    def __attrs_post_init__(self):



        raise NotImplementedError()  # TODO

    def saveToUnpackedDir(self, saveDirtyOnly: bool = True):
        raise NotImplementedError()  # TODO

    @classmethod
    def loadFromFile(cls, sessionFilepath: str, unpackToDir: str):
        raise NotImplementedError()  # TODO

    @classmethod
    def loadFromUnpackedDir(cls, unpackedSessionDir: str):
        raise NotImplementedError()  # TODO