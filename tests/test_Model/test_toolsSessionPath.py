"""
Regression tests for change signaling when a tool or tool collection's
session path changes (e.g. after save-as / session move).
"""

from NaviNIBS.Navigator.Model.Tools import Tool, Tools


def test_toolSessionPathChangeSignalsFilepathAttribs():
    """
    A missing comma previously merged 'trackerStlFilepath' and 'sessionPath'
    into one bogus attrib name in the signaled attrib list.
    """
    tool = Tool(key='t1', usedFor='visualization',
                filepathsRelTo='<session>', sessionPath='/tmp/sesA')
    received = []
    tool.sigItemAboutToChange.connect(lambda key, attribs=None: received.append(list(attribs)))
    tool.sessionPath = '/tmp/sesB'

    assert received, 'sessionPath change did not signal'
    attribs = received[0]
    assert 'trackerStlFilepathsessionPath' not in attribs
    assert 'trackerStlFilepath' in attribs
    assert 'sessionPath' in attribs


def test_toolsCollectionSessionPathChangeSignalsSessionRelativeTools():
    """
    The collection-level setter previously compared the resolved
    ``filepathsRelTo`` path against the literal token '<session>', so
    session-relative tools were never included in the signaled keys.
    """
    tools = Tools(sessionPath='/tmp/sesA')
    tools.addItem(Tool(key='rel', usedFor='visualization',
                       filepathsRelTo='<session>', sessionPath='/tmp/sesA'))
    tools.addItem(Tool(key='abs', usedFor='visualization',
                       filepathsRelTo='<userDataDir>', sessionPath='/tmp/sesA'))

    receivedAboutTo = []
    receivedChanged = []
    tools.sigItemsAboutToChange.connect(
        lambda keys, attribs=None: receivedAboutTo.append((list(keys), attribs)))
    tools.sigItemsChanged.connect(
        lambda keys, attribs=None: receivedChanged.append((list(keys), attribs)))

    tools.sessionPath = '/tmp/sesB'

    assert receivedAboutTo and receivedChanged
    assert receivedAboutTo[0][0] == ['rel']
    assert receivedChanged[0][0] == ['rel']
    assert 'sessionPath' in receivedChanged[0][1]
