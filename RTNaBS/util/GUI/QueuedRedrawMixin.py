import asyncio
import attrs
import logging
import typing as tp

from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@attrs.define(slots=False, kw_only=True)
class QueuedRedrawMixin:
    """
    Convenience mixin for classes with a _redraw method, to support "queueing" redraws.
    Especially useful for queueing redraws spawned by Qt events but that we want to handle
    in async loop, and for reducing redundant redraws.
    """
    _redrawQueue: list[str | tuple[str, dict]] = attrs.field(init=False, factory=list)
    _redrawQueueModified: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    def __attrs_post_init__(self):
        asyncio.create_task(asyncTryAndLogExceptionOnError(self._loop_queuedRedraw))

    async def _loop_queuedRedraw(self):
        while True:
            await self._redrawQueueModified.wait()
            await asyncio.sleep(0.01)  # rate limit  # TODO: make this a parameter
            while len(self._redrawQueue) > 0:
                toRedraw = self._redrawQueue.pop(0)
                logger.debug(f'Dequeuing redraw for {self.__class__.__name__} {toRedraw}')
                if isinstance(toRedraw, str):
                    self._redraw(which=toRedraw)
                else:
                    # tuple of (which, kwargs)
                    assert len(toRedraw) == 2
                    self._redraw(which=toRedraw[0], **toRedraw[1])
            self._redrawQueueModified.clear()
            await asyncio.sleep(0.01)  # rate limit  # TODO: make this a parameter

    def _queueRedraw(self, which: tp.Union[tp.Optional[str], tp.List[str]] = None, **kwargs):

        if False:  # TODO: debug, delete or set to False
            self._redraw(which=which, **kwargs)
            return

        if which is None:
            which = 'all'

        if not isinstance(which, str):
            for subWhich in which:
                self._queueRedraw(which=subWhich, **kwargs)
            return

        if which == 'all':
            self._redrawQueue.clear()
        else:
            if 'all' in self._redrawQueue:
                return
            if which in self._redrawQueue:
                return  # don't need to add duplicate to queue
                # (assumes order of redraws doesn't matter)

        if len(kwargs) == 0:
            queueKey = which
        else:
            queueKey = (which, kwargs)
        logger.debug(f'Queueing redraw for {self.__class__.__name__} {queueKey}')
        self._redrawQueue.append(queueKey)
        self._redrawQueueModified.set()

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str]] = None, **kwargs):
        if isinstance(which, str):
            # since we're redrawing now, can remove this from the queue
            if len(kwargs) == 0:
                queueKey = which
            else:
                queueKey = (which, kwargs)
            try:
                self._redrawQueue.remove(queueKey)
            except ValueError:
                pass
            else:
                self._redrawQueueModified.set()

        # subclass should handle the rest