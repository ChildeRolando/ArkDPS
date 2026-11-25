"""Evidence-only deployed-body intervals. Never infer endpoints from sampling."""
PERMANENT_REASONS = frozenset((1, 2, 3, 4, 5, 6, 8, 9))


def lifecycle_proof(value, *, kind, actor, instance, frame, game_time):
    if not isinstance(value, dict) or type(value.get('version')) is not int or value.get('version') != 1:
        return None
    if (type(actor) is not int or actor <= 0 or type(instance) is not int or instance <= 0
            or type(value.get('data_uid')) is not int or type(value.get('instance_uid')) is not int
            or value.get('data_uid') != actor or value.get('instance_uid') != instance
            or type(value.get('frame')) is not int or value['frame'] != frame
            or type(value.get('game_time')) not in (int, float) or value['game_time'] != game_time):
        return None
    fixed = value.get('fixed')
    if not isinstance(fixed, str) or not fixed.isascii() or not fixed.isdecimal():
        return None
    if abs(int(fixed) / 4294967296 - game_time) > 1e-8:
        return None
    if kind == 'character_born' and value.get('state') == 'born':
        return 'born'
    if (kind == 'skill_end' and value.get('state') == 'finish'
            and value.get('reason_type') == 'Torappu.Battle.Entity.FinishReason'
            and type(value.get('reason')) is int and value['reason'] in PERMANENT_REASONS):
        return 'finish'
    return None


class DeploymentLifecycle:
    def __init__(self):
        self.bodies = {}
        self.unknown = set()

    def observe(self, owner, actor, instance, kind, when, proof=None, *, frame=None):
        identity = (owner, actor)
        state = lifecycle_proof(proof, kind=kind, actor=actor, instance=instance,
                                frame=frame, game_time=when)
        if state == 'born':
            prior = self.bodies.get(identity)
            if prior and prior['end'] is None:
                self.unknown.add(owner)
            self.bodies.setdefault(identity, {'periods': [], 'instances': set()})
            body = self.bodies[identity]
            body['instances'].add(instance)
            body.update(instance=instance, start=when, end=None)
        elif state == 'finish':
            body = self.bodies.get(identity)
            if body and instance == body['instance'] and body['end'] == when:
                return
            if body and instance in body['instances'] and body['instance'] != instance:
                return
            if not body or body['instance'] != instance or body['end'] is not None:
                self.unknown.add(owner)
                return
            body['end'] = when
            body['periods'].append((body['start'], when))
        elif kind == 'character_born':
            self.unknown.add(owner)

    def seed_zero(self, owner, actor, instance):
        if (owner, actor) not in self.bodies:
            self.observe(owner, actor, instance, 'character_born', 0,
                dict(version=1, state='born', instance_uid=instance, data_uid=actor,
                     frame=0, game_time=0, fixed='0'), frame=0)

    def is_retired(self, owner, actor, instance):
        body = self.bodies.get((owner, actor))
        return bool(body and instance in body['instances'] and instance != body['instance'])

    def result(self, owner, watermark, *, continuous, archived, start_time=0):
        bodies = [body for (o, _), body in self.bodies.items() if o == owner]
        terminal = bool(bodies) and all(b['end'] is not None and b['end'] <= watermark for b in bodies)
        complete = (bool(bodies) and all(b['start'] <= watermark for b in bodies) and continuous and owner not in self.unknown
                    and (not archived or terminal))
        seconds = sum(sum(max(0.0, min(end, watermark) - start) for start, end in b['periods'])
                      + (max(0.0, watermark - b['start']) if b['end'] is None else 0)
                      for b in bodies)
        return dict(lifecycle_presence_seconds=seconds if complete else None,
                    lifecycle_presence_complete=complete, lifecycle_terminal_complete=terminal,
                    lifecycle_presence_window=([start_time, watermark]
                                               if complete else None))
