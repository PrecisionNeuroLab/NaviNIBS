import asyncio
import attrs
import json
import logging
import pylsl as lsl
import socket
import time
import threading
import typing as tp

from . import getKeyForStreamInfo, getEquivalentKeysForStreamInfo, inferStreamKey
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError
from NaviNIBS.util.Signaler import Signal
from NaviNIBS.util.ZMQConnector import ZMQConnectorServer, ZMQConnectorClient, getNewPort


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@attrs.define()
class LSLStreamResolver:
    _resolveStreamsTimeout: float = 0.01  # in s, if this is too short, some streams may be missed

    sigStreamDetected: Signal = attrs.field(init=False, factory=lambda: Signal((str, lsl.StreamInfo)))
    sigStreamLost: Signal = attrs.field(init=False, factory=lambda: Signal((str, lsl.StreamInfo)))

    _availableStreams: tp.Dict[str, lsl.StreamInfo] = attrs.field(init=False, factory=dict)

    def __attrs_post_init__(self):
        pass

    @property
    def availableStreams(self):
        return self._availableStreams

    def updateAvailableStreams(self):
        logger.debug('Resolving available streams')
        streamInfos = lsl.resolve_streams(
            wait_time=self._resolveStreamsTimeout)  # note that if this is too short, not all streams may be returned
        # TODO: set this resolve call to run with longer wait time in a separate thread/process and just send back
        #  stream names (not sure if streamInfo objects themselves are thread-safe) to be used for establishing
        #  connections in main thread
        logger.debug('Found %d streams' % len(streamInfos))
        streamIDs = []
        if True:
            for streamInfo in streamInfos:
                streamID = streamInfo.source_id()
                if len(streamID) == 0:
                    # if no source_id specified, construct unique(ish) key from stream name + hostname
                    # NOTE: could add other metadata into this ID to make more likely to be unique (e.g. num chan, srate)
                    streamID = streamInfo.name() + '_' + streamInfo.hostname()
                else:
                    streamID += '@' + streamInfo.hostname()
                streamIDs.append(streamID)
        else:
            streamIDs = [streamInfo.source_id() for streamInfo in streamInfos]
            assert all(len(streamID) > 0 for streamID in streamIDs)  # all streams should have a source ID set

        logger.debug('StreamIDs: %s' % (streamIDs,))
        assert len(set(streamIDs)) == len(streamIDs), "all stream source IDs should be unique"
        streams = {streamID: streamInfo for streamID, streamInfo in zip(streamIDs, streamInfos)}
        for streamKey in self._availableStreams.copy():
            if streamKey not in streams:
                self._onStreamLost(streamKey)

        for streamKey, streamInfo in streams.items():
            if streamKey not in self._availableStreams:
                self._onStreamDetected(streamKey, streamInfo)

    def _streamInfoAsDict(self, streamKey: str, streamInfo: lsl.StreamInfo) -> tp.Dict[str, tp.Any]:
        return dict(
            key=streamKey,
            name=streamInfo.name(),
            hostname=streamInfo.hostname(),
            sourceID=streamInfo.source_id(),
            type=streamInfo.type()
        )

    def _onStreamDetected(self, streamKey: str, streamInfo: lsl.StreamInfo):
        logger.info('New stream detected: %s' % streamKey)
        assert streamKey not in self._availableStreams
        self._availableStreams[streamKey] = streamInfo
        self.sigStreamDetected.emit(streamKey, streamInfo)

    def _onStreamLost(self, streamKey: str):
        logger.info('Stream lost: %s' % streamKey)
        try:
            assert streamKey in self._availableStreams
        except AssertionError as e:
            raise e
        streamInfo = self._availableStreams.pop(streamKey)
        self.sigStreamLost.emit(streamKey, streamInfo)

    def markStreamAsLost(self, streamKey: str):
        self._onStreamLost(streamKey)


