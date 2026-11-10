"""Player-readable stage names for every display surface.

The raw ``stage_id`` is the native ``LevelData.levelId`` (for example
``Obt/Main/level_main_01-07``). History and exports keep it verbatim; only the
display layer resolves it through the bundled, versioned ``stage_names.json``.
A missing or version-mismatched catalog is reported explicitly and never
rewrites or drops a record.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

CATALOG_SCHEMA_VERSION = 1
UNKNOWN_LABEL = '关卡未知'
UNKNOWN_STAGE_WARNING = '关卡名数据不可用，无法翻译关卡名称'
DATA_DIR = Path(__file__).resolve().parent / 'vendor/ark-source/game_data'
CATALOG_PATH = DATA_DIR / 'catalogs/stage_names.json'
MANIFEST_PATH = DATA_DIR / 'manifest.json'
_SUFFIX = re.compile(r'[/\\]')
_LEVEL_PREFIX = 'level_'


@dataclass(frozen=True)
class StageDisplay:
    """Resolved label plus the untouched native identifier for traceability."""

    raw: str | None
    label: str
    code: str = ''
    name: str = ''
    zone: str = ''
    stage_id: str = ''
    stage_type: str = ''
    known: bool = False
    warning: str = ''

    @property
    def detail(self) -> str:
        if self.raw is None:
            return '原始关卡 ID：— · 未知关卡'
        fields = [f'原始关卡 ID：{self.raw}']
        if self.known:
            fields.append(f'代号 {self.code or "—"} · 名称 {self.name or "—"} · 区域 {self.zone or "—"}')
        else:
            fields.append('未收录于关卡表 · 显示兜底短码')
        if self.warning:
            fields.append(self.warning)
        return ' · '.join(fields)


class StageCatalog:
    """Validated catalog lookup; failures degrade to a readable short code."""

    def __init__(self, catalog_path=None, manifest_path=None):
        self.path = Path(catalog_path or CATALOG_PATH)
        self.manifest_path = Path(manifest_path or MANIFEST_PATH)
        self.stages: dict[str, tuple] = {}
        self.stage_ids: dict[str, tuple] = {}
        self.data_version = ''
        self.warning = ''
        self._load()

    def _load(self):
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(payload, dict) or payload.get('schema_version') != CATALOG_SCHEMA_VERSION:
                raise ValueError('unsupported stage catalog schema')
            stages = payload['stages']
            if not isinstance(stages, dict):
                raise ValueError('invalid stage catalog entries')
            entries = {}
            for level_id, fields in stages.items():
                if not (isinstance(level_id, str) and level_id and isinstance(fields, list)
                        and len(fields) == 5 and all(isinstance(f, str) for f in fields)):
                    raise ValueError(f'invalid stage catalog entry: {level_id}')
                entries[level_id] = tuple(fields)
            aliases = payload.get('aliases') or {}
            if not (isinstance(aliases, dict) and all(
                    isinstance(key, str) and key and isinstance(value, str) and value
                    for key, value in aliases.items())):
                raise ValueError('invalid stage catalog aliases')
            data_version = payload.get('bundle_data_version')
            if data_version is not None and not isinstance(data_version, str):
                raise ValueError('invalid stage catalog bundle_data_version')
        except FileNotFoundError:
            self.warning = f'{UNKNOWN_STAGE_WARNING}：未找到 {self.path.name}，仅显示兜底短码。'
            return
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.warning = f'{UNKNOWN_STAGE_WARNING}：{self.path.name} 无法解析（{exc}），仅显示兜底短码。'
            return
        self.stages, self.data_version = entries, data_version or ''
        for entry in entries.values():
            stage_id = entry[0]
            if stage_id:
                self.stage_ids.setdefault(stage_id, entry)
        for alias, level_id in aliases.items():
            entry = entries.get(level_id)
            if entry is not None:
                self.stage_ids.setdefault(alias, entry)
        self.warning = self._version_warning()

    def _version_warning(self):
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding='utf-8'))
            expected = manifest.get('data_version')
        except FileNotFoundError:
            return '关卡表版本未知：缺少游戏数据 manifest，无法校验关卡名目录。'
        except (OSError, ValueError, TypeError):
            return '关卡表版本未知：游戏数据 manifest 无法解析，无法校验关卡名目录。'
        if not isinstance(expected, str) or not expected:
            return '关卡表版本未知：manifest 未记录 data_version，无法校验关卡名目录。'
        if not self.data_version:
            return '关卡表版本未知：关卡名目录未记录 bundle_data_version。'
        if self.data_version != expected:
            return (f'关卡表版本不匹配：关卡名目录依据 {self.data_version}，'
                    f'游戏数据为 {expected}；请重新生成关卡名目录。')
        return ''

    @staticmethod
    def short_code(raw: str) -> str:
        """Readable fallback: last path segment without the internal prefix."""
        parts = [part for part in _SUFFIX.split(raw.strip()) if part]
        value = parts[-1] if parts else raw.strip()
        if value.startswith(_LEVEL_PREFIX):
            value = value[len(_LEVEL_PREFIX):]
        return value or raw.strip()

    def resolve(self, stage_id) -> StageDisplay:
        raw = stage_id.strip() if isinstance(stage_id, str) else ''
        if not raw:
            return StageDisplay(None, UNKNOWN_LABEL, warning=self.warning)
        entry = self.stages.get(raw) or self.stage_ids.get(raw)
        if entry is None:
            return StageDisplay(raw=raw, label=self.short_code(raw), warning=self.warning)
        stage_key, stage_type, code, name, zone = entry
        if stage_type == 'MAIN':
            label = code or name
        elif stage_type == 'ROGUELIKE':
            label = name or code
        else:
            label = ' '.join(part for part in (code, name) if part)
        return StageDisplay(raw=raw, label=label or self.short_code(raw), code=code, name=name,
                            zone=zone, stage_id=stage_key, stage_type=stage_type,
                            known=True, warning=self.warning)


_CATALOG: StageCatalog | None = None


def catalog() -> StageCatalog:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = StageCatalog()
    return _CATALOG


def resolve(stage_id) -> StageDisplay:
    return catalog().resolve(stage_id)


def display_fields(stage_id) -> dict:
    """Stable display block for exports; the raw stage_id stays untouched."""
    display = resolve(stage_id)
    return dict(stage_id=display.raw, stage_name=display.label, stage_code=display.code,
                stage_zone=display.zone, stage_known=display.known,
                catalog_version=catalog().data_version or None)
