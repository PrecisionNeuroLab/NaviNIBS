import typing as tp
import attr
import contextlib

#  Note: once variadic generics are supported (see https://www.python.org/dev/peps/pep-0646/)
#   we'll be able to type hint this more usefully


@attr.s(auto_attribs=True, eq=False)
class Signal:
    _types: tp.Tuple[tp.Type, ...] = attr.ib(default=tuple())
    _connections: tp.Set[tp.Callable[..., None]] = attr.ib(init=False, factory=set)
    _blockedSemaphoreCounter: int = 0

    def __attrs_post_init__(self):
        pass

    def connect(self, fn: tp.Callable[[], None]):
        try:
            self._connections.add(fn)
        except TypeError as e:
            raise e

    def disconnect(self, fn: tp.Callable[[], None]):
        self._connections.remove(fn)

    @property
    def isBlocked(self):
        return self._blockedSemaphoreCounter > 0

    def emit(self, *args, **kwargs) -> None:
        if self._blockedSemaphoreCounter > 0:
            return
        for fn in self._connections.copy():
            if fn in self._connections:
                fn(*args, **kwargs)

    @contextlib.contextmanager
    def blocked(self):
        """
        Can be used like:
                sig = Signal()
                sig.emit()  # will emit
                with sig.blocked():
                    sig.emit()  # will do nothing
                sig.emit()  # will emit
        """
        self._blockedSemaphoreCounter += 1
        try:
            yield None
        finally:
            self._blockedSemaphoreCounter -= 1

    @contextlib.contextmanager
    def connected(self, fn: tp.Callable[[], None]):
        self.connect(fn)
        try:
            yield None
        finally:
            self.disconnect(fn)
