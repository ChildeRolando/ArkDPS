"""Reconcile actual attacks from the replacement summon, including reused addresses."""
import json
from verify_recovery import events, near, paused, read, rows


def main():
    s = paused('summon-redeploy-guarded.json')
    token, host = 'token_10004_otter_motter', 'char_242_otter'
    operations = [o for o in s['operations'] if o['charId'] == token]
    assert [o['opName'] for o in operations] == ['SPAWN', 'SPAWN', 'WITHDRAW', 'SPAWN']
    spawn = operations[-1]
    assert spawn['gridCol'] == 4 and spawn['gridRow'] == 3
    replacement = next(c for c in s['characters'] if c['identity']['cid'] == token
                       and c['grid_col'] == 4 and c['grid_row'] == 3)
    hit_events = [e for e in events('summon-redeploy-guarded-events.jsonl')
                  if e['name'] == 'Entity.ApplyModifier'
                  and e.get('sourceEntity', {}).get('id') == token
                  and e['afterModifier']['targetType'] == 0
                  and e['afterModifier']['type'] == 1 and e['afterModifier']['value'] > 0]
    # Allocation addresses may be reused; require both the current position and the new spawn's time boundary.
    hits = [e for e in hit_events if e['sourceEntity']['address'] == hex(replacement['address'])
            and e['frame'] > spawn['timestamp'] * 30]
    total = sum(e['afterModifier']['value'] for e in hit_events)
    replacement_total = sum(e['afterModifier']['value'] for e in hits)
    near(total, rows(s)[token]['damage'])
    assert len(hit_events) == 37 and len(hits) == 7
    near(total, 6675)
    near(replacement_total, 1125)
    mountain = next(c for c in s['characters'] if c['identity']['cid'] == 'char_264_f12yin')
    assert mountain['skill']['runtime']['trigger_count'] == 1
    assert 'f12yin_s_2[effect]' in [b['key'] for b in mountain['buffs']]
    roster = read('summon-redeploy-guarded-roster.json')
    assert roster['coherent'] and roster['after']['frame'] == s['after']['frame']
    player = roster['players'][0]
    t = next(x for x in player['tokens'] if x['cid'] == token)
    h = next(x for x in player['characters'] if x['cid'] == host)
    assert t['token_or_host_key'] == host and t['token_or_host_uid'] == h['unique_id']
    print(json.dumps({'status': 'recorded replacement attacks pass', 'all_token_damage': total,
                      'replacement_hits': len(hits), 'replacement_damage': replacement_total,
                      'mountain_skill_uses': 1, 'whole_demo_accepted': False}, indent=2))


if __name__ == '__main__':
    main()
