from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from NaviNIBS.Navigator.Model.Session import Session


def getLogFilepath(session: Session) -> str:
    return os.path.abspath(os.path.join(session.unpackedSessionDir, 'NaviNIBS_Log.txt'))


def createLogFileHandler(logFilepath: str, use_async: bool = True) -> logging.FileHandler:
    """
    Create a handler for logging to a file, using concurrent_log_handler
    to support multiple processes writing to the same log file.
    """
    from concurrent_log_handler import ConcurrentTimedRotatingFileHandler  # noqa

    handler = ConcurrentTimedRotatingFileHandler(
        logFilepath,
        when='D', # TODO: debug, set back to 'D'
        interval=1,
        maxBytes=10**8,
    )
    handler.setFormatter(logging.Formatter(
            fmt='%(asctime)s.%(msecs)03d  %(process)6d %(filename)20s %(lineno)4d %(levelname)5s: %(message)s',
            datefmt='%H:%M:%S'))

    handler.setLevel(logging.DEBUG)

    if use_async:
        # To enable background logging queue, call this near the end of your logging setup.
        from concurrent_log_handler.queue import setup_logging_queues

        setup_logging_queues()

    return handler





