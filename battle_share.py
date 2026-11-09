"""Read-only sharing of a validated BattleRecord, never a live sample."""
import json
import os
from pathlib import Path
import tempfile

from battle_history import BattleRecord
from panel_model import render_panel, rows_from_statistics


OUTCOMES = {'victory': '胜利', 'defeat': '失败', 'abandoned': '退出', 'unknown': '结果未知'}


def record_labels(record):
    data = record.to_dict()
    metadata = data['metadata']
    practice = {True: '演习', False: '正式', None: '演习状态未知'}[metadata['practice']]
    date = data['observed_at'] or data['recorded_at']
    date_label = '最后观察' if data['observed_at'] else '记录时间（观察时间未知）'
    return (metadata['stage_id'] or '关卡未知', practice, OUTCOMES[metadata['outcome']],
            f'{data["duration"]:.1f} 秒（最后可靠游戏时刻）', f'{date_label} {date}')


def record_view(record, config):
    data = record.to_dict()
    return render_panel(config, rows_from_statistics(data['statistics']), team_complete=record_complete(record))


def record_complete(record):
    # Same completeness rule as BattleRuntime.display_sample, applied to the frozen
    # result. last_sample is native input, not the live compatibility projection.
    stats = record.statistics
    return (not any(stats[key] for key in ('unknown_sources', 'unmapped_tokens',
                'ambiguous_sources', 'mapping_conflicts', 'missing_fields')) and
            all(row['counter_snapshot_complete'] for row in stats['rows'].values()))


def json_bytes(record):
    # Validate a fresh copy; do not export compact display values or compatibility rows.
    data = BattleRecord.from_dict(record.to_dict()).to_dict()
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
        # Recheck aliases after writing. No-overwrite uses an exclusive atomic link,
        # so a file created while the save dialog was open cannot be overwritten.
        export_path(target, history_directory)
        if overwrite:
            os.replace(temporary, target)
        else:
            os.link(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
