"""
View and control the test-freshness state used by the NaviNIBS pytest freshness plugin
(``NaviNIBS.util.testing.freshness``).

The plugin records when each ordered test last passed, and -- when a threshold is configured -- skips
tests that are still "fresh". This script lets you inspect that registry and set/clear the threshold
without launching pytest.

Examples
--------
    # Show the table of last-run dates (and whether each test is currently fresh)
    poetry run python scripts/showTestFreshness.py

    # Skip tests that passed within the last 7 days (resolved to an absolute cutoff now)
    poetry run python scripts/showTestFreshness.py --set-older-than 7d

    # Skip tests that passed on/after an absolute datetime
    poetry run python scripts/showTestFreshness.py --set-before 2026-06-01

    # Stop skipping anything (tracking continues)
    poetry run python scripts/showTestFreshness.py --clear-threshold

    # Force specific tests (or everything) to re-run by dropping their recorded timestamps
    poetry run python scripts/showTestFreshness.py --reset test_basicNavigation.py::test_basicNavigation
    poetry run python scripts/showTestFreshness.py --reset all
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from NaviNIBS.util.testing.freshness import (
    defaultTestWorkingDir,
    loadRegistry,
    saveRegistry,
    loadThresholdConfig,
    saveThresholdConfig,
    computeCutoff,
    describeThreshold,
    parseDuration,
    CONFIG_FILENAME,
    REGISTRY_FILENAME,
)


def _formatAge(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return 'future'
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f'{days}d {hours}h'
    if hours:
        return f'{hours}h {minutes}m'
    return f'{minutes}m'


def _showTable(workingDir: str) -> None:
    registry = loadRegistry(workingDir)
    cfg = loadThresholdConfig(workingDir)
    now = datetime.now()
    cutoff = computeCutoff(cfg, now)

    print(f'Working dir:   {workingDir}')
    print(f'Registry file: {REGISTRY_FILENAME}')
    print(f'Threshold:     {describeThreshold(cfg)} ({CONFIG_FILENAME})'
          + ('' if cutoff is None else f'  ->  skip tests last passed on/after {cutoff.isoformat(timespec="seconds")}'))
    print()

    if not registry:
        print('No tests recorded yet.')
        return

    rows = []
    for key, entry in registry.items():
        lastPass = entry.get('lastPass') if isinstance(entry, dict) else None
        if lastPass:
            dt = datetime.fromisoformat(lastPass)
            age = _formatAge(now - dt)
            fresh = 'yes' if (cutoff is not None and dt >= cutoff) else ('no' if cutoff is not None else '-')
            lastStr = dt.isoformat(timespec='seconds')
        else:
            age = '-'
            fresh = '-'
            lastStr = 'never'
        rows.append((key, lastStr, age, fresh, dt if lastPass else datetime.min))

    rows.sort(key=lambda r: r[4])  # oldest first

    keyWidth = max(len('TEST'), max(len(r[0]) for r in rows))
    lastWidth = max(len('LAST PASS'), max(len(r[1]) for r in rows))
    print(f'{"TEST":<{keyWidth}}  {"LAST PASS":<{lastWidth}}  {"AGE":>10}  {"FRESH?":>6}')
    print(f'{"-" * keyWidth}  {"-" * lastWidth}  {"-" * 10}  {"-" * 6}')
    for key, lastStr, age, fresh, _ in rows:
        print(f'{key:<{keyWidth}}  {lastStr:<{lastWidth}}  {age:>10}  {fresh:>6}')


def main() -> None:
    parser = argparse.ArgumentParser(description='View/control NaviNIBS test freshness state.')
    parser.add_argument('--cache-dir', default=None,
                        help='Override the test working dir (defaults to the platformdirs NaviNIBS test cache).')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--set-older-than', metavar='DURATION',
                       help='Skip tests that passed within this duration (e.g. 7d, 12h, 30m, 1d12h).')
    group.add_argument('--set-before', metavar='DATETIME',
                       help='Skip tests that passed on/after this ISO datetime (e.g. 2026-06-01).')
    group.add_argument('--clear-threshold', action='store_true',
                       help='Remove the threshold so nothing is skipped (tracking continues).')
    parser.add_argument('--reset', metavar='KEY',
                        help='Drop the recorded timestamp for a test key, or "all" to clear the registry.')
    args = parser.parse_args()

    workingDir = args.cache_dir or defaultTestWorkingDir()

    mutated = False

    if args.set_older_than is not None:
        duration = parseDuration(args.set_older_than)  # validate early
        cutoff = datetime.now() - duration
        saveThresholdConfig(workingDir, {'rerunBefore': cutoff.isoformat()})
        print(f'Set threshold: rerunBefore={cutoff.isoformat()} (from --set-older-than {args.set_older_than})')
        mutated = True
    elif args.set_before is not None:
        dt = datetime.fromisoformat(args.set_before)  # validate
        saveThresholdConfig(workingDir, {'rerunBefore': dt.isoformat()})
        print(f'Set threshold: rerunBefore={dt.isoformat()}')
        mutated = True
    elif args.clear_threshold:
        saveThresholdConfig(workingDir, None)
        print('Cleared threshold (nothing will be skipped).')
        mutated = True

    if args.reset is not None:
        registry = loadRegistry(workingDir)
        if args.reset == 'all':
            saveRegistry(workingDir, {})
            print(f'Cleared {len(registry)} registry entr{"y" if len(registry) == 1 else "ies"}.')
        elif args.reset in registry:
            del registry[args.reset]
            saveRegistry(workingDir, registry)
            print(f'Reset {args.reset!r}.')
        else:
            print(f'No registry entry matching {args.reset!r} (use "all" to clear everything).')
        mutated = True

    if mutated:
        print()
    _showTable(workingDir)


if __name__ == '__main__':
    main()
