"""Load the bundled operator names used to label statistics."""
import json
from pathlib import Path


def load_character_names() -> dict[str, str]:
    catalog = Path(__file__).resolve().parent / 'vendor/ark-source/game_data/catalogs/char_names.json'
    names = json.loads(catalog.read_text(encoding='utf-8'))
    names.setdefault('char_618_acrbot', '暴躁铁皮')
    return names
