
"""A late first native-PC sample must replay a producer-witnessed round origin."""
from copy import deepcopy
from dataclasses import replace

import pytest

from connection import ConnectionConfig
from desktop_bridge import DesktopState
from event_capture import CaptureRetry
from event_probe import CallbackBusyMark, TerminalReadRetry
from panel_model import PanelConfig
from test_desktop_native_defense_retention import view_values
from test_multiplayer_native_context import QUALITY, stamp
from test_multiplayer_native_skill_baseline import activation
from test_native_autochess_leading_zero_uid import leading_zero_battle
from test_native_pc_autochess_collector_gate import ROOTS


class OwnedRoundStart:
    """Control native input and the queue while using every production host layer."""

    def __init__(self, tmp_path):
        self.battle = leading_zero_battle(tmp_path / 'raw')
        self.baseline_reads = []
        self.round_start = None
        self.prefix_fault = None
        self.busy = False
        self.terminal_capable = False
        self.native_generation = 1
        self.peer_present = True
        self.original_mark = self.battle.observer.mark
        self.original_batch = self.battle.observer.read_through
        self.original_sample = self.battle.probe.sample
        self.battle.observer.mark = self.mark
        self.battle.observer.read_through = self.read_through
        self.battle.observer.read_baseline = self.read_baseline
        self.battle.probe.sample = self.guarded_sample

        self.state = DesktopState(tmp_path / 'desktop')
        self.state.layout = replace(self.state.layout, multiplayer_enabled=True,
                                    panels=(PanelConfig(),))
        self.state.effective_connection = ConnectionConfig(
            platform='native_pc', native_game_root='owned-offline-PC')

    @property
    def ack(self):
        return self.battle.commits[-1] if self.battle.commits else 0

    def guarded_sample(self, **kwargs):
        report = self.original_sample(**kwargs)
        if 'sample' in report:
            sample = report['sample']
            probe = self.battle.probe
            stamp = dict(native_identity=deepcopy(ROOTS), frame=probe.frame,
                         native_fixed_time=str(int(probe.when * (1 << 32))),
                         stage_id=probe.stage)
            sample['native_window_guard'] = dict(
                version=1, source='native_pc_guarded_read',
                process_instance_before=list(probe.process_instance),
                process_instance_after=list(probe.process_instance),
                before=stamp, after=deepcopy(stamp),
                source_build=probe.build['source_build'], battle_serial=sample['battle_serial'])
        return report

    def decorate(self, packet):
        probe = self.battle.probe
        packet.update(native_generation=self.native_generation, native_process_pid=probe.pid,
                      native_stage_id=probe.stage, active_hooks=[],
                      damage_settlement_scope=self.native_generation,
                      skill_ready=True, source_attribution_version=1,
                      source_character_uid_ready=True)
        context = packet['native_autochess_context']
        context['collection_mode'] = 'all'
        if not self.peer_present:
            context['memberships'] = [member for member in context['memberships']
                                      if member['player_index'] != 2]
        if self.terminal_capable:
            packet['terminal_read_version'] = 1
        return packet

    def mark(self):
        if self.busy:
            raise CallbackBusyMark('owned frame-zero producers still in callbacks')
        return self.decorate(self.original_mark())

    def read_through(self, through):
        packet = self.decorate(self.original_batch(through))
        packet['events'] = [event for event in packet['events'] if self.ack < event['sequence']]
        return packet

    def record_origin(self):
        """Observe fixture state at producer tick zero, independently of poll."""
        packet = self.decorate(self.original_mark())
        assert packet['frame'] == packet['game_time'] == 0
        self.round_start = dict(
            version=1, source_method='BattleController.ClearRunTimeData',
            live_character_collection_empty=True,
            quality=dict(
                **{key: value for key, value in QUALITY.items() if not key.startswith('baseline_')},
                quality_contract_version=1, source_attribution_version=1,
                source_character_uid_ready=True, skill_ready=True,
                damage_settlement_quality_version=1,
                damage_settlement_scope=self.native_generation,
                unknown_sources=[], engagement_unknown_sources=[]),
            **{key: deepcopy(packet[key]) for key in (
                'frame', 'game_time', 'native_fixed_time', 'sequence', 'epoch', 'active',
                'native_generation', 'native_process_pid', 'native_stage_id',
                'native_identity', 'native_autochess_context')})

    def read_baseline(self, through):
        packet = self.read_through(through)
        records = packet['events']
        scope = packet['native_autochess_context']['scope_key']
        self.baseline_reads.append((self.ack, through, scope))
        starts = [{k: deepcopy(e[k]) for k in (
            'sequence', 'frame', 'game_time', 'source', 'source_character_uid',
            'proof', 'native_autochess_scope')} for e in records
            if e['kind'] == 'skill_start' and e['native_autochess_scope']['scope_key'] == scope]
        packet.update(events=[], baseline_discarded_skill_events=starts,
                      baseline_discarded_skill_events_complete=True)
        origin = self.round_start
        if origin is not None and self.prefix_fault != 'absent' and origin['native_autochess_context']['scope_key'] == scope:
            prefix = [deepcopy(e) for e in records if origin['sequence'] < e['sequence']]
            complete = [event['sequence'] for event in prefix] == list(range(origin['sequence'] + 1, through + 1))
            if self.prefix_fault == 'foreign_scope':
                prefix[0]['native_autochess_scope']['scope_key'] = self.old_scope_key
            if self.prefix_fault == 'missing_record':
                prefix.pop(0)
            origin = deepcopy(origin)
            if self.prefix_fault == 'foreign_generation':
                origin['native_generation'] -= 1
            if self.prefix_fault == 'live_actors':
                origin['live_character_collection_empty'] = False
            if self.prefix_fault == 'guarded_live_actors':
                origin.update(version=2, live_character_collection_empty=False,
                              live_character_data_uids=[101])
            packet.update(autochess_round_start=origin,
                          autochess_round_start_events=prefix,
                          autochess_round_start_events_complete=complete)
        return packet

    def hit(self, sequence, actor, amount):
        event = self.battle.hit(sequence, actor, amount)
        event['native_autochess_scope']['collection_mode'] = 'all'
        return event

    def poll(self):
        observation = self.battle.poll()
        self.state.accept('autochess_scene', self.battle.runtime.feed.autochess_scene)
        self.state.accept('lifecycle', self.battle.runtime.sessions.state)
        if observation.sample is not None and observation.kind in ('sample', 'terminal'):
            self.state.accept('sample', self.battle.runtime.display_sample())
        return observation

    def local(self):
        return self.battle.runtime.display_sample()['multiplayer']['players'][2]


