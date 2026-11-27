"""Evidence-only deployed-body intervals. Never infer endpoints from sampling."""
from math import isfinite
PERMANENT_REASONS = frozenset((1, 2, 3, 4, 5, 6, 8, 9))


def lifecycle_proof(value, *, kind, actor, instance, frame, game_time):
    if not isinstance(value, dict) or type(value.get('version')) is not int or value.get('version') != 1:
        return None
    if (type(actor) is not int or not 0 < actor <= 0xffffffff or type(instance) is not int or not 0 < instance <= 0xffffffff
            or type(value.get('data_uid')) is not int or type(value.get('instance_uid')) is not int
            or value.get('data_uid') != actor or value.get('instance_uid') != instance
            or type(frame) is not int or not 0 <= frame <= 0xffffffff
            or type(game_time) not in (int, float) or game_time < 0 or game_time > 2147483648 or not isfinite(game_time)
            or type(value.get('frame')) is not int or value['frame'] != frame
            or type(value.get('game_time')) not in (int, float) or value['game_time'] != game_time):
        return None
    fixed = value.get('fixed')
    if (not isinstance(fixed, str) or not 1 <= len(fixed) <= 19 or not fixed.isascii() or not fixed.isdecimal()
            or (len(fixed) > 1 and fixed.startswith('0'))):
        return None
    if int(fixed) > (1 << 63) - 1 or int(fixed) / 4294967296 != game_time:
        return None
    if kind == 'character_born' and value.get('state') == 'born':
        return 'born'
    if (kind == 'skill_end' and value.get('state') == 'finish'
            and value.get('reason_type') == 'Torappu.Battle.Entity.FinishReason'
            and type(value.get('reason')) is int and value['reason'] in (11, 12)):
        return 'temporary'
    if (kind == 'skill_end' and value.get('state') == 'finish'
            and value.get('reason_type') == 'Torappu.Battle.Entity.FinishReason'
            and type(value.get('reason')) is int and value['reason'] in PERMANENT_REASONS):
        return 'finish'
    return None


class DeploymentLifecycle:
    def __init__(self):
        self.bodies = {}
        self.unknown = set()
        self.body_output_starts = {}

    def observe(self, owner, actor, instance, kind, when, proof=None, *, frame=None, expected_finish=False):
        identity = (owner, actor)
        state = lifecycle_proof(proof, kind=kind, actor=actor, instance=instance,
                                frame=frame, game_time=when)
        if state == 'born':
            ledger = self.bodies.setdefault(identity, {'instances': {}, 'instance': instance})
            prior = ledger['instances'].get(instance)
            if prior is not None:
                # A repeated source birth/zero seed is idempotent, never a redeploy.
                if prior['start'] != when:
                    self.unknown.add(owner)
                return
            ledger['instances'][instance] = dict(start=when, end=None)
            ledger['instance'] = instance
        elif state == 'finish':
            ledger = self.bodies.get(identity)
            body = ledger['instances'].get(instance) if ledger else None
            if body is None or when < body['start']:
                self.unknown.add(owner)
            elif body['end'] is None:
                body['end'] = when
            elif body['end'] != when and ledger['instance'] == instance:
                self.unknown.add(owner)
        elif kind == 'character_born' or (kind == 'skill_end' and state != 'temporary'
                and (proof is not None or expected_finish)):
            self.unknown.add(owner)

    def note_body_output(self, owner, actor, instance, when):
        # A body output can reveal a missing earlier lifetime prefix. It is
        # only an identity/time witness, never another damage/healing counter.
        if type(instance) is int and 0 < instance <= 0xffffffff:
            key = (owner, actor, instance)
            self.body_output_starts[key] = min(when, self.body_output_starts.get(key, when))
        else:
            self.unknown.add(owner)

    def seed_zero(self, owner, actor, instance):
        self.observe(owner, actor, instance, 'character_born', 0,
            dict(version=1, state='born', instance_uid=instance, data_uid=actor,
                 frame=0, game_time=0, fixed='0'), frame=0)

    def is_retired(self, owner, actor, instance):
        ledger = self.bodies.get((owner, actor))
        return bool(ledger and instance in ledger['instances'] and instance != ledger['instance'])

    def result(self, owner, watermark, *, continuous, archived, start_time=0, terminal_watermark=None):
        ledgers = [ledger for (o, _), ledger in self.bodies.items() if o == owner]
        bodies = [body for ledger in ledgers for body in ledger['instances'].values()]
        ambiguous = False
        for (output_owner, actor, instance), first in self.body_output_starts.items():
            if output_owner != owner:
                continue
            ledger = self.bodies.get((owner, actor))
            body = ledger['instances'].get(instance) if ledger else None
            if body is None or first < body['start']:
                ambiguous = True
        for ledger in ledgers:
            periods = sorted(ledger['instances'].values(), key=lambda body: body['start'])
            for left, right in zip(periods, periods[1:]):
                # A same-clock replacement can arrive Born-new before Finish-old.
                # It becomes complete only after the actual old endpoint arrives.
                if left['end'] is None or left['end'] > right['start']:
                    ambiguous = True
        terminal = bool(bodies) and (all(b['end'] is not None and b['end'] <= watermark for b in bodies)
                                    or continuous and terminal_watermark == watermark)
        complete = (bool(bodies) and all(b['start'] <= watermark for b in bodies) and continuous
                    and owner not in self.unknown and not ambiguous and (not archived or terminal))
        seconds = sum(max(0.0, min(b['end'] if b['end'] is not None else watermark, watermark) - b['start'])
                      for b in bodies)
        return dict(lifecycle_presence_seconds=seconds if complete else None,
                    lifecycle_presence_complete=complete, lifecycle_terminal_complete=terminal,
                    lifecycle_presence_window=([start_time, watermark] if complete else None))
