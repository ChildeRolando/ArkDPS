"""Adapt frame-guarded native snapshots to the offline statistics core.

The adapter owns one ``Statistics`` instance per battle session. It consumes
cumulative character counters plus a roster snapshot and produces rows that
carry four damage types, healing, stable source keys, live presence and
verified summon ownership. It never turns a missing field into zero and never
guesses an owner: sources without a reliable mapping are excluded and listed
in diagnostics.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from roster import CharacterEntry, RosterSnapshot
from stats_core import Statistics

DAMAGE_TYPES = ('physical', 'arts', 'true', 'elemental')


class AdapterError(RuntimeError):
    pass


class MappingUnavailable(AdapterError):
    """No reliable owner mapping is available; counters must not be published."""


@dataclass
class OwnerMap:
    owners: dict[str, str] = field(default_factory=dict)
    unknown_token_cids: set[str] = field(default_factory=set)
    ambiguous_cids: set[str] = field(default_factory=set)
    roster_entries: dict[str, CharacterEntry] = field(default_factory=dict)

    def lookup(self, cid: str) -> str | None:
        return self.owners.get(cid)


def build_owner_map(roster: RosterSnapshot) -> OwnerMap:
    """Resolve statistic keys to owners using only the roster's own evidence.

    A token is attributed only when its ``tokenOrHostKey`` and
    ``tokenOrHostUniqueId`` both identify exactly one roster host. Duplicate
    roster rows for one key are collapsed; conflicting ownership is ambiguous.
    """
    non_tokens = [e for e in roster.entries if not e.is_token]
    by_uid: dict[int, list[CharacterEntry]] = {}
    for entry in non_tokens:
        by_uid.setdefault(entry.unique_id, []).append(entry)
    result = OwnerMap()
    for entry in non_tokens:
        previous = result.owners.get(entry.cid)
        if previous is not None and previous != entry.cid:
            result.ambiguous_cids.add(entry.cid)
        result.owners[entry.cid] = entry.cid
        result.roster_entries.setdefault(entry.cid, entry)
    for token in roster.entries:
        if not token.is_token:
            continue
        hosts = by_uid.get(token.host_uid, []) if token.host_uid is not None else []
        hosts = [h for h in hosts if h.cid == token.host_key]
        host_by_key = {(h.cid, h.unique_id): h for h in hosts}
        hosts = list(host_by_key.values())
        cids = {h.cid for h in hosts}
        if len(hosts) != 1 or len(cids) != 1 or not token.host_key:
            result.unknown_token_cids.add(token.cid)
            continue
        owner = hosts[0].cid
        previous = result.owners.get(token.cid)
        if previous is not None and previous != owner:
            result.ambiguous_cids.add(token.cid)
            result.owners.pop(token.cid, None)
            continue
        if token.cid in result.ambiguous_cids:
            continue
        result.owners[token.cid] = owner
        result.roster_entries.setdefault(token.cid, token)
    for cid in result.ambiguous_cids | result.unknown_token_cids:
        result.owners.pop(cid, None)
    return result


def _counter(row: dict) -> dict[str, float] | None:
    values = {}
    for kind in ('damage', 'healing'):
        value = row.get(kind)
        if (not isinstance(value, (int, float)) or not math.isfinite(value)
                or value < 0):
            return None
        values[kind] = value
    return values


def _types(row: dict) -> dict[str, float] | None:
    out = {}
    for kind in DAMAGE_TYPES:
        value = row.get(kind)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            return None
        out[kind] = value
    return out


@dataclass
class AdapterUpdate:
    session: str
    game_time: float
    result: dict
    unknown_sources: tuple[str, ...] = ()
    unmapped_tokens: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    roster_frame: int | None = None


class BattleStatisticsAdapter:
    """One live battle at a time; callers create a new adapter per process."""

    def __init__(self):
        self.statistics: Statistics | None = None
        self.session: str | None = None
        self.owner_map = OwnerMap()
        self.coverage_start: float | None = None
        self.last_frame: int | None = None
        self.last_game_time: float | None = None
        self.last_result: dict | None = None
        self.last_types: dict[str, dict[str, float]] = {}
        self.types_current: set[str] = set()
        self.unknown_sources: set[str] = set()
        self.missing_fields: set[str] = set()
        self.session_switches = 0
        self.same_time_changes = 0
        self.mapping_conflicts: set[str] = set()
        self.last_roster_frame: int | None = None
        self.last_roster: RosterSnapshot | None = None
        self.presented_names: dict[str, str] = {}
        self.finished_session: str | None = None

    @property
    def active(self) -> bool:
        return self.statistics is not None

    def on_gap(self) -> None:
        """Report a collection gap without inventing zeros or rates."""
        if self.statistics is not None:
            self.statistics.invalidate_snapshots()
            self.statistics.invalidate_events()
            self.last_result = self.statistics.result()
            self.types_current.clear()

    def _freeze(self) -> None:
        if self.statistics is not None and self.last_game_time is not None:
            try:
                self.statistics.finish(self.last_game_time)
            except ValueError:
                pass
            self.last_result = self.statistics.result()

    def _begin(self, sample: dict, roster: RosterSnapshot) -> None:
        session = sample['session']
        start = sample['game_time']
        mapping = build_owner_map(roster)
        if not mapping.owners:
            raise MappingUnavailable(
                '未能从战局编队建立可靠的统计键归属；拒绝发布累计数据')
        self.owner_map = mapping
        self.statistics = Statistics(session, start)
        self.session = session
        self.coverage_start = start
        self.last_frame = None
        self.last_game_time = None
        self.last_result = None
        self.last_types = {}
        self.types_current = set()
        self.unknown_sources = set(mapping.unknown_token_cids)
        self.missing_fields = set()
        self.mapping_conflicts = set()
        self.last_roster = None
        self.presented_names = {}
        self.last_roster_frame = roster.frame
        self.session_switches += 1

    def _register(self, cid: str, owner: str) -> bool:
        if cid in self.owner_map.owners and self.owner_map.owners[cid] != owner:
            self.mapping_conflicts.add(cid)
            return False
        try:
            self.statistics.register_source(cid, owner)
        except ValueError:
            self.mapping_conflicts.add(cid)
            return False
        self.owner_map.owners[cid] = owner
        return True

    def update(self, sample: dict, roster: RosterSnapshot) -> AdapterUpdate | None:
        """Apply one cumulative snapshot; returns None for duplicate frames."""
        if self.statistics is None or sample['session'] != self.session:
            if self.statistics is not None:
                self._freeze()
                self.statistics = None
            self._begin(sample, roster)
        game_time = sample['game_time']
        frame = sample.get('frame')
        if self.last_frame is not None and frame == self.last_frame:
            return None
        if self.last_game_time is not None:
            if game_time < self.last_game_time:
                # A battle clock cannot move backwards in one session. Treat it
                # as a new observation session rather than mixing samples.
                self._freeze()
                self.statistics = None
                self._begin(sample, roster)
            elif game_time == self.last_game_time:
                return None
        roster_map = build_owner_map(roster)
        # New roster evidence can map tokens that were not attributable when
        # the session started. Never change an existing mapping in-place.
        for cid, owner in roster_map.owners.items():
            if cid in self.owner_map.owners and self.owner_map.owners[cid] != owner:
                self.mapping_conflicts.add(cid)
                continue
            self.owner_map.owners.setdefault(cid, owner)
        self.owner_map.unknown_token_cids |= roster_map.unknown_token_cids
        self.owner_map.ambiguous_cids |= roster_map.ambiguous_cids
        self.owner_map.roster_entries.update(roster_map.roster_entries)
        self.last_roster_frame = roster.frame

        blocked = (roster_map.unknown_token_cids | roster_map.ambiguous_cids
                   | self.mapping_conflicts)
        counters: dict[str, dict[str, float]] = {}
        types_update: dict[str, dict[str, float]] = {}
        names: dict[str, str] = {}
        for row in sample.get('rows', ()):
            cid = row.get('id')
            if not isinstance(cid, str) or not cid:
                continue
            owner = self.owner_map.owners.get(cid)
            if owner is None or cid in blocked:
                self.unknown_sources.add(cid)
                continue
            counter = _counter(row)
            if counter is None:
                self.missing_fields.add(cid)
                continue
            if not self._register(cid, owner):
                continue
            counters[cid] = counter
            names[owner] = row.get('name') or names.get(owner, owner)
            damage_types = _types(row)
            if damage_types is not None:
                types_update[cid] = damage_types
        self.presented_names.update(names)
        if not counters:
            # Fail closed: rows exist but none can be attributed. This is a
            # mapping/layout problem, not a legitimate empty battle.
            if sample.get('rows'):
                raise MappingUnavailable('统计条目存在但无法可靠归属；拒绝发布')
            self.statistics.snapshot(game_time, {})
        else:
            self.statistics.snapshot(game_time, counters)
        self.last_types.update(types_update)
        self.types_current = set(types_update)
        self.last_roster = roster
        self.last_frame = frame
        self.last_game_time = game_time
        self.last_result = self.statistics.result()
        return AdapterUpdate(
            session=self.session, game_time=game_time, result=self.last_result,
            unknown_sources=tuple(sorted(self.unknown_sources)),
            unmapped_tokens=tuple(sorted(self.owner_map.unknown_token_cids)),
            missing_fields=tuple(sorted(self.missing_fields)),
            roster_frame=roster.frame)

    def finish(self, game_time: float | None = None) -> dict:
        if self.statistics is None or self.last_game_time is None:
            return self.last_result or {'rows': {}}
        self.statistics.finish(game_time if game_time is not None else self.last_game_time)
        self.last_result = self.statistics.result()
        self.finished_session = self.session
        return self.last_result

    def result(self, roster: RosterSnapshot | None = None) -> dict:
        """Return statistics rows enriched with type totals and presence."""
        if self.last_result is None:
            return {'rows': {}}
        result = dict(self.last_result)
        roster = roster if roster is not None else self.last_roster
        present_ids = roster.present_unique_ids if roster else frozenset()
        present_known = roster.present_known if roster else False
        unknown_present: set[int] = set()
        owners_present: dict[str, bool] = {}
        if present_known:
            known_uids = {e.unique_id for e in roster.entries}
            unknown_present = set(present_ids) - known_uids
            for owner in result['rows']:
                owners_present[owner] = any(
                    entry.unique_id in present_ids
                    for entry in roster.entries
                    if self.owner_map.owners.get(entry.cid) == owner)
        rows = {}
        for owner, row in result['rows'].items():
            enriched = dict(row)
            sources = [cid for cid, mapped in self.statistics.owners.items()
                       if mapped == owner]
            known_types = [self.last_types[cid] for cid in sources
                           if cid in self.last_types]
            enriched['damage_by_type'] = (
                {kind: sum(values[kind] for values in known_types)
                 for kind in DAMAGE_TYPES} if known_types else {})
            enriched['types_available'] = bool(known_types)
            enriched['types_stale'] = not sources or any(
                cid not in self.types_current for cid in sources)
            enriched['present'] = owners_present.get(owner) if present_known else None
            enriched['sources'] = sorted(
                cid for cid, mapped in self.owner_map.owners.items() if mapped == owner)
            enriched['name'] = self.presented_names.get(owner, owner)
            rows[owner] = enriched
        result['rows'] = rows
        result['present_known'] = present_known
        result['unknown_present_unique_ids'] = sorted(unknown_present)
        result['unknown_sources'] = sorted(self.unknown_sources)
        result['unmapped_tokens'] = sorted(self.owner_map.unknown_token_cids)
        result['ambiguous_sources'] = sorted(self.owner_map.ambiguous_cids)
        result['mapping_conflicts'] = sorted(self.mapping_conflicts)
        result['missing_fields'] = sorted(self.missing_fields)
        result['adapter_session'] = self.session
        return result
