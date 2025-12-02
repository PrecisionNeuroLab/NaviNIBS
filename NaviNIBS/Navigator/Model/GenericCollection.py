from __future__ import annotations

import collections

import attrs
from abc import ABC
from collections.abc import Sequence, Mapping, Iterable
import logging
import typing as tp

from NaviNIBS.util.attrs import attrsAsDict
from NaviNIBS.util.numpy import array_equalish
from NaviNIBS.util.Signaler import Signal

logger = logging.getLogger(__name__)


K = tp.TypeVar('K', int, str)  # collection item key type


@attrs.define(slots=False)
class GenericCollectionDictItem(ABC, tp.Generic[K]):

    _key: K

    sigKeyAboutToChange: Signal = attrs.field(init=False, repr=False, eq=False,
                                              factory=lambda: Signal((K, K)))  # includes old key, new key
    sigKeyChanged: Signal = attrs.field(init=False, repr=False, eq=False,
                                        factory=lambda: Signal((K, K)))  # includes old key, new key

    sigItemAboutToChange: Signal = attrs.field(init=False, repr=False, eq=False,
                                               factory=lambda: Signal((K, tp.Optional[tp.List[str]])))
    """
    This signal includes the key of the item, and optionally a list of keys of attributes about to change;
    if second arg is None, all attributes should be assumed to be about to change.

    Not emitted when key changed (use sigKeyAboutToChange instead!)
    """
    sigItemChanged: Signal = attrs.field(init=False, repr=False, eq=False,
                                         factory=lambda: Signal((K, tp.Optional[list[str]])))
    """
    This signal includes the key of the item, and optionally a list of keys of changed attributes;  
    if second arg is None, all attributes should be assumed to have changed.

    Not emitted when key changed (use sigKeyChanged instead!)
    """

    def __attrs_post_init__(self):
        pass

    @property
    def key(self):
        return self._key

    @key.setter
    def key(self, newKey: K):
        if self._key == newKey:
            return
        prevKey = self._key
        self.sigKeyAboutToChange.emit(prevKey, newKey)
        self._key = newKey
        self.sigKeyChanged.emit(prevKey, newKey)

    def asDict(self) -> dict[str, tp.Any]:
        return attrsAsDict(self)

    @classmethod
    def fromDict(cls, d: dict[str, tp.Any]):
        return cls(**d)


# new decorator to wrap setters: preserves the setter signature (via functools.wraps)

def collectionDictItemAttrSetter(func):
    @functools.wraps(func)
    def wrapper(self: GenericCollectionDictItem, value):
        # assume method name is the public attribute name
        publicName = func.__name__
        privateName = f'_{publicName}'
        if getattr(self, privateName) == value:
            return
        # notify listeners and mark grid dirty
        self.sigItemAboutToChange.emit(self.key, [publicName])
        # allow any custom per-setter logic to run
        result = func(self, value)
        # actually set the backing attribute and trigger update
        setattr(self, privateName, value)
        self.sigItemChanged.emit(self.key, [publicName])
        return result
    return wrapper


CI = tp.TypeVar('CI', bound=GenericCollectionDictItem)  # collection item type


