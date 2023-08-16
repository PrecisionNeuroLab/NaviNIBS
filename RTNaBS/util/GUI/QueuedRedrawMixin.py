import asyncio
import attrs
import typing as tp

from RTNaBS.util.Asyncio import asyncTryAndLogExceptionOnError


@attrs.define(slots=False, kw_only=True)
class QueuedRedrawMixin:
    """
    Convenience mixin for classes with a _redraw method, to support "queueing" redraws.
    Especially useful for queueing redraws spawned by Qt events but that we want to handle
    in async loop, and for reducing redundant redraws.
    """
    _redrawQueue: list[str] = attrs.field(init=False, factory=list)
    _redrawQueueModified: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    def __attrs_post_init__(self):
        asyncio.create_task(asyncTryAndLogExceptionOnError(self._loop_queuedRedraw))

    async def _loop_queuedRedraw(self):
        while True:
            await self._redrawQueueModified.wait()
            self._redrawQueueModified.clear()
            if len(self._redrawQueue) > 0:
                toRedraw = self._redrawQueue.pop(0)
                self._redraw(which=toRedraw)

    def _queueRedraw(self, which: tp.Union[tp.Optional[str], tp.List[str]] = None):
        if which is None:
            which = 'all'

        if not isinstance(which, str):
            for subWhich in which:
                self._queueRedraw(which=subWhich)
            return

        if which == 'all':
            self._redrawQueue.clear()
        else:
            if 'all' in self._redrawQueue:
                return
            if which in self._redrawQueue:
                return  # don't need to add duplicate to queue
                # (assumes order of redraws doesn't matter)

        self._redrawQueue.append(which)
        self._redrawQueueModified.set()

    def _redraw(self, which: tp.Union[tp.Optional[str], tp.List[str]] = None):
        if isinstance(which, str):
            # since we're redrawing now, can remove this from the queue
            try:
                self._redrawQueue.remove(which)
            except ValueError:
                pass
            else:
                self._redrawQueueModified.set()

        # subclass should handle the rest