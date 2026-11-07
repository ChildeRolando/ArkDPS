"""Validate recorded Catherine shield attribution and Mountain toggle correction."""
import json
from collections import defaultdict

from verify_recovery import events, near, paused, read


def main():
    before = paused('mountain-toggle-audit.json')
    fixed = paused('mountain-toggle-corrected.json')
    later = paused('summon-shield-cold.json')
    mountain = 'char_264_f12yin'
    def char(snapshot, cid):
        return next(x for x in snapshot['characters'] if x['identity']['cid'] == cid)
    b, f, l = (char(x, mountain) for x in (before, fixed, later))
    assert b['skill']['runtime']['trigger_count'] == 10
    assert f['skill']['runtime']['trigger_count'] == l['skill']['runtime']['trigger_count'] == 11
    assert 'f12yin_s_2[effect]' not in [x['key'] for x in b['buffs']]
    assert 'f12yin_s_2[effect]' in [x['key'] for x in f['buffs']]
    assert 'f12yin_s_2[effect]' in [x['key'] for x in l['buffs']]
    token, host = 'token_10041_cathy_catsld', 'char_4162_cathy'
    source_ids = {hex(x['address']): x['identity']['cid'] for x in fixed['characters']}
    totals = defaultdict(float)
    after_fix = 0
    breaks = []
    es = events('summon-shield-events.jsonl')
    for e in es:
        if e['name'] != 'BlockDamage._AssignDynamicVar':
            continue
        n = int(e['damageArg'], 16)
        absorbed = (n - 2**64 if n >= 2**63 else n) / 2**32
        assert absorbed >= 0
        near(e['blackboard']['dynamic'] - e['afterBlackboard']['dynamic'], absorbed, 0.001)
        cid = source_ids[e['currentBuff']['source']]
        assert cid in (token, 'char_147_shining')
        totals[cid] += absorbed
        if cid == token and e['frame'] > fixed['after']['frame'] and absorbed > 0:
            after_fix += 1
        if cid == token and absorbed > 0 and e['afterBlackboard']['dynamic'] == 0:
            assert absorbed < e['modifier']['value']
            applies = [a for a in es if a['name'] == 'Entity.ApplyModifier'
                       and a['frame'] == e['frame'] and a['self'] == e['modifier']['target']
                       and a['modifier']['source'] == e['modifier']['source']]
            assert len(applies) == 1
            a = applies[0]
            near(e['modifier']['value'] - absorbed, a['entity']['hp'] - a['afterEntity']['hp'])
            breaks.append(e['frame'])
    near(totals[token], 155.77999303489923)
    near(totals['char_147_shining'], 112.00000166893005)
    assert after_fix > 0 and breaks == [2711]
    assert not {host, token} & {x['identity']['cid'] for x in later['characters']}
    roster = read('summon-shield-roster-after-cleanup.json')
    assert roster['coherent'] and roster['after']['frame'] == later['after']['frame']
    player = roster['players'][0]
    t = next(x for x in player['tokens'] if x['cid'] == token)
    h = next(x for x in player['characters'] if x['cid'] == host)
    assert t['token_or_host_key'] == host and t['token_or_host_uid'] == h['unique_id']
    print(json.dumps({'status': 'recorded cases pass', 'shield_totals': dict(totals),
                      'mountain_triggers_before_after': [10, 11], 'break_frame': 2711,
                      'cold_owner_mapping': True, 'whole_demo_accepted': False}, indent=2))


if __name__ == '__main__':
    main()
