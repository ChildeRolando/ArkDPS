"""Read-only sharing of a validated BattleRecord, never a live sample."""
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile

from battle_history import BattleRecord, AUTOCHESS_SNAPSHOT_LABEL, AUTOCHESS_SNAPSHOT_NOTE
from panel_model import team_status
import stage_names


OUTCOMES = {'victory': '胜利', 'defeat': '失败', 'abandoned': '退出', 'unknown': '结果未知'}


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
    segment = metadata.get('capture_segment')
    capture_segment = (isinstance(segment, dict) and segment.get('complete') is False
                       and segment.get('reason') in ('event_capture_paused', 'event_capture_resumed'))
    if capture_segment:
        outcome = '采样片段'
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
    if capture_segment:
        display.update(capture_segment_label=('采样片段 · 事件暂停' if segment['reason'] == 'event_capture_paused'
                                              else '采样片段 · 事件恢复'),
                       capture_warning='手动暂停事件采集；逐次统计仅覆盖本片段',
                       labels=(*display['labels'], '手动暂停事件采集；逐次统计仅覆盖本片段'))
    return display


def record_stage(record):
    """Readable stage label plus the untouched native stage_id."""
    return stage_names.resolve(record.metadata['stage_id'])


def record_statistics(record, *, for_panel=False):
    """Compatibility call site; archive interpretation belongs to BattleRecord."""
    return record.normalized_statistics(for_panel=for_panel)


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
    capture = record_display(record)
    if 'capture_segment_label' in capture:
        data['display'].update({key: capture[key] for key in ('capture_segment_label', 'capture_warning')})
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