def enter_late(tmp_path, *, phase=4, old_tail=None, prefix_fault=None,
               late_peer=False, early_peer_skill=None):
    source = OwnedRoundStart(tmp_path)
    battle = source.battle
    source.poll()
    battle.advance(1)
    battle.advance(1, [source.hit(i, i, i * 100) for i in range(1, 5)])
    source.poll()
    old = battle.runtime.display_sample()
    source.old_scope_key = battle.scope()
    assert source.ack == 4

    if old_tail is not None:
        source.terminal_capable = True
        battle.advance(2, state=3)
        assert source.poll().kind == 'terminal'
        assert battle.runtime.sessions.capture.terminal_pending
        calls = []

        def still_pending(after, *, deadline):
            calls.append((after, deadline))
            raise TerminalReadRetry('owned old terminal fence unavailable')

        battle.observer.read_terminal_tail = still_pending
        battle.active = False
        assert source.poll().reason == 'terminal_tail_pending'
        assert [after for after, _ in calls] == [4]
        assert calls[0][1] - battle.runtime.feed._terminal_retry_started == 2
        if old_tail == 'expired':
            # Run the deterministic 2s timeout path without wall-clock sleep.
            battle.runtime.feed._terminal_retry_started -= 2.001
            assert source.poll().events['terminal_diagnostic']['reason'] == 'terminal_tail_retry_budget_expired'
            assert source.ack == 4
        battle.active = True

    if old_tail != 'pending':
        battle.advance(2.125, phase=3, state=2, epoch=2, native_round=5)
        assert source.poll().kind == 'preparing'
        preparing = source.state.snapshot()
        if old_tail == 'expired':
            # Verified singleton absence retired the cached prior match view.
            assert view_values(preparing) == []
        else:
            assert view_values(preparing) == [300]
            assert preparing['multiplayer']['previous_defense'] is True
            assert preparing['multiplayer']['scope'] == old['multiplayer']['scope']

        battle.advance(2.25, phase=phase, epoch=3)
        source.poll()  # Phase label changes before real combat clock rolls back.
        assert view_values(source.state.snapshot()) == []
        assert source.ack == 4
    # An older clock record with the same local actor and native round remains
    # queued before the true producer origin. It is not part of the new total.
    battle.advance(2.5)
    battle.advance(2.5, [source.hit(5, 3, 2000)])

    source.native_generation = 2
    source.peer_present = not late_peer
    battle.advance(0, phase=phase, state=2, epoch=4, native_round=5)
    source.record_origin()
    assert source.round_start['sequence'] == 5
    source.busy = True
    with pytest.raises(CaptureRetry, match='owned frame-zero producers still in callbacks'):
        source.poll()
    source.busy = False
    assert source.ack == 4

    opening_scope = stamp(root=battle.scope(), phase=phase, native_round=5,
                          when=0, collection_mode='all')
    opening_events = [activation(6, event_scope=opening_scope)]
    if early_peer_skill is not None:
        peer_start = activation(7, index=2, event_scope=opening_scope)
        if early_peer_skill == 'missing_uid':
            peer_start['source_character_uid'] = None
        opening_events.append(peer_start)
    battle.advance(0, opening_events)
    battle.advance(.5)
    battle.advance(.5, [source.hit(len(battle.records) + 1, 3, 31)])
    battle.advance(1)
    source.prefix_fault = prefix_fault
    first = source.poll()
    if old_tail == 'pending':
        assert [after for after, _ in calls] == [4]
    return source, old, first


