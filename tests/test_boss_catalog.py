"""Boss filtering follows exact handbook classifications."""

import json

import pytest

import boss_catalog


@pytest.mark.parametrize(('target', 'expected'), [
    ('enemy_1506_patrt', True),
    ('enemy_1007_slime', False),
    ('enemy_1018_aoemag', False),
    ('enemy_1506_patrt_unknown', None),
    ('ENEMY_1506_PATRT', None),
    ('爱国者', None),
    ('char_1506_patrt', False),
    ('token_1506_patrt', False),
    ('trap_1506_patrt', False),
    ('', None),
    (None, None),
])
def test_classification_uses_exact_native_ids(target, expected):
    assert boss_catalog.classify_boss(target) is expected


def test_catalog_keeps_unsupported_malformed_and_linked_targets_unknown(tmp_path, monkeypatch):
    path = tmp_path / 'enemy_handbook.json'
    path.write_text(json.dumps({'enemyData': {
        'enemy_boss': {'enemyId': 'enemy_boss', 'enemyLevel': 'BOSS',
                       'linkEnemies': ['enemy_link']},
        'enemy_new': {'enemyId': 'enemy_new', 'enemyLevel': 'UNKNOWN'},
        'enemy_other': {'enemyId': 'enemy_mismatch', 'enemyLevel': 'BOSS'},
        'enemy_invalid': {'enemyId': 'enemy_invalid', 'enemyLevel': []},
    }}), encoding='utf-8')
    monkeypatch.setattr(boss_catalog, 'CATALOG_PATH', path)
    assert boss_catalog.classify_boss('enemy_boss') is True
    for target in ('enemy_link', 'enemy_new', 'enemy_other', 'enemy_invalid'):
        assert boss_catalog.classify_boss(target) is None


def test_missing_catalog_does_not_guess_boss_status(tmp_path, monkeypatch):
    monkeypatch.setattr(boss_catalog, 'CATALOG_PATH', tmp_path / 'missing.json')
    assert boss_catalog.classify_boss('enemy_1506_patrt') is None
    assert boss_catalog.classify_boss('trap_example') is False
