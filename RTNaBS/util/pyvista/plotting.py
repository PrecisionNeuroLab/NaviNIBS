import asyncio
import logging
import pyvistaqt as pvqt


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class BackgroundPlotter(pvqt.BackgroundPlotter):
    """
    Same as inherited pvqt.BackgroundPlotter, but batches multiple render calls together with an async coroutine
    """

    _needsRender: asyncio.Event
    _renderTask: asyncio.Task

    def __init__(self, *args, **kwargs):
        self._needsRender = asyncio.Event()

        super().__init__(*args, **kwargs)

        self._renderTask = asyncio.create_task(self._renderLoop())

    async def _renderLoop(self):
        while True:
            await self._needsRender.wait()
            self._needsRender.clear()
            logger.debug('Rendering')
            super().render()
            logger.debug('Done rendering')

    def render(self):
        logger.debug('Setting render flag')
        self._needsRender.set()