@attrs.define(kw_only=True)
class _ThreadedLSLStreamResolver_Daemon(LSLStreamResolver):
    pollPeriod: float = 1.
    _pubPort: int
    _repPort: int

    _pollTask: tp.Optional[asyncio.Task] = attrs.field(init=False, default=None)

    _connector: ZMQConnectorServer = attrs.field(init=False)

    def __attrs_post_init__(self):
        LSLStreamResolver.__attrs_post_init__(self)
        self._connector = ZMQConnectorServer(obj=self, pubSubPort=self._pubPort, reqRepPort=self._repPort)

        # do not start async polling here; let master start it via connector, to make sure master doesn't miss any messages

    async def _poll(self):
        while True:
            self.updateAvailableStreams()
            await asyncio.sleep(max(self.pollPeriod - self._resolveStreamsTimeout, 0))

    def startAsyncPolling(self):
        if self._pollTask is None:
            self._pollTask = asyncio.create_task(asyncTryAndLogExceptionOnError(self._poll))

    def _onStreamDetected(self, streamKey: str, streamInfo: lsl.StreamInfo):
        LSLStreamResolver._onStreamDetected(self, streamKey=streamKey, streamInfo=streamInfo)
        logger.info('Publishing streamDetected message')
        self._connector.publish([b'streamDetected', json.dumps(self._streamInfoAsDict(
            streamKey=streamKey, streamInfo=streamInfo)).encode('utf-8')])

    def _onStreamLost(self, streamKey: str):
        LSLStreamResolver._onStreamLost(self, streamKey=streamKey)
        logger.info('Publishing streamLost message')
        self._connector.publish([b'streamLost', streamKey.encode('utf-8')])

    def markStreamAsLost(self, streamKey: str):
        if streamKey not in self._availableStreams:
            return  # silently ignore if we already marked this stream as lost
        LSLStreamResolver.markStreamAsLost(self, streamKey=streamKey)

    @classmethod
    async def createAndRun_async(cls, *args, **kwargs):
        self = cls(*args, **kwargs)
        while True:
            await asyncio.sleep(1)

    @classmethod
    def createAndRun(cls, *args, **kwargs):
        asyncio.run(cls.createAndRun_async(*args, **kwargs))


@attrs.define()
class ThreadedLSLStreamResolver(LSLStreamResolver):
    _pollPeriod: float = 1.
    _subPort: tp.Optional[int] = None
    _reqPort: tp.Optional[int] = None

    _thread: threading.Thread = attrs.field(init=False)
    _connector: ZMQConnectorClient = attrs.field(init=False)

    def __attrs_post_init__(self):
        LSLStreamResolver.__attrs_post_init__(self)

        if self._subPort is None:
            self._subPort = getNewPort()

        if self._reqPort is None:
            self._reqPort = getNewPort()

        self._connector = ZMQConnectorClient(
            pubSubPort=self._subPort,
            reqRepPort=self._reqPort,
            onMessagePublished=self._onMessagePublished,
            allowAsyncCalls=True
        )

        self._thread = threading.Thread(target=_ThreadedLSLStreamResolver_Daemon.createAndRun, kwargs=dict(
            pollPeriod=self._pollPeriod,
            pubPort=self._subPort,
            repPort=self._reqPort,
            resolveStreamsTimeout=self._resolveStreamsTimeout
        ), daemon=True)
        self._thread.start()
        asyncio.create_task(asyncTryAndLogExceptionOnError(self._startPolling))

    async def _startPolling(self):
        await asyncio.sleep(self._pollPeriod)  # extra delay here to give time for subscribe to take effect
                                                # TODO: try shortening this delay
        await self._connector.call_async('startAsyncPolling')

    def updateAvailableStreams(self):
        raise NotImplementedError()  # this happens in remote thread, should not be called here

    def _onMessagePublished(self, msg: tp.List[bytes]):
        logger.debug('Received message of type %s' % (msg[0].decode('utf-8'),))
        if msg[0] == b'streamDetected':
            d = json.loads(msg[1].decode('utf-8'))
            streamKey = d['key']
            timeout = 0.01
            infos = []
            while timeout < 10.:  # TODO: don't hardcode this limit
                infos = lsl.resolve_bypred("name='%s' and hostname='%s' and type='%s' and source_id='%s'" %
                                           (d['name'], d['hostname'], d['type'], d['sourceID']),
                                           minimum=1, timeout=timeout)
                if len(infos) == 0:
                    # timed out
                    timeout *= 2  # try with longer timeout
                else:
                    break

            if len(infos) == 0:
                # timed out even after trying long timeout
                logger.error('Remote reported streamDetected but could not resolve, dropping.')
                return

            if len(infos) > 1:
                # multiple streams resolved when we only expected 1
                logger.error('Expected to resolve one stream, but found multiple matches.')
                raise NotImplementedError()

            streamInfo = infos[0]
            self._onStreamDetected(streamKey=streamKey, streamInfo=streamInfo)

        elif msg[0] == b'streamLost':
            streamKey = msg[1].decode('utf-8')
            self._onStreamLost(streamKey=streamKey)

        else:
            logger.error('Unexpected message')
            raise NotImplementedError()

    def markStreamAsLost(self, streamKey: str):
        self._connector.call('markStreamAsLost', streamKey=streamKey)