@pytest.mark.parametrize('phase,old_tail', [
    (4, None), (4, 'expired'), (4, 'pending'), (5, None), (6, None),
], ids=['defense', 'defense-old-tail-expired', 'defense-old-tail-pending', 'assist', 'boss'])
def test_late_host_replays_exact_scope_prefix_and_keeps_previous_defense_isolated(
        tmp_path, phase, old_tail):
    source, old, first = enter_late(tmp_path, phase=phase, old_tail=old_tail)
    battle = source.battle
    assert first.kind == 'sample'
    assert first.sample['frame'] == 30 and first.sample['game_time'] == 1
    assert first.sample['global_total'] == old['global_total'] == 90000
    assert first.events['drain']['events'] == []
    assert source.baseline_reads[-1][:2] == (4, 7)
    assert source.ack == 7
    assert battle.runtime.sessions.adapter.event_stream.sequence == 7
    first_local = source.local()
    assert first_local['observed_damage'] == 31
    assert first_local['actor_ledgers'][0]['ledger']['observed_skill_damage'] == 31
    assert view_values(source.state.snapshot()) == [31]
    current = source.state.snapshot()['multiplayer']
    current_scope = current['scope']
    assert current_scope != old['multiplayer']['scope']
    assert current['scope_kind'] == {4: 'defense', 5: 'assist', 6: 'boss'}[phase]

    battle.advance(1.5)
    battle.advance(1.5, [source.hit(8, 3, 43), source.hit(9, 2, 19)])
    source.poll()
    player = source.local()
    lane = player['actor_ledgers'][0]['ledger']
    assert player['observed_damage'] == lane['observed_skill_damage'] == 74
    assert lane['skill_initial_unknown_hits'] == lane['skill_unclassified_hits'] == 0
    peer = battle.runtime.display_sample()['multiplayer']['players'][1]
    assert peer['actor_ledgers'][0]['ledger']['observed_non_skill_damage'] == 19
    assert source.ack == 9
    expected_panel = [74, 19] if phase == 5 else [74]
    assert view_values(source.state.snapshot()) == expected_panel

    source.state.accept('sample', dict(old, state=3))
    assert view_values(source.state.snapshot()) == expected_panel
    assert source.state.snapshot()['multiplayer']['scope'] == current_scope



@pytest.mark.parametrize('fault', ['missing_record', 'foreign_scope', 'foreign_generation'])
def test_unverified_round_origin_stops_event_coverage_without_ack(tmp_path, fault):
    source, _, first = enter_late(tmp_path, prefix_fault=fault)
    assert first.kind == 'sample'
    assert first.sample['frame'] == 30
    assert source.ack == 4
    assert view_values(source.state.snapshot()) == []
    assert source.battle.runtime.sessions.event_status == 'gap'


def test_missing_round_origin_uses_conservative_legacy_baseline(tmp_path):
    source, _, first = enter_late(tmp_path, prefix_fault='absent')
    assert first.kind == 'sample'
    assert source.local()['observed_damage'] is None
    assert source.ack == 7
    battle = source.battle
    battle.advance(1.5)
    battle.advance(1.5, [source.hit(8, 3, 43)])
    source.poll()
    player = source.local()
    lane = player['actor_ledgers'][0]['ledger']
    assert player['observed_damage'] == 43
    assert lane['skill_initial_unknown_hits'] == 1
    assert lane['observed_skill_damage'] is None


