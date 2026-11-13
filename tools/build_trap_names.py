"""Extract the display names of map devices from the pinned Chinese game table."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'vendor/ark-source/game_data/catalogs/trap_names.json'
REVISION = '6f63c14e27b847ef74b53b00fe51fc322b3cd7ff'
SOURCE = ('https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/'
          f'{REVISION}/zh_CN/gamedata/excel/character_table.json')
SOURCE_SHA256 = 'eb6fff6fb3ce605bd60db2206a644625a3d145b4c46fa126f218cd51df4f1f1d'
SOURCE_SIZE = 15_587_673


def build_catalog(source: bytes) -> dict:
    if len(source) != SOURCE_SIZE or sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError('character_table.json does not match the pinned source')
    table = json.loads(source)
    names = {
        key: row['name'].strip()
        for key, row in sorted(table.items())
        if key.startswith('trap_') and isinstance(row.get('name'), str) and row['name'].strip()
    }
    return dict(schema_version=1, source=SOURCE, source_sha256=SOURCE_SHA256, names=names)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, help='Reuse a copy of the pinned character_table.json')
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.input is not None:
        source = args.input.read_bytes()
    else:
        with urlopen(SOURCE, timeout=120) as response:
            source = response.read()
    payload = json.dumps(build_catalog(source), ensure_ascii=False, indent=2)
    args.output.write_text(payload + '\n', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
