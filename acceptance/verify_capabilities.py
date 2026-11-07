"""Replay captured capability evidence offline; never connect to the game.

These checks cover the recorded cases, not every operator or shield mechanic.
Run: python acceptance/verify_capabilities.py
"""
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent / 'capabilities'


def read(name):
    return json.loads((HERE / name).read_text(encoding='utf-8'))


def lines(name):
    return [json.loads(s) for s in (HERE / name).read_text(encoding='utf-8').splitlines()]


def near(a, b, tolerance=0.001):
    assert math.isfinite(a) and math.isfinite(b)
    assert abs(a - b) <= tolerance, (a, b)


def paused(name):
    d = read(name)
    assert d['coherent'] and d['before']['session'] == d['after']['session']
    assert d['before']['frame'] == d['after']['frame']
    for s in (d['before'], d['after']):
        g = s['guard']
        assert g['complete'] and g['start'] == g['end'] == s['frame']
    return d


def rows(d):
    return {r['id']: r for r in d['after']['rows']}


def events(name):
    records = lines(name)
    assert not any(r.get('type') == 'error' for r in records)
    assert any(r.get('payload', {}).get('ready') for r in records)
    es = [e for r in records for e in r.get('payload', {}).get('events', [])]
    assert es and not any('error' in e or 'afterError' in e for e in es)
    return es


def fp_argument(s):
    n = int(s, 16)
    if n >= 2**63:
        n -= 2**64
    return n / 2**32


def shield_check(name, require_hp=False):
    es = events(name)
    shields = [e for e in es if e['name'] == 'BlockDamage._AssignDynamicVar']
    assert shields and len({e['seq'] for e in shields}) == len(shields)
    if require_hp:
        casters = {e['sourceEntity']['address'] for e in es
                   if e.get('sourceEntity', {}).get('id') == 'char_147_shining'}
        assert casters and all(e['currentBuff']['source'] in casters for e in shields)
    breaks = []
    for e in shields:
        assert e['frame'] == e['afterFrame']
        assert e['currentBuff']['key'] == 'shining_s_2'
        assert e['currentBuff']['hasShield'] == 1
        before, after = e['blackboard']['dynamic'], e['afterBlackboard']['dynamic']
        absorbed = fp_argument(e['damageArg'])
        assert absorbed > 0 and 0 <= after < before
        near(before - after, absorbed)
        near(min(before, e['modifier']['value']), absorbed)
        if after != 0:
            continue
        frame, target, source = e['frame'], e['modifier']['target'], e['modifier']['source']
        # A partial shield break did NOT call OnBlockDamage in these captures.
        block = [x for x in es if x['name'] == 'Entity.OnBlockDamage'
                 and x['frame'] == frame and x['self'] == target]
        assert not block
        takes = [x for x in es if x['name'] == 'Entity.OnTakeDamage'
                 and x['frame'] == frame and x['self'] == target
                 and x['modifier']['source'] == source]
        assert len(takes) == 1
        remaining = takes[0]['afterModifier']['value']
        near(e['modifier']['value'], absorbed + remaining)
        assert remaining > 0
        result = dict(frame=frame, incoming=e['modifier']['value'], absorbed=absorbed,
                      remaining=remaining, buff_uid=e['currentBuff']['uid'])
        if require_hp:
            applies = [x for x in es if x['name'] == 'Entity.ApplyModifier'
                       and x['frame'] == frame and x['self'] == target
                       and x['modifier']['source'] == source]
            assert len(applies) == 1
            a = applies[0]
            assert a['entity']['id'] == 'char_502_nblade'
            near(a['entity']['hp'] - a['afterEntity']['hp'], remaining)
            near(-a['afterModifier']['realDelta'], remaining)
            result.update(hp_before=a['entity']['hp'], hp_after=a['afterEntity']['hp'])
        breaks.append(result)
    assert len(breaks) >= 2
    return dict(events=len(shields), absorbed=sum(fp_argument(e['damageArg']) for e in shields),
                partial_breaks=breaks, callbacks=dict(Counter(e['name'] for e in es)))


