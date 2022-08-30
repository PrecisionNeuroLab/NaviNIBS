import asyncio
import logging
import pyvistaqt as pvqt


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class BackgroundPlotter(pvqt.plotting.BackgroundPlotter):
    """
    Same as inherited pvqt.BackgroundPlotter, but batches multiple render calls together with an async coroutine
    """

    _needsRender: asyncio.Event
    _renderTask: asyncio.Task
    _renderingNotPaused: asyncio.Event

    def __init__(self, *args, **kwargs):
        self._needsRender = asyncio.Event()
        self._renderingNotPaused = asyncio.Event()
        self._renderingNotPaused.set()

        super().__init__(*args, **kwargs)

        self._renderTask = asyncio.create_task(self._renderLoop())

    def pauseRendering(self):
        self._renderingNotPaused.clear()

    def resumeRendering(self):
        self._renderingNotPaused.set()

    async def _renderLoop(self):
        while True:
            await asyncio.sleep(0.05)
            await self._needsRender.wait()
            await self._renderingNotPaused.wait()
            self._needsRender.clear()
            logger.debug('Rendering')
            super().render()
            logger.debug('Done rendering')

    def render(self):
        logger.debug('Setting render flag')
        self._needsRender.set()