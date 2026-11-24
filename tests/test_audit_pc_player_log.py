"""Check the metadata-only scanner against ordinary files, not a game process."""

import hashlib
import json

from acceptance.audit_pc_player_log import capture_player_log


def test_counts_completed_metadata_without_reporting_log_contents(tmp_path):
    path = tmp_path / 'Player.log'
    data = (
        '[INFO] battle entered at frame=21\n'
        '  at damage handler (Player.cs:22) frame=21\n'
        '{"event":"healing","operator":"PRIVATE_PLAYER_TEST_用户"}\n'
        '{"event":"damage","amount":17.5,"target":"PRIVATE_TARGET_TEST"}\n'
        'shield hp_before=40\n'
        '\n'
    ).encode('utf-8')
    path.write_bytes(data)

    result = capture_player_log(path)

    assert result['status'] == 'scanned'
    assert result['scope'] == 'whole_file'
    assert result['size'] == result['last_complete_byte'] == len(data)
    assert result['sha256'] == hashlib.sha256(data).hexdigest()
    assert result['identity'] is not None
    assert result['lines']['nonblank'] == 5
    assert result['lines']['json_objects'] == 2
    assert result['lines']['event_and_value'] == 2
    assert result['lines']['term_lines']['battle'] == 1
    assert result['lines']['term_lines']['healing'] == 1
    assert result['lines']['term_lines']['shield'] == 1
    encoded = json.dumps(result, ensure_ascii=False)
    for private in ('PRIVATE_PLAYER_TEST_用户', 'PRIVATE_TARGET_TEST', 'Player.cs:22', 'hp_before=40'):
        assert private not in encoded


def test_baseline_scans_only_new_complete_lines(tmp_path):
    path = tmp_path / 'Player.log'
    before = b'{"event":"damage","amount":4}\n'
    after = b'battle leaving frame=200\n{"event":"healing","amount":7}\n'
    path.write_bytes(before)
    baseline = capture_player_log(path)
    assert baseline['lines']['event_and_value'] == 1

    with path.open('ab') as stream:
        stream.write(after)
    result = capture_player_log(path, baseline=baseline)

    assert result['status'] == 'scanned'
    assert result['scope'] == 'appended_lines'
    assert result['identity'] == baseline['identity']
    assert result['size'] == result['last_complete_byte'] == len(before + after)
    assert result['sha256'] == hashlib.sha256(before + after).hexdigest()
    assert result['lines']['nonblank'] == 2
    assert result['lines']['json_objects'] == result['lines']['event_and_value'] == 1
    assert result['lines']['term_lines']['damage'] == 0
    assert result['lines']['term_lines']['healing'] == 1


def test_incomplete_final_line_is_counted_only_after_it_is_completed(tmp_path):
    path = tmp_path / 'Player.log'
    complete = b'battle ready\n'
    partial = b'{"event":"damage","amount":17'
    path.write_bytes(complete + partial)
    baseline = capture_player_log(path)
    assert baseline['last_complete_byte'] == len(complete)
    assert baseline['size'] == len(complete + partial)
    assert baseline['sha256'] == hashlib.sha256(complete + partial).hexdigest()
    assert baseline['lines']['nonblank'] == 1
    assert baseline['lines']['event_and_value'] == 0

    with path.open('ab') as stream:
        stream.write(b'.5}\n')
    result = capture_player_log(path, baseline=baseline)

    assert result['status'] == 'scanned'
    assert result['scope'] == 'appended_lines'
    assert result['last_complete_byte'] == result['size']
    assert result['lines']['nonblank'] == 1
    assert result['lines']['json_objects'] == result['lines']['event_and_value'] == 1


def test_rewrite_and_replacement_are_not_accepted_as_appends(tmp_path):
    path = tmp_path / 'Player.log'
    original = b'{"event":"damage","amount":4}\n'
    path.write_bytes(original)
    baseline = capture_player_log(path)

    # Same file identity and size, but the bytes previously measured changed.
    path.write_bytes(original.replace(b'4', b'8'))
    assert capture_player_log(path, baseline=baseline)['status'] == 'not_append_only'

    # Identical prefix but a new file identity: a rotated log is not a delta.
    replacement = tmp_path / 'Player-new.log'
    replacement.write_bytes(original + b'shield amount=1\n')
    replacement.replace(path)
    assert capture_player_log(path, baseline=baseline)['status'] == 'not_append_only'


def test_missing_file_is_not_a_successful_empty_scan(tmp_path):
    assert capture_player_log(tmp_path / 'Player.log')['status'] == 'missing'


def test_uses_physical_lines_even_with_unicode_separator_inside_json(tmp_path):
    path = tmp_path / 'Player.log'
    record = json.dumps({'event': 'damage', 'label': 'a\u2028b', 'amount': 4}, ensure_ascii=False)
    path.write_bytes((record + '\r\n').encode('utf-8'))
    result = capture_player_log(path)['lines']
    assert result['nonblank'] == result['json_objects'] == result['event_and_value'] == 1


def test_file_limit_is_checked_before_scanning(tmp_path, monkeypatch):
    monkeypatch.setattr('acceptance.audit_pc_player_log.MAX_LOG_BYTES', 4)
    path = tmp_path / 'Player.log'
    path.write_bytes(b'1234\n')
    assert capture_player_log(path)['status'] == 'too_large'