@attrs.define(slots=False)
class GenericCollection(ABC, tp.Generic[K, CI]): # (minor note: it would be helpful to specify CI[K] here but python syntax doesn't yet allow for this)
    """
    Base class to implement collection behavior and signaling for various session model components
    """

    _items: dict[K, CI] = attrs.field(factory=dict)

    sigItemsAboutToChange: Signal[list[K], list[str] | None] = attrs.field(init=False, eq=False, factory=Signal, repr=False)
    """
    This signal includes list of keys of collection items about to change, and optionally a list of 
    keys of attributes about to change;  if second arg is None, all attributes should be assumed to
    be about to change.
    """

    sigItemsChanged: Signal[list[K], list[str] | None] = attrs.field(init=False, eq=False, factory=Signal, repr=False)
    """
    This signal includes list of keys of changed collection items, and optionally a list of keys of
    changed attributes; if second arg is None, all attributes should be assumed to have changed.
    """

    sigItemKeyAboutToChange: Signal = attrs.field(init=False, eq=False, factory=lambda: Signal((K, K)), repr=False)
    sigItemKeyChanged: Signal = attrs.field(init=False, eq=False, factory=lambda: Signal((K, K)), repr=False)
    """
    Emitted in addition to sigItemsAboutToChange and sigItemsChanged specifically when an item key changes.

    This is because when a key does change, everything else about a item may have changed, so these other signals don't include a list of attributes indicating the source of the change.
    """

    def __attrs_post_init__(self):
        for key, item in self._items.items():
            assert item.key == key
            item.sigItemAboutToChange.connect(self._onItemAboutToChange)
            item.sigItemChanged.connect(self._onItemChanged)
            item.sigKeyAboutToChange.connect(self._onItemKeyAboutToChange)
            item.sigKeyChanged.connect(self._onItemKeyChanged)

    def addItem(self, item: CI):
        assert item.key not in self._items
        return self.setItem(item=item)

    def deleteItem(self, key: K):
        self.deleteItems([key])

    def deleteItems(self, keys: list[K]):
        assert all(key in self._items for key in keys)
        logger.info(f'Deleting {keys}')

        self.sigItemsAboutToChange.emit(keys, None)
        for key in keys:
            self._items[key].sigItemAboutToChange.disconnect(self._onItemAboutToChange)
            self._items[key].sigKeyAboutToChange.disconnect(self._onItemKeyAboutToChange)
            self._items[key].sigKeyChanged.disconnect(self._onItemKeyChanged)
            self._items[key].sigItemChanged.disconnect(self._onItemChanged)

            del self._items[key]

        self.sigItemsChanged.emit(keys, None)

    def setItem(self, item: CI):
        self.sigItemsAboutToChange.emit([item.key], None)
        if item.key in self._items:
            self._items[item.key].sigItemAboutToChange.disconnect(self._onItemAboutToChange)
            self._items[item.key].sigKeyAboutToChange.disconnect(self._onItemKeyAboutToChange)
            self._items[item.key].sigKeyChanged.disconnect(self._onItemKeyChanged)
            self._items[item.key].sigItemChanged.disconnect(self._onItemChanged)
        self._items[item.key] = item

        item.sigItemAboutToChange.connect(self._onItemAboutToChange)
        item.sigKeyAboutToChange.connect(self._onItemKeyAboutToChange)
        item.sigKeyChanged.connect(self._onItemKeyChanged)
        item.sigItemChanged.connect(self._onItemChanged)

        self.sigItemsChanged.emit([item.key], None)

    def setItems(self, items: list[CI]):
        # assume all keys are changing, though we could do comparisons to find subset changed
        # note that this can also be used for reordering items
        oldKeys = list(self.keys())
        newKeys = [item.key for item in items]
        combinedKeys = list(set(oldKeys) | set(newKeys))
        self.sigItemsAboutToChange.emit(combinedKeys, None)
        for key in oldKeys:
            self._items[key].sigItemAboutToChange.disconnect(self._onItemAboutToChange)
            self._items[key].sigKeyAboutToChange.disconnect(self._onItemKeyAboutToChange)
            self._items[key].sigKeyChanged.disconnect(self._onItemKeyChanged)
            self._items[key].sigItemChanged.disconnect(self._onItemChanged)
        self._items = {item.key: item for item in items}
        for key, item in self._items.items():
            self._items[key].sigItemAboutToChange.connect(self._onItemAboutToChange)
            self._items[key].sigKeyAboutToChange.connect(self._onItemKeyAboutToChange)
            self._items[key].sigKeyChanged.connect(self._onItemKeyChanged)
            self._items[key].sigItemChanged.connect(self._onItemChanged)
        self.sigItemsChanged.emit(combinedKeys, None)

    def setAttribForItems(self, keys: Sequence[K], attribsAndValues: dict[str, Sequence[tp.Any]]) -> None:
        """
        Change an attribute for multiple items at once, without signaling separately for each item.
        E.g. for changing visibility of multiple targets simultaneously.

        :param keys: Identifying keys of items to change
        :param attribsAndValues: mapping of attribute -> list of new values, where this list is of the same length as specified keys.
        :return: None
        """
        assert 'key' not in attribsAndValues, 'Should change key separately, not via setAttribForItems'
        for attrib, values in attribsAndValues.items():
            assert len(values) == len(keys)

        changingKeys = list()
        changingAttribsAndValues = dict()
        for iKey, key in enumerate(keys):
            for attrib, values in attribsAndValues.items():
                assert len(values) == len(keys)
                if not array_equalish(getattr(self._items[key], attrib), values[iKey]):
                    changingKeys.append(key)
                    if attrib not in changingAttribsAndValues:
                        changingAttribsAndValues[attrib] = list()
                    changingAttribsAndValues[attrib].append(values[iKey])
                    break

        if len(changingKeys) == 0:
            return

        self.sigItemsAboutToChange.emit(changingKeys, list(changingAttribsAndValues.keys()))
        with self.sigItemsAboutToChange.blocked(), self.sigItemsChanged.blocked():
            for iKey, key in enumerate(changingKeys):
                for attrib, values in changingAttribsAndValues.items():
                    setattr(self._items[key], attrib, values[iKey])
        self.sigItemsChanged.emit(changingKeys, list(changingAttribsAndValues.keys()))

    def _onItemAboutToChange(self, key: str, attribKeys: tp.Optional[list[str]] = None):
        self.sigItemsAboutToChange.emit([key], attribKeys)

    def _onItemKeyAboutToChange(self, fromKey: str, toKey: str):
        assert toKey not in self._items
        self.sigItemKeyAboutToChange.emit(fromKey, toKey)
        self.sigItemsAboutToChange.emit([fromKey, toKey], None)

    def _onItemKeyChanged(self, fromKey: str, toKey: str):
        assert toKey not in self._items
        self._items = {(toKey if key == fromKey else key): val for key, val in self._items.items()}
        self.sigItemKeyChanged.emit(fromKey, toKey)
        self.sigItemsChanged.emit([fromKey, toKey], None)

    def _onItemChanged(self, key: str, attribKeys: tp.Optional[list[str]] = None):
        self.sigItemsChanged.emit([key], attribKeys)

    def __getitem__(self, key):
        return self._items[key]

    def __setitem__(self, key, item: CI):
        assert key == item.key
        self.setItem(item=item)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)

    def keys(self):
        return self._items.keys()

    def items(self) -> collections.ItemsView[K, CI]:
        return self._items.items()

    def get(self, *args, **kwargs):
        return self._items.get(*args, **kwargs)

    def values(self):
        return self._items.values()

    def merge(self: C, otherItems: C):
        self.sigItemsAboutToChange.emit(list(otherItems.keys()), None)

        with self.sigItemsAboutToChange.blocked(), self.sigItemsChanged.blocked():
            for item in otherItems.values():
                self.setItem(item)

        self.sigItemsChanged.emit(list(otherItems.keys()), None)

    def asList(self) -> list[dict[str, tp.Any]]:
        return [item.asDict() for item in self._items.values()]

    @classmethod
    def fromList(cls: tp.Type[C], itemList: list[dict[K, tp.Any]]) -> C:
        raise NotImplementedError('Should be implemented by subclass')


