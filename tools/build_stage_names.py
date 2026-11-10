"""Build the compact player-readable stage catalog bundled with ArkDPS.

The table is generated at build time so the application never reads game files
at runtime. Sources are pinned: the repository's game-data manifest fixes the
bundle version, and the public JSON export is identified by commit and SHA-256.
Pass --input-dir to reuse previously downloaded inputs, otherwise this script
downloads the pinned files into a temporary directory.

See THIRD_PARTY.md for provenance and licensing.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'vendor/ark-source/game_data/catalogs/stage_names.json'
MANIFEST = ROOT / 'vendor/ark-source/game_data/manifest.json'

REPOSITORY = 'https://github.com/Kengxxiao/ArknightsGameData'
COMMIT = '6f63c14e27b847ef74b53b00fe51fc322b3cd7ff'
BASE = f'https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/{COMMIT}/zh_CN/gamedata/excel/'
INPUTS = {
    'stage_table.json': dict(sha256='499319a48326de0f915c9f4b5e750a9bf946ad73b2bb56797ba8718af468ce84',
                             size=20160577),
    'roguelike_topic_table.json': dict(sha256='74d7b046f1f36f44d6dfd896817a35aa8f0144838dfb8e4386774adff4976353',
                                       size=18164948),
    'zone_table.json': dict(sha256='715df22acd2c87aa326858cfe8265db1d902afad07abf03e5a856e9f0e35474e',
                            size=392460),
}
ROGUELIKE = 'ROGUELIKE'


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def verify(path: Path, name: str) -> None:
    expected = INPUTS[name]
    if path.stat().st_size != expected['size'] or digest(path) != expected['sha256']:
        raise ValueError(f'{name} does not match the pinned game data; refusing to build')


def download(directory: Path) -> dict[str, Path]:
    paths = {}
    for name in INPUTS:
        target = directory / name
        with urlopen(BASE + name, timeout=120) as response:
            target.write_bytes(response.read())
        verify(target, name)
        paths[name] = target
    return paths


def zone_label(zone: dict | None) -> str:
    zone = zone or {}
    for field in ('zoneNameFirst', 'zoneNameSecond'):
        value = zone.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def build_catalog(stage_table: dict, roguelike_table: dict, zone_table: dict,
                  *, bundle_data_version: str) -> dict:
    """Pure table -> catalog transform; entries are [stageId, type, code, name, zone]."""
    zones = zone_table.get('zones') or {}
    stages = stage_table.get('stages') or {}
    entries: dict[str, list[str]] = {}
    aliases: dict[str, str] = {}

    def add(level_id: str, key: str, fields: list[str]) -> None:
        if level_id in entries:
            if key != entries[level_id][0]:
                aliases[key] = level_id
        else:
            entries[level_id] = [key, *fields]

    for key in sorted(stages):
        item = stages[key]
        if '#' in key or not isinstance(item, dict):
            continue  # difficulty variants share the normal level's levelId
        level_id = item.get('levelId')
        if not isinstance(level_id, str) or not level_id.strip():
            continue
        code = (item.get('code') or '').strip()
        name = (item.get('name') or '').strip()
        if not (code or name):
            continue
        add(level_id, key, [item.get('stageType') or 'UNKNOWN', code, name,
                            zone_label(zones.get(item.get('zoneId')))])
    details = roguelike_table.get('details') or {}
    topics = roguelike_table.get('topics') or {}
    for theme in sorted(details):
        theme_stages = details[theme].get('stages') or {}
        topic = topics.get(theme) or {}
        theme_name = (topic.get('name') if isinstance(topic, dict) else '') or theme
        # Several node variants share one levelId; prefer the normal node so
        # the catalog records the ordinary stage id.
        for key in sorted(theme_stages, key=lambda name: ('_e_' in name, name)):
            item = theme_stages[key]
            level_id = item.get('levelId') if isinstance(item, dict) else None
            if not isinstance(level_id, str) or not level_id.strip():
                continue
            code = (item.get('code') or '').strip()
            name = (item.get('name') or '').strip()
            if not (code or name):
                continue
            add(level_id, key, [ROGUELIKE, code, name, theme_name.strip()])
    return dict(schema_version=1, bundle_data_version=bundle_data_version,
                source=dict(repository=REPOSITORY, commit=COMMIT,
                            files={name: dict(sha256=meta['sha256'], size=meta['size'])
                                   for name, meta in INPUTS.items()}),
                aliases=dict(sorted(aliases.items())), stages=entries)


def render(catalog: dict) -> str:
    return json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, help='directory with the pinned JSON tables')
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--check', action='store_true', help='fail if the committed catalog differs')
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    version = manifest.get('data_version')
    if not isinstance(version, str) or not version:
        raise ValueError('game data manifest has no data_version')
    temporary = None
    try:
        if args.input_dir:
            paths = {name: args.input_dir / name for name in INPUTS}
        else:
            temporary = tempfile.TemporaryDirectory(prefix='arkdps-stage-names-')
            paths = download(Path(temporary.name))
        for name, path in paths.items():
            verify(path, name)
        tables = {name: json.loads(path.read_text(encoding='utf-8')) for name, path in paths.items()}
        catalog = build_catalog(tables['stage_table.json'], tables['roguelike_topic_table.json'],
                                tables['zone_table.json'], bundle_data_version=version)
        payload = render(catalog)
        if args.check:
            current = args.output.read_text(encoding='utf-8') if args.output.is_file() else ''
            if current != payload:
                raise SystemExit(f'{args.output} is stale; rerun without --check')
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding='utf-8')
        print(f'{len(catalog["stages"])} stage names, {len(catalog["aliases"])} aliases -> '
              f'{args.output} ({len(payload.encode("utf-8"))} bytes)')
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == '__main__':
    main()
