from __future__ import annotations
import attrs


@attrs.define(frozen=True)
class ActorRef:
    """
    vtk Actors can't be pickled, so we use this class to pass to remote processes,
    and use ActorManager to keep track of which ref refers to which actor locally.
    """
    actorID: str


