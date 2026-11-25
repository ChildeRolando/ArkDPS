"""Read-only sharing of a validated BattleRecord, never a live sample."""
from datetime import datetime
from functools import cache
import json
import os
from pathlib import Path
import re
import tempfile

from autochess_context import NATIVE_AUTOCHESS_CHARACTER_COUNTERS
from battle_history import BattleRecord
from character_catalog import load_character_names
from panel_model import DAMAGE_ORDER, DIAGNOSTIC_LABELS, team_status
from source_classification import classify_unmapped_source
import stage_names


OUTCOMES = {'victory': '胜利', 'defeat': '失败', 'abandoned': '退出', 'unknown': '结果未知'}
AUTOCHESS_SNAPSHOT_LABEL = '卫戍快照 · 玩家归属未知'
AUTOCHESS_SNAPSHOT_NOTE = '按角色键记录客户端累计伤害和治疗。玩家归属、回合及整场结果未知。'
_CHARACTER_KEY = re.compile(r'[A-Za-z0-9_#.-]{1,110}')
_history_character_names = cache(load_character_names)


def record_labels(record):
    return record_display(record)['labels']


def record_display(record):
    """Metadata for the history browser and the share header."""
    data = record.display_metadata()
    metadata = data['metadata']
    stage = stage_names.resolve(metadata['stage_id'])
    unverified = record.unverified_autochess_scope
    practice = {True: '演习', False: '正式', None: '演习状态未知'}[None if unverified else metadata['practice']]
    outcome = OUTCOMES['unknown' if unverified else metadata['outcome']]
    duration = f'{data["duration"]:.1f} 秒'
    duration_label = f'采样至 {duration}' if unverified else f'{duration}（最后可靠游戏时刻）'
    date = data['observed_at'] or data['recorded_at']
    date_label = '最后观察' if data['observed_at'] else '记录时间（观察时间未知）'
    observed_date = datetime.fromisoformat(date)
    try:
        observed_date = observed_date.astimezone()
    except (ValueError, OverflowError, OSError):
        # Older valid archives can predate the operating system's time zone table.
        pass
    platform = metadata.get('platform')
    source_label = ('PC' if platform == 'native_pc' else
                    'Android（ADB）' if platform == 'android' else '来源未记录')
    display = dict(battle_id=record.battle_id, stage=stage.label, stage_description=stage.description,
                stage_detail=stage.detail, stage_raw=stage.raw, outcome=outcome,
                duration=duration_label if unverified else duration,
                source_label=source_label,
                date=f'{observed_date:%Y-%m-%d %H:%M}', labels=(stage.label, practice, outcome,
                    duration_label, f'{date_label} {date}', source_label))
    if platform in ('native_pc', 'android'):
        display['platform'] = platform
    if unverified:
        display.update(scope_warning=AUTOCHESS_SNAPSHOT_LABEL, labels=(*display['labels'], AUTOCHESS_SNAPSHOT_LABEL))
    return display


def record_stage(record):
    """Readable stage label plus the untouched native stage_id."""
    return stage_names.resolve(record.metadata['stage_id'])


def _legacy_totals_complete(stats, sample):
    """Infer cumulative-totals completeness for pre-ARK-24 records.

    Earlier builds cleared every source's ``counter_snapshot_complete`` at the
    battle boundary, so a False flag on an ended record does not by itself mean
    a collection gap. Fall back only on positive evidence: the frozen result is
    ended, no diagnostic reports an unknown/incomplete source, the final native
    sample is a complete FINISHED (state 3) frame, and every row's sources were
    present in that sample with both cumulative counters.
    """
    if stats.get('ended') is not True:
        return False
    if any(stats.get(key) for key, _ in DIAGNOSTIC_LABELS):
        return False
    guard = sample.get('guard')
    guard = guard if isinstance(guard, dict) else {}
    native_rows = sample.get('rows')
    if (sample.get('state') != 3 or guard.get('complete') is not True
            or not isinstance(native_rows, list)):
        return False
    native_ids = {item.get('id') for item in native_rows if isinstance(item, dict)}
    for row in stats['rows'].values():
        if row.get('damage') is None or row.get('healing') is None:
            return False
        if not set(row.get('sources') or ()) <= native_ids:
            return False
    return True


def _native_output(sample):
    """Last native cumulative rows, used only to label unmapped sources."""
    rows = sample.get('rows')
    if not isinstance(rows, list):
        return {}
    return {item.get('id'): item for item in rows if isinstance(item, dict)}


def _legacy_autochess_rows(sample):
    """Project only the last native CID counters from archives predating scoped snapshots."""
    raw = sample.get('rows')
    if not isinstance(raw, list) or not raw:
        return {}
    names = _history_character_names()
    rows, seen = {}, set()

    def amount(value):
        # Native rows are floats with each typed component at most 1e15.
        return value if type(value) in (int, float) and 0 <= value <= 4e15 else None

    for item in raw:
        cid = item.get('id') if isinstance(item, dict) else None
        if not isinstance(cid, str) or not _CHARACTER_KEY.fullmatch(cid):
            continue
        if cid in seen:
            rows.pop(cid, None)
            continue
        seen.add(cid)
        damage, healing = amount(item.get('damage')), amount(item.get('healing'))
        if damage is None and healing is None:
            continue
        by_type = {key: value for key in DAMAGE_ORDER if (value := amount(item.get(key))) is not None}
        rows[cid] = dict(name=names.get(cid, cid), damage=damage, healing=healing, damage_by_type=by_type,
                         sources=[cid], counter_snapshot_complete=damage is not None and healing is not None)
    return rows


