"""
Regression tests for Session's reaction to coordinate system collection
changes. Deleting or renaming a coordinate system emits change signals
after the old key is already gone from the collection, which previously
raised KeyError inside Session._onCoordinateSystemsChanged.

Dirty-flagging is asserted by observing sigDirtyKeysChanged across the
mutation (Session._onCoordinateSystemsChanged is the sole dirty-flagger
for the 'coordinateSystems' key) rather than by mutating or resetting
Session.dirtyKeys, whose contract says it should not be modified.
"""

import numpy as np

from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.Navigator.Model.CoordinateSystems.Affine import AffineTransformedCoordinateSystem


def _makeSession(tmp_path) -> Session:
    sesDir = str(tmp_path / 'ses.navinibsdir')
    return Session.createNew(filepath=sesDir, unpackedSessionDir=sesDir)


def _dirtySignalCounter(ses: Session) -> list:
    emissions = []
    ses.sigDirtyKeysChanged.connect(lambda: emissions.append(True))
    return emissions


def test_deleteCoordinateSystem(tmp_path):
    ses = _makeSession(tmp_path)
    ses.coordinateSystems.addItem(
        AffineTransformedCoordinateSystem(key='TestAffine', transfThisToWorld=np.eye(4)))
    emissions = _dirtySignalCounter(ses)

    ses.coordinateSystems.deleteItem('TestAffine')

    assert 'TestAffine' not in ses.coordinateSystems
    assert len(emissions) > 0, 'deletion should flag session dirty'
    assert 'coordinateSystems' in ses.dirtyKeys


def test_renameCoordinateSystem(tmp_path):
    ses = _makeSession(tmp_path)
    cs = AffineTransformedCoordinateSystem(key='TestAffine', transfThisToWorld=np.eye(4))
    ses.coordinateSystems.addItem(cs)
    emissions = _dirtySignalCounter(ses)

    cs.key = 'RenamedAffine'

    assert 'RenamedAffine' in ses.coordinateSystems
    assert 'TestAffine' not in ses.coordinateSystems
    assert len(emissions) > 0, 'rename should flag session dirty'
    assert 'coordinateSystems' in ses.dirtyKeys