def test_origin_with_live_actors_replays_prefix_without_guessing_initial_skill(tmp_path):
    source, _, first = enter_late(tmp_path, prefix_fault='live_actors')
    assert first.kind == 'sample'
    assert source.ack == 7
    assert source.local()['observed_damage'] == 31
    assert source.local()['actor_ledgers'][0]['ledger']['observed_skill_damage'] == 31

    battle = source.battle
    battle.advance(1.5)
    battle.advance(1.5, [source.hit(8, 2, 19)])
    source.poll()
    peer = battle.runtime.display_sample()['multiplayer']['players'][1]
    ledger = peer['actor_ledgers'][0]['ledger']
    assert source.ack == 8 and peer['observed_damage'] == 19
    assert ledger['observed_non_skill_damage'] is None
    assert ledger['skill_initial_unknown_hits'] == 1


@pytest.mark.parametrize('phase', [4, 5])
@pytest.mark.parametrize('later_skill_gap', [False, True])
def test_zero_live_uid_proof_certifies_only_the_actor_absent_at_round_start(tmp_path, phase, later_skill_gap):
    source, _, first = enter_late(tmp_path, phase=phase, prefix_fault='guarded_live_actors')
    assert first.kind == 'sample'
    assert source.ack == 7
    battle = source.battle
    if later_skill_gap:
        original_batch = source.original_batch
        source.original_batch = lambda through: dict(original_batch(through), skill_unknown=True)
    battle.advance(1.5)
    battle.advance(1.5, [source.hit(8, 1, 19), source.hit(9, 2, 23)])
    source.poll()
    players = battle.runtime.display_sample()['multiplayer']['players']
    present = players[0]['actor_ledgers'][0]['ledger']
    absent = players[1]['actor_ledgers'][0]['ledger']
    assert present['observed_non_skill_damage'] is None
    assert present['skill_initial_unknown_hits'] == 1
    assert absent['observed_damage'] == 23
    assert absent['observed_non_skill_damage'] == (None if later_skill_gap else 23)
    assert absent['skill_initial_unknown_hits'] == 0
    assert absent['skill_unclassified_hits'] == int(later_skill_gap)
    assert source.ack == 9


@pytest.mark.parametrize('early_peer_skill', [None, 'unmapped_uid', 'missing_uid', 'foreign_scope'],
                         ids=['clean-prefix', 'unassigned-start', 'missing-uid-start', 'foreign-scope-start'])
def test_late_peer_inherits_known_origin_only_without_unverified_skill_history(tmp_path, early_peer_skill):
    prefix_skill = None if early_peer_skill == 'foreign_scope' else early_peer_skill
    source, _, first = enter_late(tmp_path, late_peer=True, early_peer_skill=prefix_skill)
    assert first.kind == 'sample'
    battle = source.battle
    if early_peer_skill == 'foreign_scope':
        # Ordinary post-baseline delivery can carry a fully stamped foreign
        # event. The certified opening prefix itself must be scope-pure.
        battle.advance(1.25)
        other_scope = stamp(root=source.old_scope_key, native_round=4,
                            when=1.25, collection_mode='all')
        other_start = activation(8, when=1.25, index=2, event_scope=other_scope)
        other_start['source_character_uid'] = None
        battle.advance(1.25, [other_start])
        source.poll()
    player_stats = battle.runtime.display_sample()['multiplayer']
    assert player_stats['players'][1]['actor_ledgers'] == []
    assert source.local()['observed_damage'] == 31
    starts = [(record['first_sequence'], record['source_character_uid'], record['reason'])
              for record in player_stats['unknown_attribution'] if record['kind'] == 'skill_start']
    assert starts == {
        None: [],
        'unmapped_uid': [(7, 201, 'source_uid_unmapped')],
        'missing_uid': [(7, None, 'source_uid_missing')],
        'foreign_scope': [(8, None, 'event_scope_mismatch')],
    }[early_peer_skill]

    source.peer_present = True
    next_sequence = source.ack + 1
    battle.advance(1.5)
    battle.advance(1.5, [source.hit(next_sequence, 2, 19)])
    source.poll()
    peer = battle.runtime.display_sample()['multiplayer']['players'][1]
    lane = peer['actor_ledgers'][0]['ledger']
    assert peer['observed_damage'] == 19
    assert source.ack == next_sequence
    if prefix_skill is not None:
        assert lane['observed_skill_damage'] is lane['observed_non_skill_damage'] is None
        assert lane['skill_initial_unknown_hits'] == 1
        assert lane['skill_initial_unknown_reason_codes'] == ['actor_has_unverified_skill_prefix']
    else:
        assert lane['observed_non_skill_damage'] == 19
        assert lane['skill_initial_unknown_hits'] == 0