def _legacy_non_team(stats, native):
    """Move recognised non-team keys out of a pre-ARK-24 unknown list.

    Old records classified nothing, so a known map-device key was written as
    unknown and blocked shares forever. The classification rule is pure over
    the statistic key; output amounts come from the record's final native rows.
    """
    unknown = stats.get('unknown_sources') or []
    if not isinstance(unknown, list):
        return
    blocked = set()
    for key in ('unmapped_tokens', 'ambiguous_sources', 'mapping_conflicts'):
        blocked.update(item for item in stats.get(key) or () if isinstance(item, str))
    remaining, moved, output = [], [], {}
    for cid in unknown:
        if (isinstance(cid, str) and cid not in blocked
                and classify_unmapped_source(cid) is not None):
            moved.append(cid)
            row = native.get(cid) or {}
            damage, healing = row.get('damage'), row.get('healing')
            valid = (isinstance(damage, (int, float)) and damage >= 0
                     and isinstance(healing, (int, float)) and healing >= 0)
            output[cid] = {'damage': damage if valid else None,
                           'healing': healing if valid else None}
        else:
            remaining.append(cid)
    stats['unknown_sources'] = remaining
    stats['non_team_sources'] = sorted(moved)
    stats['non_team_output'] = output


def record_statistics(record, *, for_panel=False):
    """Return frozen statistics with totals completeness resolved for display.

    New records carry ``totals_complete`` and the source classification from
    the statistics core. Legacy records are normalized from the evidence
    above, including reclassifying recognised non-team keys; missing totals
    stay incomplete instead of being zero-filled.
    """
    if for_panel:
        unverified = record.unverified_autochess_scope
        if unverified:
            sample = record.last_sample
            if sample.get('native_counter_scope') != NATIVE_AUTOCHESS_CHARACTER_COUNTERS:
                return dict(rows=_legacy_autochess_rows(sample), ended=True, totals_complete=False,
                            display_scope_label=AUTOCHESS_SNAPSHOT_LABEL)
        fields = ('rows', 'ended', 'totals_complete', 'non_team_sources', 'non_team_output',
                  'boss_damage_available', 'boss_damage_reason', 'settlement_unassigned',
                  *(key for key, _ in DIAGNOSTIC_LABELS))
        stats = record.statistics_fields(fields)
        if unverified:
            return dict(stats, totals_complete=False, display_scope_label=AUTOCHESS_SNAPSHOT_LABEL)
    else:
        stats = record.statistics
    if 'non_team_sources' in stats and 'totals_complete' in stats:
        return stats
    sample = record.last_sample
    if 'non_team_sources' not in stats:
        _legacy_non_team(stats, _native_output(sample))
    if 'totals_complete' not in stats:
        complete = _legacy_totals_complete(stats, sample)
        stats['totals_complete'] = complete
        if complete:
            for row in stats['rows'].values():
                row['counter_snapshot_complete'] = True
    return stats


def record_status(record):
    return team_status(record_statistics(record, for_panel=True))



def record_complete(record):
    # Same completeness rule as BattleRuntime.display_sample, applied to the frozen
    # result. last_sample is native input, not the live compatibility projection.
    return record_status(record)[0]


def json_bytes(record):
    # Validate a fresh copy; never replace raw values with compact display values
    # or compatibility rows.
    data = BattleRecord.from_dict(record.to_dict()).to_dict()
    # The raw stage_id stays authoritative; the display block only adds the
    # resolved name and any warning needed to interpret the data.
    data['display'] = {'stage': stage_names.display_fields(data['metadata']['stage_id'])}
    if record.unverified_autochess_scope:
        data['display']['scope_warning'] = AUTOCHESS_SNAPSHOT_NOTE
    return (json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')


def export_path(path, history_directory):
    """Reject archive paths, including directory and file symlink aliases."""
    target = Path(path).absolute()
    resolved, protected = target.resolve(), Path(history_directory).resolve()
    if resolved == protected or protected in resolved.parents:
        raise ValueError('请另选保存位置：历史目录由采集器管理，不能用导出覆盖。')
    return target


def write_export(path, payload, *, history_directory, overwrite=False):
    """Commit complete bytes atomically; cancellation/failed writes leave files intact."""
    target = export_path(path, history_directory)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.arkdps-share-',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck aliases after writing. Windows rename is atomic and refuses an
        # existing destination, including on filesystems without hard-link support.
        export_path(target, history_directory)
        if overwrite:
            os.replace(temporary, target)
        elif os.name == 'nt':
            os.rename(temporary, target)
        else:
            os.link(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
