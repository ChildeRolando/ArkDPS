"""Read-only sharing of a validated BattleRecord, never a live sample."""
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile

from battle_history import BattleRecord
from panel_model import (DIAGNOSTIC_LABELS, EventAvailability, panel_statistics, render_panel,
                         rows_from_statistics, team_status)
from source_classification import classify_unmapped_source
import stage_names


OUTCOMES = {'victory': '胜利', 'defeat': '失败', 'abandoned': '退出', 'unknown': '结果未知'}


def record_labels(record):
    return record_display(record)['labels']


def record_display(record):
    """Metadata for the history browser and the share header."""
    data = record.to_dict()
    metadata = data['metadata']
    stage = stage_names.resolve(metadata['stage_id'])
    practice = {True: '演习', False: '正式', None: '演习状态未知'}[metadata['practice']]
    outcome = OUTCOMES[metadata['outcome']]
    duration = f'{data["duration"]:.1f} 秒'
    date = data['observed_at'] or data['recorded_at']
    date_label = '最后观察' if data['observed_at'] else '记录时间（观察时间未知）'
    observed_date = datetime.fromisoformat(date)
    try:
        observed_date = observed_date.astimezone()
    except (ValueError, OverflowError, OSError):
        # Older valid archives can predate the operating system's time zone table.
        pass
    return dict(battle_id=record.battle_id, stage=stage.label, stage_description=stage.description,
                stage_detail=stage.detail, stage_raw=stage.raw, outcome=outcome, duration=duration,
                date=f'{observed_date:%Y-%m-%d %H:%M}', labels=(stage.label, practice, outcome,
                    f'{duration}（最后可靠游戏时刻）', f'{date_label} {date}'))


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


def record_statistics(record):
    """Return frozen statistics with totals completeness resolved for display.

    New records carry ``totals_complete`` and the source classification from
    the statistics core. Legacy records are normalized from the evidence
    above, including reclassifying recognised non-team keys; missing totals
    stay incomplete instead of being zero-filled.
    """
    stats = record.statistics
    sample = record.to_dict().get('last_sample') or {}
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
    return team_status(record_statistics(record))


def record_view(record, config):
    stats = panel_statistics(config, record_statistics(record), history=True)
    complete, reason, note = team_status(stats)
    if config.boss_scope:
        reason = stats['boss_damage_reason'] or reason
        complete = complete and stats['boss_damage_available']
        note = ''
    return render_panel(config, rows_from_statistics(stats), team_complete=complete,
                        team_reason=reason, team_note=note,
                        event=EventAvailability(record.event_status, record.event_error, live=False))


def record_complete(record):
    # Same completeness rule as BattleRuntime.display_sample, applied to the frozen
    # result. last_sample is native input, not the live compatibility projection.
    return record_status(record)[0]


def json_bytes(record):
    # Validate a fresh copy; never replace raw values with compact display values
    # or compatibility rows.
    data = BattleRecord.from_dict(record.to_dict()).to_dict()
    # The raw stage_id stays authoritative; the display block only adds the
    # resolved name so exported JSON is readable without the catalog.
    data['display'] = {'stage': stage_names.display_fields(data['metadata']['stage_id'])}
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
