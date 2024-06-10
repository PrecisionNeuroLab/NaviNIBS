import asyncio
import json
import logging

import attrs
import numpy as np
import pylsl as lsl
import socket
import time
import threading
import typing as tp


from NaviNIBS.util.ZMQConnector import ZMQConnectorServer, ZMQConnectorClient, getNewPort


logger = logging.getLogger(__name__)


lsl_fmt2string = ['undefined', 'float32', 'double64', 'string', 'int32', 'int16','int8', 'int64']


def getDTypeFromStreamInfo(streamInfo: lsl.StreamInfo) -> np.dtype:
    dataType = lsl_fmt2string[streamInfo.channel_format()]
    if dataType == 'string':
        dtype = 'object'
    elif dataType == 'double64':
        dtype = 'float64'
    else:
        dtype = dataType
    return dtype


def getKeyForStreamInfo(streamInfo: lsl.StreamInfo) -> str:
    # construct what should be a unique key as streamName@hostname
    # (Note that this does not use source_id since that is not reliably known in advance and so wouldn't work for
    #   pre-specifying desired streams. This is in important distinction from the keys used by LSLStreamResolver)
    return '%s@%s' % (streamInfo.name(), streamInfo.hostname())
    # TODO: add support wherever this is used for "localhost" to appear equivalent to actual hostname


def getEquivalentKeysForStreamInfo(streamInfo: lsl.StreamInfo) -> tp.List[str]:
    streamKey = getKeyForStreamInfo(streamInfo)
    equivalentStreamKeys = [streamKey]
    if streamInfo.hostname() == socket.gethostname():
        # allow a local stream by to be specified by name only, or by name@localhost
        equivalentStreamKeys = [streamInfo.name(), '%s@%s' % (streamInfo.name(), 'localhost')] + equivalentStreamKeys
    return equivalentStreamKeys


def inferStreamKey(equivStreamKeys: tp.List[str], preferredStreamKeys: tp.Iterable[str]) -> str:
    if any(equivStreamKey in preferredStreamKeys for equivStreamKey in equivStreamKeys):
        matchingKeys = [equivStreamKey for equivStreamKey in equivStreamKeys if
                        equivStreamKey in preferredStreamKeys]
        if len(matchingKeys) > 1:
            # multiple matching keys
            raise KeyError()
        return matchingKeys[0]
    else:
        return equivStreamKeys[0]  # if no matches, prefer first equivalent key

