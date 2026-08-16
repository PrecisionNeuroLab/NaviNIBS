"""
Regression tests for SubjectRegistration derived values.
"""

import numpy as np

from NaviNIBS.Navigator.Model.SubjectRegistration import SubjectRegistration, Fiducial


def test_approxHeadCenterAveragesPlannedCoords():
    """
    Previously averaged the Fiducial objects themselves instead of their
    planned coordinates, raising TypeError whenever LPA and RPA were planned.
    """
    sr = SubjectRegistration()
    sr.fiducials.addItem(Fiducial(key='LPA', plannedCoord=np.array([-80., 0., -40.])))
    sr.fiducials.addItem(Fiducial(key='RPA', plannedCoord=np.array([80., 0., -40.])))

    center = sr.approxHeadCenter
    assert center is not None
    np.testing.assert_allclose(center, np.array([0., 0., -40.]))


def test_approxHeadCenterMissingFiducials():
    sr = SubjectRegistration()
    assert sr.approxHeadCenter is None

    sr.fiducials.addItem(Fiducial(key='LPA', plannedCoord=np.array([-80., 0., -40.])))
    assert sr.approxHeadCenter is None
