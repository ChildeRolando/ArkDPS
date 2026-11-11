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

from character_catalog import load_character_names
from roster import CharacterEntry, RosterSnapshot
from stats_core import Statistics
from event_stream import EventStream, StreamGap
from source_classification import classify_unmapped_source

DAMAGE_TYPES = ('physical', 'arts', 'true', 'elemental')


class AdapterError(RuntimeError):
    pass


class MappingUnavailable(AdapterError):
    """No reliable owner mapping is available; counters must not be published."""


class FrameMismatch(AdapterError):
    """The independently guarded snapshots must be sampled again."""


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
        previous = result.roster_entries.setdefault(entry.cid, entry)
        # Native counters keyed by prefab cannot separate a map unit and a
        # squad operator if the client gives them the same key.
        if previous.is_predefined != entry.is_predefined:
            result.ambiguous_cids.add(entry.cid)
        result.owners[entry.cid] = entry.cid
    for token in roster.entries:
        if not token.is_token:
            continue
        # A proven map-device namespace stays non-team even if the roster
        # exposes it as a token with no resolvable host (e.g. stage summons
        # such as trap_016_peon). It must not block event coverage.
        if classify_unmapped_source(token.cid) is not None:
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
    non_team_sources: tuple[str, ...] = ()
    unmapped_tokens: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    roster_frame: int | None = None


