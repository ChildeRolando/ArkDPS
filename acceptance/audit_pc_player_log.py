"""Audit normal Unity player logs for potential PC battle events without copying raw lines."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat


FILES = ('Player.log', 'Player-prev.log')
MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_RECEIPT_BYTES = 64 * 1024
TERMS = {name: re.compile(pattern, re.IGNORECASE) for name, pattern in {
    'battle': r'battle|combat|战斗|作战',
    'damage': r'damage|伤害|扣血',
    'healing': r'healing|heal\b|治疗|回血',
    'shield': r'shield|absorb|护盾|吸收',
    'skill': r'skill|技能',
    'settlement': r'settle|结算',
}.items()}
VALUE = re.compile(
    r'\b(?:amount|value|hp(?:[-_]?(?:before|after))?|absorbed)["\x27]?\s*[:=]\s*["\x27]?[-+]?\d',
    re.IGNORECASE)


class _UnstableLog(Exception):
    pass


def _identity(info):
    return [info.st_dev, info.st_ino]


def _stamp(info):
    return _identity(info), info.st_size, info.st_mtime_ns


def _read_stable(path):
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            return 'unreadable', None, None
        if before.st_size > MAX_LOG_BYTES:
            return 'too_large', None, None
        payload = stream.read(before.st_size)
        after = os.fstat(stream.fileno())
        try:
            selected = path.stat()
        except FileNotFoundError as exc:
            raise _UnstableLog from exc
        if len(payload) != before.st_size or _stamp(before) != _stamp(after) or _stamp(before) != _stamp(selected):
            raise _UnstableLog
        return 'scanned', before, payload


def _scan(payload):
    text = payload.decode('utf-8-sig', errors='replace')
    lines = dict(nonblank=0, json_objects=0, term_lines=dict.fromkeys(TERMS, 0), event_and_value=0,
                 replacement_characters=text.count('\ufffd'))
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        lines['nonblank'] += 1
        families = [name for name, pattern in TERMS.items() if pattern.search(line)]
        for name in families:
            lines['term_lines'][name] += 1
        if any(name != 'battle' for name in families) and VALUE.search(line):
            lines['event_and_value'] += 1
        if line.startswith('{'):
            try:
                if isinstance(json.loads(line), dict):
                    lines['json_objects'] += 1
            except (ValueError, RecursionError):
                pass
    return lines


def _append_offset(baseline, info, payload):
    offset, size = baseline.get('last_complete_byte'), baseline.get('size')
    if (baseline.get('status') != 'scanned' or baseline.get('identity') != _identity(info)
            or type(size) is not int or type(offset) is not int or not 0 <= offset <= size <= len(payload)):
        return None
    if (hashlib.sha256(payload[:size]).hexdigest() != baseline.get('sha256')
            or offset != payload.rfind(b'\n', 0, size) + 1):
        return None
    # Scan a now-complete line even if its first bytes predate the baseline.
    return offset


def capture_player_log(path, *, baseline=None):
    """Summarize complete lines; with a baseline, only admit a verified appended suffix."""
    path = Path(path)
    for _ in range(3):
        try:
            status, info, payload = _read_stable(path)
        except FileNotFoundError:
            return dict(status='missing')
        except OSError:
            return dict(status='unreadable')
        except _UnstableLog:
            continue
        if status != 'scanned':
            return dict(status=status)
        break
    else:
        return dict(status='unstable')
    offset = _append_offset(baseline, info, payload) if baseline is not None else 0
    if offset is None:
        return dict(status='not_append_only')
    complete = payload.rfind(b'\n') + 1
    digest = hashlib.sha256(payload).hexdigest()
    return dict(status='scanned', scope='appended_lines' if baseline is not None else 'whole_file',
                size=len(payload), identity=_identity(info), sha256=digest, last_complete_byte=complete,
                source_mtime_utc=datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(timespec='seconds'),
                analyzed_bytes=complete - offset, lines=_scan(payload[offset:complete]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log-dir', type=Path, default=Path.home() / 'AppData/LocalLow/Hypergryph/Arknights')
    parser.add_argument('--baseline', type=Path, help='Previous receipt; compare only a verified appended suffix')
    args = parser.parse_args()
    prior = None
    if args.baseline is not None:
        try:
            with args.baseline.open('rb') as stream:
                data = stream.read(MAX_RECEIPT_BYTES + 1)
            if len(data) > MAX_RECEIPT_BYTES:
                raise ValueError
            receipt = json.loads(data.decode('utf-8-sig'))
            prior = receipt.get('logs') if isinstance(receipt, dict) and receipt.get('schema_version') == 1 else None
            if not isinstance(prior, dict) or any(not isinstance(prior.get(name), dict) for name in FILES):
                raise ValueError
        except (OSError, ValueError, RecursionError):
            parser.error('baseline must be a receipt from this audit command')
    logs = {name: capture_player_log(args.log_dir / name, baseline=None if prior is None else prior[name])
            for name in FILES}
    print(json.dumps(dict(schema_version=1, source='ordinary_pc_player_logs', game_build='unverified',
                          captured_utc=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                          battle_log_names={name: (args.log_dir / name).exists()
                                            for name in ('BattleLog', 'BattleLog.log', 'MultiBattleLog')},
                          logs=logs), ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
