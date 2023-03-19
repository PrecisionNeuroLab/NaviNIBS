import asyncio
import attrs
import logging
import numpy as np
import pyigtl
import typing as tp
from typing import ClassVar

from RTNaBS.Devices.ToolPositionsServer import ToolPositionsServer, TimestampedToolPosition


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@attrs.define
class IGTLinkToolPositionsServer(ToolPositionsServer):
    """
    This acts as a central router for IGTLink position updates.

    It actually acts as a client connecting to a running Plus Server that is itself streaming tool positions.
    But this provides other clients a connection-agnostic async interface for receiving updates.
    """

    _type: ClassVar[str] = 'IGTLink'

    _igtlHostname: str = '127.0.0.1'
    _igtlPort: int = 18944
    _igtlClient: pyigtl.OpenIGTLinkClient = attrs.field(init=False)
    _igtlPollPeriod: float = 0.01

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        logger.info('Initializing IGTLink client')
        self._igtlClient = pyigtl.OpenIGTLinkClient(host=self._igtlHostname, port=self._igtlPort, start_now=False)

    async def run(self):
        logger.info('Starting IGTLink client')
        self._igtlClient.start()
        await asyncio.sleep(1.)  # give some time for client to connect
        logger.info('Starting IGTLink polling')
        while True:
            msgs = self._igtlClient.get_latest_messages()
            if len(msgs) == 0:
                logger.debug('No new IGTLink messages')
                await asyncio.sleep(self._igtlPollPeriod)
            else:
                logger.debug('{} new IGTLink messages'.format(len(msgs)))
                async with self._publishingLatestLock:  # delay publishing until after handling all these messages
                    for msg in msgs:
                        if msg.message_type == 'TRANSFORM':
                            transf = msg.matrix
                            if np.allclose(transf, np.eye(4)):
                                # plus sends identity when transforms are invalid
                                logger.debug('Transform for {} is invalid'.format(msg.device_name))
                                transf = None

                            position = TimestampedToolPosition(time=msg.timestamp,
                                                               transf=transf)
                            key = msg.device_name
                            if key.endswith('ToTracker'):
                                # strip 'ToTracker' suffix from device_name, assuming plus config is set to only send *ToTracker transforms
                                key = key[:-len('ToTracker')]
                            else:
                                raise KeyError('Unexpected position device_name: {}'.format(key))

                            logger.debug(f'Transform for key {key}')
                            await self.recordNewPosition(key=key, position=position)
                        else:
                            logger.error('Unexpected message type: {}'.format(msg.message_type))
                            raise NotImplementedError()
                await asyncio.sleep(0)


if __name__ == '__main__':
    IGTLinkToolPositionsServer.createAndRun()