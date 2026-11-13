"""Resolve the native target ID against the bundled enemy handbook."""

from functools import lru_cache
import json
from pathlib import Path

CATALOG_PATH = (Path(__file__).resolve().parent /
                'vendor/ark-source/game_data/catalogs/enemy_handbook.json')


@lru_cache(maxsize=1)
def _levels(path: Path) -> dict[str, bool]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    entries = payload.get('enemyData') if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        return {}
    levels = {}
    for target, entry in entries.items():
        if not isinstance(entry, dict) or entry.get('enemyId') != target:
            continue
        rank = entry.get('enemyLevel')
        if rank in ('NORMAL', 'ELITE', 'BOSS'):
            levels[target] = rank == 'BOSS'
    return levels


def classify_boss(target: str | None) -> bool | None:
    """Return the handbook's boss classification, or None for unknown IDs."""
    if not isinstance(target, str):
        return None
    if target.startswith(('char_', 'token_', 'trap_')):
        return False
    if target.startswith('enemy_'):
        return _levels(CATALOG_PATH).get(target)
    return None
