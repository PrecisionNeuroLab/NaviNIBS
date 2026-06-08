"""
Pytest plugin: track when each (ordered) test last passed, and optionally skip tests that have
passed more recently than a configurable freshness threshold.

Motivation
----------
The NaviNIBS GUI integration tests form a long, strictly-ordered pipeline (via ``pytest-order``)
that passes state between tests through ``.navinibsdir`` session folders in a persistent cache dir.
The full battery is slow and often not finished in one sitting. This plugin records the timestamp of
each test's last *successful* run and, when a threshold is configured, skips tests that are still
"fresh" -- without updating their timestamp (a skip is not a run). Skipping a fresh upstream test is
safe because its session-folder artifact remains on disk for any downstream test that does run.

Scope
-----
Registered as a ``pytest11`` entry point (see ``pyproject.toml``), so it loads automatically for
every pytest invocation in the environment -- the main suite, each addon suite, combined runs, and
any future addon -- with no per-tree conftest or per-test decorator.

Which tests are tracked
------------------------
Only tests that participate in the ``pytest-order`` graph: those carrying an ``@pytest.mark.order``
marker, plus any test that is depended upon by another (i.e. named as an ``after=`` target -- this
includes pipeline roots like ``test_createSessionViaGUI`` that have no marker of their own). Fast
unit tests with no ordering are left untouched.

Configuration & data files (both live in the test working dir = the same cache dir used by the
``workingDir`` fixture):
  - ``testFreshness.json``        -- registry: ``{key: {"lastPass": <ISO8601>, "outcome": "passed"}}``
  - ``testFreshnessConfig.json``  -- threshold, e.g. ``{"rerunBefore": "2026-06-01T00:00:00"}``.
                                     Absent/empty => nothing is skipped (tracking still happens).

Escape hatches:
  - Set env var ``NAVINIBS_TEST_FRESHNESS=off`` to disable skipping for a whole run (passes are still
    recorded). Useful for focused single-test runs in an IDE.
  - Mark an individual test with ``@pytest.mark.ignoreFreshness`` to always run it regardless of the
    threshold -- the per-test counterpart to the global env-var switch. Its passes are still recorded.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta

import platformdirs
import pytest

logger = logging.getLogger(__name__)

REGISTRY_FILENAME = 'testFreshness.json'
CONFIG_FILENAME = 'testFreshnessConfig.json'

_DISABLE_ENV_VAR = 'NAVINIBS_TEST_FRESHNESS'
_IGNORE_FRESHNESS_MARKER = 'ignoreFreshness'


def pytest_configure(config):
    config.addinivalue_line(
        'markers',
        f'{_IGNORE_FRESHNESS_MARKER}: always run this test regardless of the freshness threshold.')


# region path resolution (single source of truth, shared with the workingDir fixture and the script)

def defaultTestWorkingDir() -> str:
    """The default test working/cache directory, independent of any pytest ``config``."""
    return os.path.join(platformdirs.user_cache_dir(appname='NaviNIBS', appauthor=False), 'tests')


def resolveTestWorkingDir(config) -> str:
    """
    Resolve the test working directory from a pytest ``config``, matching exactly what the
    ``workingDir`` fixture does: prefer a value cached from a previous session, otherwise fall back
    to :func:`defaultTestWorkingDir` and persist it. The ``workingDir`` fixture delegates here so the
    path is computed in one place.
    """
    path = config.cache.get('workingDir', None)
    if path is None:
        path = defaultTestWorkingDir()
        config.cache.set('workingDir', path)
    if not os.path.exists(path):
        os.makedirs(path)
    return path

# endregion


# region registry / config I/O

def _registryPath(workingDir: str) -> str:
    return os.path.join(workingDir, REGISTRY_FILENAME)


def _configPath(workingDir: str) -> str:
    return os.path.join(workingDir, CONFIG_FILENAME)


def loadRegistry(workingDir: str) -> dict:
    path = _registryPath(workingDir)
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f'Could not read test freshness registry at {path}: {exc}')
        return {}


def saveRegistry(workingDir: str, registry: dict) -> None:
    path = _registryPath(workingDir)
    with open(path, 'w') as f:
        json.dump(registry, f, indent=2, sort_keys=True)


def loadThresholdConfig(workingDir: str) -> dict | None:
    """Return the threshold config dict, or ``None`` if absent/empty/unreadable."""
    path = _configPath(workingDir)
    try:
        with open(path, 'r') as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f'Could not read test freshness config at {path}: {exc}')
        return None
    if not cfg:
        return None
    return cfg


def saveThresholdConfig(workingDir: str, cfg: dict | None) -> None:
    """Write the threshold config (or clear it by passing ``None``/empty)."""
    path = _configPath(workingDir)
    if not cfg:
        if os.path.exists(path):
            os.remove(path)
        return
    with open(path, 'w') as f:
        json.dump(cfg, f, indent=2, sort_keys=True)

# endregion


# region threshold parsing

_DURATION_TOKEN = re.compile(r'(\d+(?:\.\d+)?)\s*([smhdw])')
_UNIT_SECONDS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}


def parseDuration(text: str) -> timedelta:
    """
    Parse a duration string like ``'7d'``, ``'12h'``, ``'30m'``, ``'2w'``, or a combination such as
    ``'1d12h'``. Units: s(econds), m(inutes), h(ours), d(ays), w(eeks). Note ``m`` is minutes.
    """
    matches = _DURATION_TOKEN.findall(text.strip().lower())
    if not matches:
        raise ValueError(f'Could not parse duration {text!r} (expected e.g. "7d", "12h", "30m")')
    totalSeconds = sum(float(num) * _UNIT_SECONDS[unit] for num, unit in matches)
    return timedelta(seconds=totalSeconds)


def computeCutoff(cfg: dict | None, now: datetime) -> datetime | None:
    """
    Resolve the threshold config to a cutoff datetime. A test is considered fresh (and skipped) when
    its ``lastPass`` is at or after this cutoff. Returns ``None`` when no threshold is configured.
    """
    if not cfg:
        return None
    if cfg.get('rerunBefore'):
        return datetime.fromisoformat(cfg['rerunBefore'])
    return None


def describeThreshold(cfg: dict | None) -> str:
    if not cfg:
        return 'none'
    if cfg.get('rerunBefore'):
        return f"rerunBefore={cfg['rerunBefore']}"
    return 'none'

# endregion


# region test identity / ordering

def testKey(item) -> str:
    """
    Stable per-test key, independent of which rootdir the test is collected from (the main suite and
    addon suites have different rootdirs). Basename + the full item name (which includes any
    parametrization), matching how ``@pytest.mark.order(after=...)`` references tests.
    """
    return f'{os.path.basename(str(item.path))}::{item.name}'


def _orderAfterTargets(item) -> list[str]:
    """The ``after=`` targets declared on an item's ``order`` marker(s), if any."""
    targets: list[str] = []
    for marker in item.iter_markers(name='order'):
        after = marker.kwargs.get('after')
        if after is None:
            continue
        if isinstance(after, str):
            targets.append(after)
        else:
            targets.extend(after)
    return targets


