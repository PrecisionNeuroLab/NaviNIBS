import asyncio
import logging
import traceback
from functools import wraps
import typing as tp

from . import exceptionToStr
from .AsyncRunner import asyncioRunAndHandleExceptions

logger = logging.getLogger(__name__)

_T = tp.TypeVar('_T')


async def _wrap_awaitable(aw: tp.Awaitable[_T]) -> _T:
    return await aw


async def asyncWait(
        aws: tp.Iterable[tp.Awaitable],
        return_when=asyncio.ALL_COMPLETED) -> tuple[set[asyncio.Task], set[asyncio.Task]]:
    """
    Similar to asyncio.wait, but allow waiting for coroutines, which was removed from asyncio.wait in 3.11
    """
    done, pending = await asyncio.wait([asyncio.create_task(
        _wrap_awaitable(aw),
        name=f'{aw.__qualname__}_{i}') for i, aw in enumerate(aws)],
        return_when=return_when)
    return done, pending


async def asyncWaitWithCancel(
        aws: tp.Iterable[tp.Awaitable],
        timeout: float | None = None,
        return_when=asyncio.ALL_COMPLETED) -> tuple[set[asyncio.Task], set[asyncio.Task]]:

    done, pending = await asyncio.wait([asyncio.create_task(
        _wrap_awaitable(aw),
        name=f'{aw.__qualname__}_{i}') for i, aw in enumerate(aws)],
        timeout=timeout,
        return_when=return_when)
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

_asyncTasks = []

def asyncCreateTask(
        fn: tp.Callable[..., tp.Awaitable],
        *args,
        asyncTaskName: str | None = None,
        raiseException: bool = False,
        **kwargs) -> asyncio.Task:
    name = asyncTaskName if asyncTaskName is not None else fn.__qualname__
    creationTraceback = traceback.extract_stack()[:-1]

    async def _wrapper():
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            logger.error('Exception in task %r\nCreated at:\n%s%s',
                         name,
                         ''.join(traceback.format_list(creationTraceback)),
                         exceptionToStr(e))
            if raiseException:
                raise

    newTask = asyncio.create_task(_wrapper(), name=name)

    _asyncTasks.append(newTask)  # keep reference to task to prevent garbage collection of task before it finishes

    return newTask


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

