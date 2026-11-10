"""Versioned local summaries. One acquisition owner writes; readers get copies."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from threading import RLock


POLICIES = {
    'damage': 'native_cumulative_including_overkill',
    'healing': 'native_cumulative_including_overheal',
    'shield_absorbed': 'observed_actual_absorption_only',
    'overkill': 'observed_settlement_value_minus_actual_hp_at_kill_after_shield',
    'recent': 'cumulative_delta_game_time_window_up_to_5s',
    'engagement': 'observed_events_separate_damage_healing_union_1.5s_tail',
    'ownership': 'verified_statistics_source_to_owner_no_guessing',
}
# Files written before overkill existed keep their original policy map and are
# read with overkill unknown rather than being rejected as unsupported.
LEGACY_POLICIES = {key: value for key, value in POLICIES.items() if key != 'overkill'}
_LOCK = RLock()


class HistoryError(ValueError):
    """A failed validation/write; reliable files have not been overwritten."""


def _require(condition, message):
    if not condition:
        raise HistoryError(message)


def _number(value, name, *, nullable=False):
    if nullable and value is None:
        return
    _require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
             f'invalid {name}')


def _date(value):
    _require(isinstance(value, str), 'invalid timestamp')
    try:
        parsed = datetime.fromisoformat(value)
        _require(parsed.utcoffset() is not None, 'timestamp needs timezone')
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise HistoryError(f'invalid timestamp: {value}') from exc


def _intervals(value, end):
    _require(isinstance(value, list), 'invalid snapshot intervals')
    previous = 0
    for interval in value:
        _require(isinstance(interval, list) and len(interval) == 2, 'invalid interval')
        start, stop = interval
        _number(start, 'interval start')
        _number(stop, 'interval end')
        _require(previous <= start <= stop <= end, 'unordered/out-of-range interval')
        previous = stop


class BattleRecord:
    """Validated JSON summary; null and numeric zero retain their meanings."""

    def __init__(self, data):
        self._data = data

    @classmethod
    def from_archive(cls, archive):
        data = deepcopy(archive)
        stats = data.get('statistics', {})
        observed = (data.get('last_sample') or {}).get('observed_at')
        if observed is not None:
            _number(observed, 'observed_at')
            try:
                observed = datetime.fromtimestamp(observed, timezone.utc).isoformat()
            except (ValueError, OverflowError, OSError) as exc:
                raise HistoryError('invalid observed_at') from exc
        data.update(schema_version=1, recorded_at=datetime.now(timezone.utc).isoformat(),
                    observed_at=observed, duration=stats.get('game_time'),
                    policies=deepcopy(POLICIES))
        # Older handoffs lack interval history; unknown must not imply full coverage.
        data.setdefault('snapshot_intervals', None)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data):
        try:
            # Normalize tuples, reject NaN/infinity and non-JSON source objects.
            data = json.loads(json.dumps(data, ensure_ascii=False, allow_nan=False))
            _require(isinstance(data, dict), 'record must be an object')
            _require(type(data.get('schema_version')) is int and data['schema_version'] == 1,
                     'unsupported BattleRecord schema_version')
            required = {'battle_id', 'generation', 'reason', 'metadata', 'statistics',
                        'last_sample', 'event_status', 'event_error', 'recorded_at',
                        'observed_at', 'duration', 'policies', 'snapshot_intervals'}
            _require(required <= data.keys(), 'missing BattleRecord fields')
            battle_id = data['battle_id']
            _require(isinstance(battle_id, str) and bool(battle_id.strip()), 'invalid battle_id')
            _require(type(data['generation']) is int and data['generation'] > 0, 'invalid generation')
            _require(isinstance(data['reason'], str) and bool(data['reason']), 'invalid reason')
            _date(data['recorded_at'])
            if data['observed_at'] is not None:
                _date(data['observed_at'])
            _require(data['policies'] in (POLICIES, LEGACY_POLICIES),
                     'unsupported statistics policies')
            data['policies'] = deepcopy(POLICIES)
            _number(data['duration'], 'duration')
            metadata = data['metadata']
            _require(isinstance(metadata, dict), 'invalid metadata')
            _require({'stage_id', 'practice', 'outcome', 'evidence'} <= metadata.keys(),
                     'missing metadata fields')
            _require(metadata.get('outcome') in ('victory', 'defeat', 'abandoned', 'unknown'),
                     'invalid outcome')
            _require(metadata.get('practice') is None or type(metadata['practice']) is bool,
                     'invalid practice')
            _require(metadata.get('stage_id') is None or isinstance(metadata['stage_id'], str),
                     'invalid stage_id')
            stats = data['statistics']
            _require(isinstance(stats, dict) and stats.get('ended') is True, 'statistics not frozen')
            _require(stats.get('session') == battle_id and stats.get('adapter_session') == battle_id,
                     'statistics battle_id mismatch')
            _require(stats.get('game_time') == data['duration'], 'duration mismatch')
            _number(stats['game_time'], 'game_time')
            _number(stats['coverage_start'], 'coverage_start')
            _require(stats['coverage_start'] <= data['duration'], 'coverage starts after end')
            _require(isinstance(stats['rows'], dict), 'invalid statistics rows')
            for key, default in (('overkill_damage', None),
                                 ('overkill_coverage_complete', False),
                                 ('overkill_coverage_start', None),
                                 ('overkill_coverage_end', None)):
                stats.setdefault(key, default)
            _number(stats['overkill_damage'], 'overkill_damage', nullable=True)
            _require(type(stats['overkill_coverage_complete']) is bool,
                     'invalid overkill_coverage_complete')
            _number(stats['overkill_coverage_start'], 'overkill_coverage_start', nullable=True)
            _number(stats['overkill_coverage_end'], 'overkill_coverage_end', nullable=True)
            if stats['overkill_coverage_start'] is not None:
                _require(stats['overkill_coverage_end'] is not None
                         and 0 <= stats['overkill_coverage_start']
                         <= stats['overkill_coverage_end'] <= data['duration'],
                         'invalid overkill coverage interval')
            for owner, row in stats['rows'].items():
                _require(bool(owner) and isinstance(row, dict), 'invalid owner row')
                for key, default in (('overkill_damage', None),
                                     ('overkill_coverage_complete', False),
                                     ('overkill_coverage_start', None),
                                     ('overkill_coverage_end', None)):
                    row.setdefault(key, default)
                for metric in ('damage', 'healing', 'shield_absorbed', 'recent_damage',
                               'recent_healing', 'engagement_damage', 'engagement_healing',
                               'active_damage', 'active_healing', 'observed_damage', 'observed_healing',
                               'shield_coverage_start', 'event_coverage_end',
                               'overkill_damage', 'overkill_coverage_start',
                               'overkill_coverage_end'):
                    _number(row[metric], metric, nullable=True)
                _number(row['coverage_start'], 'row coverage_start')
                _require(row['coverage_start'] <= data['duration'], 'invalid row coverage start')
                for flag in ('counter_snapshot_complete', 'event_coverage_complete',
                             'overkill_coverage_complete', 'types_available', 'types_stale'):
                    _require(type(row[flag]) is bool, f'invalid {flag}')
                _require(row['present'] is None or type(row['present']) is bool, 'invalid presence')
                _require(isinstance(row['sources'], list) and
                         all(isinstance(s, str) and s for s in row['sources']), 'invalid sources')
                _require(isinstance(row['name'], str), 'invalid name')
                _require(isinstance(row['damage_by_type'], dict), 'invalid damage types')
                for value in row['damage_by_type'].values():
                    _number(value, 'damage type')
                for kind in ('damage', 'healing'):
                    window = row[f'recent_{kind}_window']
                    if window is not None:
                        _intervals([window], data['duration'])
                start, end = row['shield_coverage_start'], row['event_coverage_end']
                if start is not None:
                    _require(end is not None and 0 <= start <= end <= data['duration'],
                             'invalid event coverage interval')
                start, end = row['overkill_coverage_start'], row['overkill_coverage_end']
                if start is not None:
                    _require(end is not None and 0 <= start <= end <= data['duration'],
                             'invalid overkill coverage interval')
            for field in ('unknown_sources', 'unmapped_tokens', 'ambiguous_sources',
                          'mapping_conflicts', 'missing_fields'):
                _require(isinstance(stats[field], list) and
                         all(isinstance(s, str) for s in stats[field]), f'invalid {field}')
            sample = data['last_sample']
            _require(isinstance(sample, dict) and sample.get('session') == battle_id,
                     'last sample battle_id mismatch')
            _require(isinstance(data['event_status'], str), 'invalid event_status')
            _require(data['event_error'] is None or isinstance(data['event_error'], str),
                     'invalid event_error')
            if data['snapshot_intervals'] is not None:
                _intervals(data['snapshot_intervals'], data['duration'])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise HistoryError(f'invalid BattleRecord: {exc}') from exc
        return cls(data)

    @property
    def battle_id(self):
        return self._data['battle_id']

    @property
    def statistics(self):
        return deepcopy(self._data['statistics'])

    @property
    def metadata(self):
        return deepcopy(self._data['metadata'])

    def to_dict(self):
        return deepcopy(self._data)


@dataclass(frozen=True)
class HistoryDiagnostic:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class HistoryResult:
    records: tuple = ()
    diagnostics: tuple = ()
    written: bool = False


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f'duplicate JSON key: {key}')
        result[key] = value
    return result


class BattleHistory:
    """Atomic JSON files, durable ID deduplication, and newest-100 read API.

    Retention counts only validated records. Invalid/unknown files stay intact.
    The app's single acquisition owner performs all writes; the lock also
    serializes writers/readers in this process, including separate instances.
    """

    def __init__(self, directory):
        self.directory = Path(directory)

    def path_for(self, battle_id):
        _require(isinstance(battle_id, str) and bool(battle_id.strip()), 'invalid battle_id')
        return self.directory / (hashlib.sha256(battle_id.encode('utf-8')).hexdigest() + '.json')

    def _read(self, path):
        with path.open(encoding='utf-8') as stream:
            record = BattleRecord.from_dict(json.load(stream, object_pairs_hook=_unique_keys))
        _require(self.path_for(record.battle_id) == path, 'filename battle_id mismatch')
        return record

    def _scan(self):
        records, diagnostics = [], []
        try:
            paths = list(self.directory.iterdir())
        except FileNotFoundError:
            return records, diagnostics
        except OSError as exc:
            return records, [HistoryDiagnostic('read_failed', str(self.directory), str(exc))]
        for path in paths:
            if path.suffix != '.json':
                continue
            try:
                records.append(self._read(path))
            except (OSError, ValueError, UnicodeError) as exc:
                diagnostics.append(HistoryDiagnostic('invalid_record', str(path), str(exc)))
        records.sort(key=lambda r: (_date(r._data['observed_at'] or r._data['recorded_at']),
                                    r.battle_id), reverse=True)
        return records, diagnostics

    def list_recent(self):
        with _LOCK:
            records, diagnostics = self._scan()
            return HistoryResult(tuple(records[:100]), tuple(diagnostics))

    def get(self, battle_id):
        path = self.path_for(battle_id)
        with _LOCK:
            try:
                return HistoryResult((self._read(path),))
            except FileNotFoundError:
                return HistoryResult()
            except (OSError, ValueError, UnicodeError) as exc:
                return HistoryResult(diagnostics=(HistoryDiagnostic('invalid_record', str(path), str(exc)),))

    def save(self, record):
        record = BattleRecord.from_dict(record.to_dict())
        path = self.path_for(record.battle_id)
        with _LOCK:
            temporary = None
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                if path.exists():
                    existing = self._read(path)
                    before, after = existing.to_dict(), record.to_dict()
                    before.pop('recorded_at')
                    after.pop('recorded_at')
                    _require(before == after, 'battle_id conflict; original record retained')
                    return self._retention(existing, written=False)
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.directory,
                                                 prefix='.battle-', suffix='.tmp', delete=False) as stream:
                    temporary = Path(stream.name)
                    json.dump(record.to_dict(), stream, ensure_ascii=False, allow_nan=False,
                              sort_keys=True, separators=(',', ':'))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                temporary = None
                self._read(path)
            except (OSError, ValueError, UnicodeError) as exc:
                raise HistoryError(f'history write failed ({path.name}): {exc}') from exc
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink()
                    except OSError:
                        pass
            return self._retention(record, written=True)

    def _retention(self, record, *, written):
        # Never evict reliable history before a summary has committed and been
        # read back. Duplicate handoffs can retry an earlier failed eviction.
        records, diagnostics = self._scan()
        for old in records[100:]:
            old_path = self.path_for(old.battle_id)
            try:
                old_path.unlink()
            except OSError as exc:
                diagnostics.append(HistoryDiagnostic('prune_failed', str(old_path), str(exc)))
        return HistoryResult((record,), tuple(diagnostics), written=written)
