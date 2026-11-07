"""Validate the user-deployed Silence drones and cold attribution to a borrowed host."""
import json
from verify_recovery import events, near, paused, read, rows


def main():
    token, host = 'token_10000_silent_healrb', 'char_108_silent'
    s = paused('drone-support-paused.json')
    cold = paused('drone-support-cold.json')
    heal = [e for e in events('drone-support-events.jsonl')
            if e['name'] == 'Entity.ApplyModifier'
            and e.get('sourceEntity', {}).get('id') == token
            and e['afterModifier']['targetType'] == 0 and e['afterModifier']['type'] == 4]
    assert len(heal) == 60 and {e['entity']['id'] for e in heal} == {'char_502_nblade'}
    total = sum(e['afterModifier']['value'] for e in heal)
    effective = sum(e['afterModifier']['realDelta'] for e in heal)
    near(total, 7500)
    near(effective, 4181.000001981854)
    for e in heal:
        near(e['afterEntity']['hp'] - e['entity']['hp'], e['afterModifier']['realDelta'])
        assert e['afterModifier']['value'] >= e['afterModifier']['realDelta'] >= 0
    for sample in (s, cold):
        near(rows(sample)[token]['healing'], total)
        near(rows(sample)[host]['healing'], 0)
    spawns = [o for o in s['operations'] if o['charId'] == token and o['opName'] == 'SPAWN']
    assert len(spawns) == 3
    assert not {host, token} & {c['identity']['cid'] for c in cold['characters']}
    r = read('drone-support-cold-roster.json')
    assert r['coherent'] and r['after']['frame'] == cold['after']['frame']
    p = r['players'][0]
    t = next(x for x in p['tokens'] if x['cid'] == token)
    h = next(x for x in p['characters'] if x['cid'] == host)
    assert h['is_assist'] and h['evolve_phase'] == 2 and h['level'] == 80
    assert h['main_skill_index'] == 1
    assert t['token_or_host_key'] == host and t['token_or_host_uid'] == h['unique_id']
    print(json.dumps({'status': 'recorded summon healing passes', 'deployments': 3,
                      'heal_events': len(heal), 'native_total_healing': total,
                      'effective_hp_restored_audit_only': effective,
                      'cold_borrowed_owner_mapping': True, 'drone_deployment': 'user assisted',
                      'whole_demo_accepted': False}, indent=2))


if __name__ == '__main__':
    main()