class BattleStatisticsAdapter:
    """One live battle at a time; callers create a new adapter per process."""

    def __init__(self):
        self.snapshot_revision = 0
        self.event_stream = None
        self._character_names = load_character_names()
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
        self.non_team_sources: dict[str, tuple[str, str]] = {}
        self.unattributed_output: dict[str, dict[str, float]] = {}
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
            if self.statistics.end_time is not None:
                return
            if self.event_stream is not None:
                self.event_stream.stop()
            self.statistics.invalidate_snapshots()
            self.statistics.invalidate_events()
            self.last_result = self.statistics.result()
            self.types_current.clear()

    def on_end(self) -> None:
        """Freeze a battle boundary that is not a collection gap.

        A vanished or FINISHED singleton confirms the end; it does not mean a
        read failed. Reliability rules differ by concern: close event coverage
        and stop certifying rate windows, but keep the last complete cumulative
        counters as final totals so settlement and history can share the team
        denominator. A real collection gap must use :meth:`on_gap` instead.
        """
        if self.statistics is None:
            return
        if self.event_stream is not None:
            self.event_stream.stop()
        self.statistics.seal_totals()
        self.statistics.invalidate_rates()
        self.types_current.clear()
        self.last_result = self.statistics.result()

    def _freeze(self) -> None:
        if self.statistics is not None and self.last_game_time is not None:
            if self.event_stream is not None:
                self.event_stream.stop()
            try:
                self.statistics.finish(self.last_game_time)
            except ValueError:
                pass
            self.last_result = self.statistics.result()

    def _begin(self, sample: dict, roster: RosterSnapshot) -> None:
        self.snapshot_revision = 0
        self.event_stream = None
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
        self.non_team_sources = {}
        self.unattributed_output = {}
        self.missing_fields = set()
        self.mapping_conflicts = set()
        self.last_roster = None
        self.presented_names = {}
        self.last_roster_frame = roster.frame
        self.session_switches += 1

    def start_events(self, *, session, frame, sequence):
        """Start only after the producer proves its barrier follows this baseline."""
        reasons = []
        if self.statistics is None:
            reasons.append('no statistics baseline')
        if self.event_stream is not None:
            reasons.append('event stream already open')
        if session != self.session:
            reasons.append(f'session {session!r} != {self.session!r}')
        if frame != self.last_frame:
            reasons.append(f'frame {frame} != {self.last_frame}')
        if self.last_game_time != self.coverage_start:
            reasons.append(f'time {self.last_game_time} != coverage start {self.coverage_start}')
        if self.owner_map.unknown_token_cids:
            reasons.append('unknown token sources: ' + ', '.join(sorted(self.owner_map.unknown_token_cids)))
        if self.owner_map.ambiguous_cids:
            reasons.append('ambiguous sources: ' + ', '.join(sorted(self.owner_map.ambiguous_cids)))
        if self.mapping_conflicts:
            reasons.append('mapping conflicts: ' + ', '.join(sorted(self.mapping_conflicts)))
        if reasons:
            raise ValueError('event barrier does not match the initial baseline: ' + '; '.join(reasons))
        self.statistics.event_coverage = True
        try:
            self.event_stream = EventStream(self.statistics, session=session,
                                            baseline_frame=frame,
                                            baseline_sequence=sequence)
        except ValueError:
            self.statistics.event_coverage = False
            raise
        self.last_result = self.statistics.result()

    def accept_event(self, **event):
        if self.event_stream is None:
            raise StreamGap('event stream is unavailable')
        source = event.get('source')
        kind = event.get('kind')
        if kind != 'elemental_burst' and (not isinstance(source, str) or not source):
            self.event_stream.stop()
            self.last_result = self.statistics.result()
            raise StreamGap('event source is invalid')
        blocked = (self.owner_map.unknown_token_cids | self.owner_map.ambiguous_cids
                   | self.mapping_conflicts)
        if source is not None and (source in blocked or self.owner_map.owners.get(source) in blocked):
            self.event_stream.stop()
            self.last_result = self.statistics.result()
            raise StreamGap('event source is ambiguous')
        owner = self.owner_map.owners.get(source) if source is not None else None
        if owner is not None and source not in self.statistics.owners:
            if not self._register(source, owner):
                self.event_stream.stop()
                self.last_result = self.statistics.result()
                raise StreamGap('event source cannot be registered')
        try:
            self.event_stream.accept(**event)
        finally:
            self.last_result = self.statistics.result()

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

    def update(self, sample: dict, roster: RosterSnapshot, *, terminal=False) -> AdapterUpdate | None:
        """Apply one cumulative snapshot; returns None for duplicate frames."""
        frame = sample.get('frame')
        if not isinstance(frame, int) or isinstance(frame, bool) or frame != roster.frame:
            raise FrameMismatch('累计快照与编队快照逻辑帧不一致；请重新读取')
        if terminal and (sample.get('state') != 3 or self.statistics is None
                         or sample['session'] != self.session or self.last_game_time is None):
            raise FrameMismatch('结算修订必须属于已有观察战局的原生结束状态')
        if terminal and (sample['game_time'] < self.last_game_time or
                         (self.statistics.end_time is not None and
                          sample['game_time'] != self.statistics.end_time)):
            raise FrameMismatch('结算修订不能改变冻结的游戏时间')
        if self.statistics is None or sample['session'] != self.session:
            if self.statistics is not None:
                self._freeze()
                self.statistics = None
            self._begin(sample, roster)
        game_time = sample['game_time']
        settling = self.statistics.end_time is not None
        if settling and (frame != self.last_frame or game_time != self.statistics.end_time
                         or sample.get('state') != 3):
            raise FrameMismatch('结算修订必须属于同局、同帧、同时间的结束快照')
        if not settling and self.last_game_time is not None and not terminal:
            if game_time < self.last_game_time:
                # A battle clock cannot move backwards in one session. Treat it
                # as a new observation session rather than mixing samples.
                self._freeze()
                self.statistics = None
                self._begin(sample, roster)
            elif game_time == self.last_game_time:
                return None
        if self.last_frame is not None and frame == self.last_frame and not terminal and not settling:
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
        self.mapping_conflicts.update(
            entry.cid for entry in roster.entries
            if entry.is_predefined and not entry.is_token
            and entry.cid in roster_map.ambiguous_cids)
        self.owner_map.roster_entries.update(roster_map.roster_entries)
        self.last_roster_frame = roster.frame

        blocked = (roster_map.unknown_token_cids | roster_map.ambiguous_cids
                   | self.mapping_conflicts)
        if self.event_stream is not None and blocked:
            self.event_stream.stop()
        counters: dict[str, dict[str, float]] = {}
        types_update: dict[str, dict[str, float]] = {}
        roster_keys = {entry.cid for entry in roster.entries}
        for row in sample.get('rows', ()):
            cid = row.get('id')
            if not isinstance(cid, str) or not cid:
                continue
            owner = self.owner_map.owners.get(cid)
            if owner is None or cid in blocked or owner in blocked:
                counter = _counter(row)
                if counter is not None:
                    self.unattributed_output[cid] = counter
                # Ambiguous or conflicting keys may still hide team output;
                # only a key with no owner evidence at all can be classified.
                classified = (classify_unmapped_source(cid, roster_keys)
                              if owner is None and cid not in blocked else None)
                if classified is None:
                    self.unknown_sources.add(cid)
                    self.non_team_sources.pop(cid, None)
                else:
                    self.non_team_sources[cid] = classified
                    self.unknown_sources.discard(cid)
                continue
            counter = _counter(row)
            if counter is None:
                self.missing_fields.add(cid)
                continue
            if not self._register(cid, owner):
                continue
            self.non_team_sources.pop(cid, None)
            counters[cid] = counter
            name = row.get('name')
            if cid == owner and name and name != owner:
                self.presented_names[owner] = name
            damage_types = _types(row)
            if damage_types is not None:
                types_update[cid] = damage_types
        if not counters:
            # Fail closed: rows exist but none can be attributed. This is a
            # mapping/layout problem, not a legitimate empty battle.
            if sample.get('rows'):
                raise MappingUnavailable('统计条目存在但无法可靠归属；拒绝发布')
        if terminal:
            if self.statistics.end_time is None:
                if game_time > self.last_game_time:
                    self.statistics.snapshot(game_time, counters)
                self.finish(game_time)
            changed = self.statistics.correct_final_totals(counters)
        elif settling:
            changed = self.statistics.settle_snapshot(counters)
        else:
            self.statistics.snapshot(game_time, counters)
            changed = True
        if (changed or roster != self.last_roster
                or any(self.last_types.get(cid) != values for cid, values in types_update.items())):
            self.snapshot_revision += 1
        self.last_types.update(types_update)
        self.types_current = set(types_update)
        self.last_roster = roster
        self.last_frame = frame
        self.last_game_time = game_time
        self.last_result = self.statistics.result()
        return AdapterUpdate(
            session=self.session, game_time=game_time, result=self.last_result,
            unknown_sources=tuple(sorted(self.unknown_sources)),
            non_team_sources=tuple(sorted(self.non_team_sources)),
            unmapped_tokens=tuple(sorted(self.owner_map.unknown_token_cids)),
            missing_fields=tuple(sorted(self.missing_fields)),
            roster_frame=roster.frame)

    def finish(self, game_time: float | None = None) -> dict:
        if self.statistics is None or self.last_game_time is None:
            return self.last_result or {'rows': {}}
        if self.statistics.end_time is not None:
            self.statistics.seal_totals()
            self.last_result = self.statistics.result()
            return self.last_result
        end = game_time if game_time is not None else self.last_game_time
        if self.event_stream is not None:
            if (not self.event_stream.closed and self.event_stream.game_time == end
                    and self.statistics.now == end):
                self.event_stream.closed = True
            else:
                self.event_stream.stop()
        self.statistics.finish(end)
        # A frozen battle has no future observation: last reliable cumulative
        # counters are final. A gap-invalidated source stays unknown.
        self.statistics.seal_totals()
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
                    if entry.cid == owner and not entry.is_token)
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
            burst = enriched.get('burst_settlement')
            if burst is not None:
                types = dict(enriched['damage_by_type'])
                types['elemental'] = types.get('elemental', 0.0) + burst
                enriched['damage_by_type'] = types
            enriched['types_available'] = bool(known_types) or burst is not None
            enriched['types_stale'] = ((not sources or any(
                cid not in self.types_current for cid in sources))
                and burst is None)
            enriched['present'] = owners_present.get(owner) if present_known else None
            enriched['sources'] = sorted(
                cid for cid, mapped in self.owner_map.owners.items() if mapped == owner)
            enriched['name'] = (self.presented_names.get(owner)
                                or self._character_names.get(owner)
                                or owner)
            rows[owner] = enriched
        result['rows'] = rows
        result['present_known'] = present_known
        result['unknown_present_unique_ids'] = sorted(unknown_present)
        result['unknown_sources'] = sorted(self.unknown_sources)
        result['non_team_sources'] = sorted(self.non_team_sources)
        result['non_team_output'] = {
            cid: dict(values) for cid, values in sorted(self.unattributed_output.items())
            if cid in self.non_team_sources}
        result['unmapped_tokens'] = sorted(self.owner_map.unknown_token_cids)
        result['ambiguous_sources'] = sorted(self.owner_map.ambiguous_cids)
        result['mapping_conflicts'] = sorted(self.mapping_conflicts)
        result['missing_fields'] = sorted(self.missing_fields)
        # Team totals require every source to be attributed: an unattributable
        # (or ambiguous) source may hide team output, so it overrides the core
        # per-source completeness flag. Classified non-team sources do not.
        result['totals_complete'] = bool(result.get('totals_complete')) and not (
            self.unknown_sources or self.owner_map.unknown_token_cids
            or self.owner_map.ambiguous_cids or self.mapping_conflicts
            or self.missing_fields)
        result['adapter_session'] = self.session
        result['snapshot_revision'] = self.snapshot_revision
        return result