def main():
    for name, meta in read('manifest.json').items():
        content = (HERE / name).read_bytes()
        assert len(content) == meta['bytes']
        assert hashlib.sha256(content).hexdigest() == meta['sha256'], name

    a, b = (paused(n) for n in ('summon-physical-1.json', 'summon-physical-2.json'))
    ar, br = rows(a), rows(b)
    host, token = 'char_242_otter', 'token_10004_otter_motter'
    assert a['after']['session'] == b['after']['session']
    near(ar[host]['damage'], br[host]['damage'])
    near(br[token]['physical'] - ar[token]['physical'], 740)
    tokens = [c for c in b['characters'] if c['identity'].get('is_token')]
    owner = next(c for c in b['characters'] if c['identity'].get('cid') == host)
    assert len(tokens) == 2 and len({t['address'] for t in tokens}) == 2
    assert len({t['identity']['data_ptr'] for t in tokens}) == 1
    for t in tokens:
        assert t['identity']['token_or_host_uid'] == owner['identity']['unique_id']
        assert t['identity']['token_or_host_key'] == host
    retired, recovered = (paused(n) for n in ('summon-retired.json', 'summon-retired-reconnected.json'))
    assert rows(retired) == rows(recovered) == br
    summon = dict(host_damage=br[host]['damage'], token_damage=br[token]['damage'],
                  normalized_host_damage=br[host]['damage'] + br[token]['damage'],
                  isolated_token_increment=740, token_entities=2, shared_counter_entries=1)

    before, after = (paused(n) for n in ('shield-before-active-gap.json', 'active-gap-reconnected.json'))
    assert before['after']['session'] == after['after']['session']
    assert before['after']['frame'] == 2477 and after['after']['frame'] == 2728
    old, new = rows(before), rows(after)
    near(new['char_502_nblade']['damage'] - old['char_502_nblade']['damage'], 1645)
    near(new['char_147_shining']['healing'] - old['char_147_shining']['healing'], 1455.900390625)
    assert before['verbose_recorder'] == after['verbose_recorder'] == 0
    assert before['operation_log_count'] == after['operation_log_count'] == 2
    assert {o['opName'] for o in after['operations']} == {'SPAWN'}
    for name in ('history-runtime-expanded.json', 'history-late-fields.json'):
        h = read(name)
        assert h['coherent'] and h['verbose'] == 0 and h['field_count'] == 44
        assert sum(not f['static'] for f in h['fields']) == 34
        assert h['extras']['extraBattleInfo'] == []

    # An unused shield expired without consuming its remaining 316.5 points.
    polls = [r for r in lines('shield-live-1.jsonl') if 'rejected' not in r]
    observations = [(r['frame'], c, bf) for r in polls for c in r['characters']
                    for bf in c['buffs'] if bf['uid'] == 103]
    assert observations
    for _, _, bf in observations:
        near(next(v['value'] for v in bf['blackboard'] if v['key'] == 'dynamic'), 316.5)
    last_frame, last_char, last_buff = observations[-1]
    assert last_buff['remaining'] < 0.04
    later = [r for r in polls if r['frame'] > last_frame]
    assert later and not any(bf['uid'] == 103 for c in later[0]['characters'] for bf in c['buffs'])

    # Native ApplyModifier events include non-HP changes (e.g. SP). Filter those
    # out, and use the final modifier value: healing amplification occurs inside
    # the call. realDelta is effective HP change, unlike the inclusive totals.
    final = paused('of8-shield-hp-paused.json')
    assert final['after']['frame'] == 2964 and final['verbose_recorder'] == 0
    es = events('shield-events-of8-hp.jsonl')
    output = {}
    for cid, kind, metric in [('char_502_nblade', 1, 'damage'),
                               ('char_017_huang', 1, 'damage'),
                               ('char_147_shining', 4, 'healing')]:
        matching = [e for e in es if e['name'] == 'Entity.ApplyModifier'
                    and e.get('sourceEntity', {}).get('id') == cid
                    and e['afterModifier']['targetType'] == 0
                    and e['afterModifier']['type'] == kind]
        assert matching
        total = sum(e['afterModifier']['value'] for e in matching)
        native_total = rows(final)[cid][metric]
        # The game counters are floats, whereas event values are fixed point.
        near(total, native_total, 0.01)
        output[cid] = dict(metric=metric, events=len(matching), event_sum=total,
                           counter=native_total, difference=native_total-total)

    result = dict(scope='MuMu / Android 2.7.91 recorded cases; not product acceptance',
                  summon=summon,
                  reconnect=dict(damage_increment=1645, healing_increment=1455.900390625,
                                 complete_native_history='NOT ESTABLISHED'),
                  unused_shield_expiry=dict(uid=103, unconsumed=316.5, last_frame=last_frame),
                  native_output_counter_comparison=output,
                  shield_first_run=shield_check('shield-events-of8.jsonl'),
                  shield_hp_run=shield_check('shield-events-of8-hp.jsonl', require_hp=True))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
