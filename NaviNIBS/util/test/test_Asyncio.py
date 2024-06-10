import asyncio
import logging
import pytest

from NaviNIBS.util.Asyncio import asyncNonCancellable, asyncAtomicCancel

logger = logging.getLogger(__name__)


async def fn1():
    logger.debug('Starting task 1')
    await asyncio.sleep(1.)
    logger.debug('Done task 1')


@asyncNonCancellable
async def fn2():
    logger.debug('Starting task 2')
    await asyncio.sleep(1.)
    logger.debug('Done task 2')


@asyncAtomicCancel
async def fn3():
    logger.debug('Starting task 3')
    await asyncio.sleep(1.)
    logger.debug('Done task 3')


@pytest.mark.asyncio
async def test_cancellation():
    logger.debug('Starting test')
    task1 = asyncio.create_task(fn1())
    task2 = asyncio.create_task(fn2())
    task3 = asyncio.create_task(fn3())
    await asyncio.sleep(0.5)
    logger.debug('Cancelling tasks')
    for task in (task1, task2, task3):
        task.cancel()
    logger.debug('Waiting for tasks to finish')
    with pytest.raises(asyncio.CancelledError):
        await task1  # should have been cancelled

    with pytest.raises(asyncio.CancelledError):
        await task3  # should have finished, but then raised CancelledError

    await task2  # should not have been cancelled
    logger.debug('Done test')

