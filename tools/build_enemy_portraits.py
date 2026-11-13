"""Rebuild bundled 48px enemy or trap portrait atlases (requires Pillow).

No downloads occur in the application. See THIRD_PARTY.md for asset provenance.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
REVISION = '62fe51e6cd7faa5c1698e05f8cf63ed61cf6a7ac'
BASE = f'https://raw.githubusercontent.com/yuanyan3060/ArknightsGameResource/{REVISION}/'
TREE = ('https://api.github.com/repos/yuanyan3060/ArknightsGameResource/'
        f'git/trees/{REVISION}?recursive=1')


def portrait_keys(folder, prefix, tree_file):
    if tree_file is None:
        with urlopen(TREE, timeout=60) as response:
            payload = json.load(response)
    else:
        payload = json.loads(tree_file.read_text(encoding='utf-8-sig'))
    if payload.get('sha') != REVISION or payload.get('truncated'):
        raise ValueError('Portrait source tree is incomplete or from a different revision')
    return sorted(Path(item['path']).stem for item in payload['tree']
                  if item['path'].startswith(f'{folder}/{prefix}') and item['path'].endswith('.png'))


def fetch(key, base):
    url = base + key + '.png'
    for attempt in range(3):
        try:
            with urlopen(url, timeout=45) as response:
                raw = response.read()
            image = Image.open(BytesIO(raw)).convert('RGBA').resize(
                (48, 48), Image.Resampling.LANCZOS)
            return key, image, sha256(raw).hexdigest()
        except (URLError, TimeoutError) as exc:
            if attempt == 2 or isinstance(exc, HTTPError) and exc.code == 404:
                raise RuntimeError(f'Could not download pinned portrait {key}: {exc}') from exc


def build(kind, tree_file=None):
    folder, prefix = ('avatar', 'trap_') if kind == 'trap' else ('enemy', '')
    keys = portrait_keys(folder, prefix, tree_file)
    if not keys:
        raise ValueError(f'No {kind} portraits found')
    base = f'{BASE}{folder}/'
    output = ROOT / 'assets' / f'{kind}_portraits'
    output.mkdir(parents=True, exist_ok=True)
    manifest = dict(source=base, revision=REVISION, cell_size=48, columns=16, portraits={})
    with ThreadPoolExecutor(max_workers=16) as pool:
        portraits = list(pool.map(lambda key: fetch(key, base), keys))
    normal = Image.new('RGBA', (48 * 16, 48 * ((len(portraits) + 15) // 16)), '#26343d')
    muted = normal.copy()
    for index, (key, image, digest) in enumerate(portraits):
        xy = (index % 16 * 48, index // 16 * 48)
        normal.paste(image, xy, image)
        gray = ImageOps.grayscale(image).convert('RGBA')
        gray.putalpha(image.getchannel('A'))
        muted.paste(gray, xy, gray)
        manifest['portraits'][key] = dict(index=index, source_sha256=digest)
    for name, atlas in (('normal.png', normal), ('muted.png', muted)):
        atlas.save(output / name, optimize=True)
        manifest[name] = sha256((output / name).read_bytes()).hexdigest()
    (output / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'{len(portraits)} {kind} portraits; offline atlases ready')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=('enemy', 'trap'), default='enemy')
    parser.add_argument('--tree-file', type=Path, help='Reuse the recursive GitHub tree response')
    args = parser.parse_args()
    build(args.kind, args.tree_file)


if __name__ == '__main__':
    main()
