"""Load the bundled operator names used to label statistics."""
import json
from pathlib import Path


def load_character_names() -> dict[str, str]:
    catalog = Path(__file__).resolve().parent / 'vendor/ark-source/game_data/catalogs/char_names.json'
    return json.loads(catalog.read_text(encoding='utf-8'))
