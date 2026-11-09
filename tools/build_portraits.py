"""Rebuild bundled 48px portrait atlases (build-only dependency: Pillow).

No downloads occur in the application. See THIRD_PARTY.md for asset provenance.
"""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
REVISION = '62fe51e6cd7faa5c1698e05f8cf63ed61cf6a7ac'
BASE = f'https://raw.githubusercontent.com/yuanyan3060/ArknightsGameResource/{REVISION}/avatar/'


def fetch(key):
    url = BASE + key + '.png'
    try:
        with urlopen(url, timeout=30) as response:
            raw = response.read()
    except HTTPError as exc:
        if exc.code == 404:
            return key, None, None
        raise
    image = Image.open(BytesIO(raw)).convert('RGBA').resize((48, 48), Image.Resampling.LANCZOS)
    return key, image, sha256(raw).hexdigest()


def main():
    names = json.loads((ROOT / 'vendor/ark-source/game_data/catalogs/char_names.json').read_text('utf-8'))
    keys = sorted(key for key in names if key.startswith('char_'))
    output = ROOT / 'assets/portraits'
    output.mkdir(parents=True, exist_ok=True)
    manifest = dict(source=BASE, revision=REVISION, cell_size=48, columns=16, portraits={}, missing=[])
    with ThreadPoolExecutor(max_workers=8) as pool:
        portraits = list(pool.map(fetch, keys))
    found = [(key, image, digest) for key, image, digest in portraits if image is not None]
    normal = Image.new('RGBA', (48 * 16, 48 * ((len(found) + 15) // 16)), '#26343d')
    muted = normal.copy()
    for index, (key, image, digest) in enumerate(found):
        xy = (index % 16 * 48, index // 16 * 48)
        normal.paste(image, xy, image)
        gray = ImageOps.grayscale(image).convert('RGBA')
        gray.putalpha(image.getchannel('A'))
        muted.paste(gray, xy, gray)
        manifest['portraits'][key] = dict(index=index, source_sha256=digest)
    manifest['missing'] = [key for key, image, _ in portraits if image is None]
    for name, atlas in (('normal.png', normal), ('muted.png', muted)):
        atlas.save(output / name, optimize=True)
        manifest[name] = sha256((output / name).read_bytes()).hexdigest()
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'{len(found)} portraits; {len(manifest["missing"])} missing; offline atlases ready')


if __name__ == '__main__':
    main()