C = tp.TypeVar('C', bound=GenericCollection)  # collection type


@attrs.define(slots=False, eq=False)
class GenericListItem(ABC):
    """
    List item that does not track its index. When signalling changes, the item should emit itself
    as the first argument so collections can determine the current index (via list.index(item)).
    """
    sigItemAboutToChange: Signal[tp.Self, list[str] | None] = attrs.field(init=False, repr=False, eq=False, factory=Signal)
    """
    Emits (item, optional list of attribute keys about to change). If second arg is None, assume all attributes.
    """

    sigItemChanged: Signal[tp.Self, list[str] | None] = attrs.field(init=False, repr=False, eq=False, factory=Signal)
    """
    Emits (item, optional list of changed attribute keys). If second arg is None, assume all attributes.
    """

    def __attrs_post_init__(self):
        pass

    def asDict(self) -> dict[str, tp.Any]:
        return attrsAsDict(self)

    @classmethod
    def fromDict(cls, d: dict[str, tp.Any]):
        return cls(**d)


LI = tp.TypeVar('LI', bound=GenericListItem)  # list item type


@attrs.define(slots=False)
class GenericList(ABC, tp.Generic[LI]):
    """
    Ordered list-based collection with signaling. Items are addressed by integer indices.
    Items themselves do not track index; they should emit signals in the form (item, attribKeys).
    """
    _items: list[LI] = attrs.field(factory=list)

    sigItemsAboutToChange: Signal[set[LI], list[str] | None] = attrs.field(init=False, eq=False, factory=Signal, repr=False)
    sigItemsChanged: Signal[set[LI], list[str] | None] = attrs.field(init=False, eq=False, factory=Signal, repr=False)
    """
    These signals include set of collection items about to change / changed, and optionally a list of
    keys of attributes about to change / changed;  if second arg is None, all attributes should be assumed to
    be changing / changed.
    
    Not emitted when only index changes.
    """

    sigItemIndicesAboutToChange: Signal[set[LI]] = attrs.field(init=False, eq=False, factory=lambda: Signal((set[int],)), repr=False)
    sigItemIndicesChanged: Signal[set[LI]] = attrs.field(init=False, eq=False, factory=lambda: Signal((set[int],)), repr=False)
    """
    Emitted when items change (e.g. via insertion/deletion/reordering).
    """

    def __attrs_post_init__(self):
        for item in self._items:
            item.sigItemAboutToChange.connect(self._onItemAboutToChange)
            item.sigItemChanged.connect(self._onItemChanged)

    def append(self, item: LI):
        assert item not in self._items, 'Item already present in list'
        newIndex = len(self._items)
        self.sigItemIndicesAboutToChange.emit({item})
        self.sigItemsAboutToChange.emit({item}, None)
        self._items.append(item)
        item.sigItemAboutToChange.connect(self._onItemAboutToChange)
        item.sigItemChanged.connect(self._onItemChanged)
        self.sigItemsChanged.emit({item}, None)
        self.sigItemIndicesChanged.emit({item})

    def insert(self, index: int, item: LI):
        assert item not in self._items, 'Item already present in list'
        assert 0 <= index <= len(self._items)
        laterIndices = [idx for idx in range(len(self._items)) if idx >= index]
        itemsChangingIndex = {self._items[idx] for idx in laterIndices} | {item}
        self.sigItemIndicesAboutToChange.emit(itemsChangingIndex)
        self.sigItemsAboutToChange.emit({item}, None)
        self._items.insert(index, item)
        item.sigItemAboutToChange.connect(self._onItemAboutToChange)
        item.sigItemChanged.connect(self._onItemChanged)
        self.sigItemsChanged.emit({item}, None)
        self.sigItemIndicesChanged.emit(itemsChangingIndex)

    def deleteItem(self, index: int):
        self.deleteItems([index])

    def deleteItems(self, indices: list[int]):
        # ensure valid and unique, remove highest indices first to preserve remaining indices
        assert all(0 <= idx < len(self._items) for idx in indices)
        indices = sorted(set(indices), reverse=True)
        laterIndices = [idx for idx in range(len(self._items)) if idx not in indices and idx > indices[-1]]
        logger.info(f'Deleting indices {indices}')
        itemsDeleting = {self._items[idx] for idx in indices}
        itemsChangingIndex = {self._items[idx] for idx in indices + laterIndices}
        self.sigItemIndicesAboutToChange.emit(itemsChangingIndex)
        self.sigItemsAboutToChange.emit(itemsDeleting, None)
        for idx in indices:
            item = self._items[idx]
            item.sigItemAboutToChange.disconnect(self._onItemAboutToChange)
            item.sigItemChanged.disconnect(self._onItemChanged)
            del self._items[idx]
        # update nothing on items themselves (they don't track index)
        self.sigItemsChanged.emit(itemsDeleting, None)
        self.sigItemIndicesChanged.emit(itemsChangingIndex)

    def setItem(self, item: LI, index: tp.Optional[int] = None):
        """
        If index is None, append item (same as addItem). If index is provided, replace or insert at that position.
        """
        if index is None or index == len(self._items):
            self.append(item)
            return
        assert 0 <= index < len(self._items)
        oldItem = self._items[index]
        try:
            prevIndex = self._items.index(item)
        except ValueError:
            prevIndex = None
        else:
            if prevIndex != index:
                raise ValueError('Item already present in list at different index')
            else:
                logger.debug('Setting item at same index where it already exists; no action taken')
                return
        itemsChanging = {item, oldItem}
        self.sigItemIndicesAboutToChange.emit(itemsChanging)
        self.sigItemsAboutToChange.emit(itemsChanging, None)
        old = self._items[index]
        old.sigItemAboutToChange.disconnect(self._onItemAboutToChange)
        old.sigItemChanged.disconnect(self._onItemChanged)
        self._items[index] = item
        item.sigItemAboutToChange.connect(self._onItemAboutToChange)
        item.sigItemChanged.connect(self._onItemChanged)
        self.sigItemsChanged.emit(itemsChanging, None)
        self.sigItemIndicesChanged.emit(itemsChanging)

    def setItems(self, items: list[LI]):
        itemsChanging = set(self._items) | set(items)
        self.sigItemIndicesAboutToChange.emit(itemsChanging)
        self.sigItemsAboutToChange.emit(itemsChanging, None)
        for itm in self._items:
            itm.sigItemAboutToChange.disconnect(self._onItemAboutToChange)
            itm.sigItemChanged.disconnect(self._onItemChanged)
        self._items = items
        for itm in self._items:
            itm.sigItemAboutToChange.connect(self._onItemAboutToChange)
            itm.sigItemChanged.connect(self._onItemChanged)
        self.sigItemsChanged.emit(itemsChanging, None)
        self.sigItemIndicesChanged.emit(itemsChanging)

    def setAttribForItems(self, indices: Sequence[int], attribsAndValues: dict[str, Sequence[tp.Any]]) -> None:
        for attrib, values in attribsAndValues.items():
            assert len(values) == len(indices)

        changingIndices = list()
        changingAttribsAndValues = dict()
        for iIdx, idx in enumerate(indices):
            for attrib, values in attribsAndValues.items():
                if not array_equalish(getattr(self._items[idx], attrib), values[iIdx]):
                    changingIndices.append(idx)
                    if attrib not in changingAttribsAndValues:
                        changingAttribsAndValues[attrib] = list()
                    changingAttribsAndValues[attrib].append(values[iIdx])
                    break

        if len(changingIndices) == 0:
            return

        itemsChanging = {self._items[idx] for idx in changingIndices}

        self.sigItemsAboutToChange.emit(itemsChanging, list(changingAttribsAndValues.keys()))
        with self.sigItemsAboutToChange.blocked(), self.sigItemsChanged.blocked():
            for iIdx, idx in enumerate(changingIndices):
                for attrib, values in changingAttribsAndValues.items():
                    setattr(self._items[idx], attrib, values[iIdx])
        self.sigItemsChanged.emit(itemsChanging, list(changingAttribsAndValues.keys()))

    def _onItemAboutToChange(self, item: LI, attribKeys: tp.Optional[list[str]] = None):
        # resolve current index of the item and emit collection-level signal
        try:
            idx = self._items.index(item)
        except ValueError:
            # item no longer present
            return
        self.sigItemsAboutToChange.emit({item}, attribKeys)

    def _onItemChanged(self, item: LI, attribKeys: tp.Optional[list[str]] = None):
        try:
            idx = self._items.index(item)
        except ValueError:
            return
        self.sigItemsChanged.emit({item}, attribKeys)

    def __getitem__(self, index: int) -> LI:
        return self._items[index]

    def __setitem__(self, index: int | slice, item: LI | list[LI]):
        if isinstance(index, slice):
            assert isinstance(item, list)
            items = self._items.copy()
            items[index] = item
            self.setItems(items)
        else:
            # place item at given index
            self.setItem(item=item, index=index)

    def __iter__(self) -> tp.Iterator[LI]:
        return self._items.__iter__()

    def __len__(self):
        return len(self._items)

    def index(self, item: LI) -> int:
        return self._items.index(item)

    def asList(self) -> list[dict[str, tp.Any]]:
        return [item.asDict() for item in self._items]

    @classmethod
    def fromList(cls: tp.Type[GenericList[LI]], itemList: list[dict[str, tp.Any]]) -> GenericList[LI]:
        raise NotImplementedError('Should be implemented by subclass')
