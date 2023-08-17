import asyncio
import logging
from functools import wraps
import typing as tp

from . import exceptionToStr

logger = logging.getLogger(__name__)

_T = tp.TypeVar('_T')


async def _wrap_awaitable(aw: tp.Awaitable[_T]) -> _T:
    return await aw


async def asyncWaitWithCancel(
        aws: tp.Iterable[tp.Awaitable],
        timeout: float | None = None,
        return_when=asyncio.ALL_COMPLETED) -> tuple[set[asyncio.Task], set[asyncio.Task]]:

    done, pending = await asyncio.wait([asyncio.create_task(_wrap_awaitable(aw)) for aw in aws], timeout=timeout, return_when=return_when)
    for task in pending:
        task.cancel()
    cancelled = pending
    return done, cancelled


async def asyncTryAndLogExceptionOnError(fn: tp.Callable[..., tp.Awaitable], *args, **kwargs):
    try:
        return await fn(*args, **kwargs)
    except Exception as e:
        logger.error('Exception: %s' % exceptionToStr(e))
        raise e


def asyncioRunAndHandleExceptions(fn: tp.Callable[..., tp.Awaitable], *args, **kwargs):
    def handleExceptionInAsyncTask(loop: asyncio.AbstractEventLoop, context: dict):
        from pprint import pprint
        logger.error('Unhandled exception in async task, will stop loop. Exception context:\n%s' % (pprint(context),))
        loop.stop()

    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handleExceptionInAsyncTask)
    try:
        loop.run_until_complete(fn(*args, **kwargs))
    except RuntimeError as e:
        if e.args[0] == 'Event loop stopped before Future completed.':
            logger.info('Event loop stopped early')
        else:
            logger.error('Exception: %s' % exceptionToStr(e))
            raise e
    except Exception as e:
        logger.error('Exception: %s' % exceptionToStr(e))
        raise e
    finally:
        logger.debug('Running any shutdown_asyncgens')
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except RuntimeError as e:
            if e.args[0] == 'Event loop is closed':
                logger.info('Event looped already closed')
            else:
                raise e
        else:
            logger.debug('Closing loop')
            loop.close()
        logger.debug('Done!')


def asyncAtomicCancellable(fn: tp.Callable[..., tp.Awaitable], *args, **kwargs):
    @wraps(fn)
    async def wrapper(*args, **kwargs):
        task = asyncio.create_task(fn(*args, **kwargs))
        shieldedTask = asyncio.shield(task)
        try:
            return await shieldedTask
        except asyncio.CancelledError:
            await task
            raise
    return wrapper


def asyncNonCancellable(fn: tp.Callable[..., tp.Awaitable], *args, **kwargs):
    @wraps(fn)
    async def wrapper(*args, **kwargs):
        task = asyncio.create_task(fn(*args, **kwargs))
        shieldedTask = asyncio.shield(task)
        try:
            return await shieldedTask
        except asyncio.CancelledError:
            return await task
    return wrapper

