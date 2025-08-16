from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING
import logging.handlers
import queue
import atexit

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

    clhHandler = ConcurrentTimedRotatingFileHandler(
        logFilepath,
        when='D', # TODO: debug, set back to 'D'
        interval=1,
        maxBytes=10**8,
    )
    clhHandler.setFormatter(logging.Formatter(
            fmt='%(asctime)s.%(msecs)03d  %(process)6d %(filename)20s %(lineno)4d %(levelname)5s: %(message)s',
            datefmt='%H:%M:%S'))

    clhHandler.setLevel(logging.DEBUG)

    if use_async:
        # To enable background logging queue, call this near the end of your logging setup.
        if False:
            # Configure logging
            # (deprecated queue setup)
            from concurrent_log_handler.queue import setup_logging_queues
            setup_logging_queues()
            handler = clhHandler
        else:
            # Explicit queue setup
            # adapted from https://github.com/Preston-Landers/concurrent-log-handler/blob/fa8f12496aa19f51a8263b2227945caec20e7761/docs/patterns.md?plain=1#L761
            log_queue = queue.Queue(maxsize=10000)

            # Queue handler for non-blocking
            queue_handler = logging.handlers.QueueHandler(log_queue)

            # Listener for background processing
            listener = logging.handlers.QueueListener(log_queue, clhHandler)
            listener.start()
            atexit.register(listener.stop)

            queue_handler.listener = listener

            handler = queue_handler

    return handler





