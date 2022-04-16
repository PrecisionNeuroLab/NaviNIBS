import attrs
import typing as tp


@attrs.define
class MRI:
    pass


@attrs.define
class MNIRegistration:
    pass


@attrs.define
class SubjectRegistration:
    pass


@attrs.define
class Target:
    pass


@attrs.define
class Session:
    subjectID: tp.Optional[str] = None
    sessionID: tp.Optional[str] = None
    MRI: tp.Optional[MRI] = None
    MNIRegistration: tp.Optional[MNIRegistration] = None
    SubjectRegistration: tp.Optional[SubjectRegistration] = None
    targets: tp.Dict[str, Target] = None
