"""
Regression test for the RemotePlotterProxy sentinel used on platforms
where remote plotting is unavailable (currently macOS).
"""

import platform

import pytest

from NaviNIBS.util.pyvista import DefaultBackgroundPlotter, RemotePlotterProxy


@pytest.mark.skipif(platform.system() != 'Darwin',
                    reason='sentinel branch only taken on macOS')
def test_remotePlotterSentinelIsNotStr():
    """
    ``type('__None')`` evaluates to ``str``, so string values would satisfy
    ``isinstance(x, RemotePlotterProxy)`` checks; the sentinel must be a
    dedicated class.
    """
    assert RemotePlotterProxy is not str
    assert not isinstance('some string', RemotePlotterProxy)
    assert not isinstance(DefaultBackgroundPlotter, RemotePlotterProxy)