def _normalizeTargetName(target: str) -> str:
    """Reduce an ``after=`` target (``'file.py::test_x'`` or ``'test_x'``) to the test function name."""
    return target.rsplit('::', 1)[-1]

# endregion


# region hooks

def pytest_collection_modifyitems(config, items):
    workingDir = resolveTestWorkingDir(config)

    # Tests depended upon by others (so pipeline roots without their own order marker are tracked).
    dependedUpon: set[str] = set()
    for item in items:
        for target in _orderAfterTargets(item):
            dependedUpon.add(_normalizeTargetName(target))

    # Tracked = participates in the order graph.
    trackedByNodeid: dict[str, str] = {}
    for item in items:
        hasOrderMarker = any(True for _ in item.iter_markers(name='order'))
        originalName = getattr(item, 'originalname', item.name)
        if hasOrderMarker or originalName in dependedUpon:
            trackedByNodeid[item.nodeid] = testKey(item)

    # Stash for the report hook (which only has access to the item, hence its config).
    config._navinibsFreshnessTracked = trackedByNodeid
    config._navinibsFreshnessWorkingDir = workingDir

    if not trackedByNodeid:
        return

    disabled = os.environ.get(_DISABLE_ENV_VAR, '').strip().lower() == 'off'
    if disabled:
        logger.info(f'{_DISABLE_ENV_VAR}=off -> freshness skipping disabled (still recording passes)')
        return

    cfg = loadThresholdConfig(workingDir)
    cutoff = computeCutoff(cfg, datetime.now())
    if cutoff is None:
        return

    registry = loadRegistry(workingDir)
    nSkipped = 0
    for item in items:
        key = trackedByNodeid.get(item.nodeid)
        if key is None:
            continue
        if item.get_closest_marker(_IGNORE_FRESHNESS_MARKER) is not None:
            continue  # never skip-for-freshness; runs every invocation
        entry = registry.get(key)
        lastPass = entry.get('lastPass') if entry else None
        if not lastPass:
            continue
        try:
            lastPassDt = datetime.fromisoformat(lastPass)
        except ValueError:
            continue
        if lastPassDt >= cutoff:
            item.add_marker(pytest.mark.skip(
                reason=f'freshness: last passed {lastPass}, within threshold {describeThreshold(cfg)}'))
            nSkipped += 1

    if nSkipped:
        logger.info(f'Freshness: skipping {nSkipped} test(s) within threshold {describeThreshold(cfg)} '
                    f'(set {_DISABLE_ENV_VAR}=off or clear {CONFIG_FILENAME} to disable)')


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != 'call' or not report.passed:
        return

    config = item.config
    tracked = getattr(config, '_navinibsFreshnessTracked', None)
    workingDir = getattr(config, '_navinibsFreshnessWorkingDir', None)
    if not tracked or workingDir is None:
        return
    key = tracked.get(item.nodeid)
    if key is None:
        return

    # Record immediately so an interrupted battery still persists what passed.
    registry = loadRegistry(workingDir)
    registry[key] = {'lastPass': datetime.now().isoformat(), 'outcome': 'passed'}
    saveRegistry(workingDir, registry)

# endregion
