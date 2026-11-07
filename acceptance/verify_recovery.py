"""Reconcile recorded recovery experiments offline. No game connection."""
import hashlib
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent / 'recovery'


def read(name):
    return json.loads((HERE / name).read_text(encoding='utf-8'))


def records(name):
    return [json.loads(x) for x in (HERE / name).read_text(encoding='utf-8').splitlines()]


def events(name):
    raw = records(name)
    assert not any(x.get('type') == 'error' for x in raw)
    result = [e for x in raw for e in x.get('payload', {}).get('events', [])]
    assert result and not any('error' in e or 'afterError' in e for e in result)
    return result


def near(a, b, tolerance=0.01):
    assert abs(a - b) <= tolerance, (a, b)


def paused(name):
    x = read(name)
    assert x['coherent'] and x['before']['session'] == x['after']['session']
    assert x['before']['frame'] == x['after']['frame']
    for sample in [x['before'], x['after']]:
        g = sample['guard']
        assert g['complete'] and g['start'] == g['end'] == sample['frame']
    return x


def rows(snapshot):
    return {r['id']: r for r in snapshot['after']['rows']}


def main():
    for name, expected in read('manifest.json').items():
        raw = (HERE / name).read_bytes()
        assert len(raw) == expected['bytes']
        assert hashlib.sha256(raw).hexdigest() == expected['sha256'], name

    before = read('shield-ledger-before.json')
    recovered = read('shield-ledger-recovered.json')
    final = read('shield-device-final.json')
    journal = records('shield-device-final.events.jsonl')
    assert not before['errors'] and not recovered['errors'] and not final['errors']
    assert not recovered['stopped'] and final['stopped']
    assert before['started'] == recovered['started'] == final['started']
    assert recovered['totals'] == final['totals']
    assert [x['index'] for x in journal] == list(range(1, final['count'] + 1))
    summed = defaultdict(float)
    for x in journal:
        summed[x['source']['id'] + '/' + x['metric']] += x['amount']
    assert set(summed) == set(final['totals'])
    for key, value in summed.items():
        near(value, final['totals'][key], 0.00001)

    old, new = paused('shield-gap-before.json'), paused('shield-gap-after.json')
    assert old['after']['session'] == new['after']['session']
    assert old['after']['frame'] == before['frame'] == 1215
    assert new['after']['frame'] == recovered['frame'] == 2429
    assert all(c['shield'] == 0 for c in new['characters'])
    key = 'char_147_shining/shield'
    gap = [x for x in journal if x['metric'] == 'shield' and x['event']['frame'] > before['frame']]
    near(final['totals'][key], 753.5)
    near(before['totals'][key], 91.5)
    near(sum(x['amount'] for x in gap), 662)
    near(final['totals'][key] - before['totals'][key], sum(x['amount'] for x in gap))
    for cid, metric in [('char_502_nblade', 'damage'), ('char_017_huang', 'damage'),
                        ('char_147_shining', 'healing')]:
        near(summed[cid + '/' + metric], rows(new)[cid][metric])

    # Two live entities share one token counter; sum source events once.
    concurrent = paused('summon-concurrent-paused.json')
    token = 'token_10004_otter_motter'
    es = events('summon-concurrent-events.jsonl')
    attacks = [e for e in es if e['name'] == 'Entity.ApplyModifier'
               and e.get('sourceEntity', {}).get('id') == token
               and e['frame'] <= concurrent['after']['frame']]
    assert len({e['sourceEntity']['address'] for e in attacks}) == 2
    near(sum(e['afterModifier']['value'] for e in attacks), rows(concurrent)[token]['damage'])

    cold = paused('summon-cold-recovered.json')
    roster = paused('summon-roster-after-cleanup.json')
    host = 'char_242_otter'
    assert cold['after']['session'] == roster['after']['session']
    assert not any(c['identity'].get('cid') in (host, token) for c in cold['characters'])
    host_data = [r for player in roster['players'] for r in player['characters'] if r['cid'] == host]
    token_data = [r for player in roster['players'] for r in player['tokens'] if r['cid'] == token]
    assert len(host_data) == len(token_data) == 1
    assert token_data[0]['is_token'] and not host_data[0]['is_token']
    assert token_data[0]['token_or_host_uid'] == host_data[0]['unique_id']
    assert token_data[0]['token_or_host_key'] == host
    es = events('summon-cold-events.jsonl')
    for cid in [host, token]:
        attacks = [e for e in es if e['name'] == 'Entity.ApplyModifier'
                   and e.get('sourceEntity', {}).get('id') == cid
                   and e['afterModifier']['targetType'] == 0]
        near(sum(e['afterModifier']['value'] for e in attacks), rows(cold)[cid]['damage'])

    result = dict(
        scope='Recorded Android 2.7.91 cases; not complete product acceptance',
        shield=dict(device_observer_continued=True, host_reference_observer_continued=False,
                    before=before['totals'][key], recovered=final['totals'][key], gap=662,
                    native_remaining_shields=0, recovery_without_continuing_observer='NOT ESTABLISHED'),
        summon=dict(two_entities_one_counter=True, live_entities_absent_on_cold_read=True,
                    roster_owner_mapping_recovered=True, host_damage=rows(cold)[host]['damage'],
                    token_damage=rows(cold)[token]['damage'],
                    normalized_damage=rows(cold)[host]['damage'] + rows(cold)[token]['damage'],
                    replacement_attack='not observed in these OF-2 captures'))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
