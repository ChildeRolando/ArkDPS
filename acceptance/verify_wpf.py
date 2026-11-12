"""Foreground WPF/stdio JSON Lines acceptance. Synthetic data; never accesses MuMu."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from battle_history import BattleHistory, BattleRecord
from test_battle_history import record


def seed(directory):
    directory.mkdir(parents=True, exist_ok=True)
    original = record(stage_id='Obt/Main/level_main_01-07', practice=True, outcome='victory').to_dict()
    row = original['statistics']['rows']['a']
    rows = {}
    names = [('char_002_amiya', '阿米娅'), ('char_017_huang', '煌'),
             ('char_003_kalts', '凯尔希'), ('char_010_chen', '陈'),
             ('char_009_12fce', '12F'), ('char_103_angel', '能天使'),
             ('char_112_siege', '推进之王'), ('char_134_ifrit', '伊芙利特'),
             ('char_147_shining', '闪灵'), ('char_179_cgbird', '夜莺'),
             ('char_202_demkni', '塞雷娅'), ('char_172_svrash', '银灰')]
    for i, (owner, name) in enumerate(names):
        value = copy.deepcopy(row)
        damage = (12 - i) * 9123.4567
        overkill = damage * .08
        value.update(name=name, damage=damage, healing=(i % 3) * 4567.89,
            overkill_damage=overkill, effective_damage=damage - overkill,
            overkill_damage_by_type={'physical': overkill*.55, 'arts': overkill*.3,
                'elemental': overkill*.1, 'true': overkill*.05},
            overkill_type_complete=True, types_available=True, types_stale=False,
            skill_damage=damage * .42, non_skill_damage=damage * .58,
            skill_damage_effective=(damage - overkill) * .42,
            non_skill_damage_effective=(damage - overkill) * .58,
            skill_split_complete=True, skill_split_effective_complete=True,
            skill_seen=True, skill_active_seconds=4.2,
            skill_coverage_complete=True, skill_split_reason=None,
            recent_damage=(12-i)*97.23, recent_healing=(i % 3)*103.12,
            present=i not in (3, 8), shield_absorbed=2334.56 if i == 2 else None,
            engagement_damage=(12-i)*123.45, engagement_healing=(i % 3)*215.43,
            active_damage=9.3, active_healing=4.2, presence_seconds=9.3,
            presence_complete=True, event_coverage_complete=False,
            sources=[owner], damage_by_type={'physical': damage*.55, 'arts': damage*.3,
                'elemental': damage*.1, 'true': damage*.05},
            recent_damage_by_type={'physical': (12-i)*97.23},
            recent_damage_window=[7., 12.], recent_healing_window=[7., 12.])
        rows[owner] = value
    original['statistics'].update(rows=rows, game_time=12., totals_complete=True)
    archive = BattleRecord.from_dict(original)
    history = BattleHistory(directory / 'history')
    history.save(archive)
    history.save(record(1, stage_id='Obt/Roguelike/RO6/level_rogue6_1-3',
                        practice=None, outcome='unknown'))
    series = [
        dict(game_time=0., entered_total_hp=18000., remaining_hp=18000.,
             our_effective_damage=0., our_damage_known=True, entered_count=2, alive_count=2, hp_complete=True),
        dict(game_time=6., entered_total_hp=26000., remaining_hp=14100.,
             our_effective_damage=11900., our_damage_known=True, entered_count=3, alive_count=2, hp_complete=True),
        dict(game_time=12., entered_total_hp=26000., remaining_hp=6200.,
             our_effective_damage=18700., our_damage_known=True, our_damage_complete=False,
             entered_count=3, alive_count=2, hp_complete=True),
    ]
    enemies = [
        dict(instance_uid='1', roster_id=1, spawn_order=1, eid='enemy_1007_slime', name='源石虫',
             code='B1', hp=1200., max_hp=2400., hp_known=True, hp_ratio=.5,
             atk=180., **{'def': 20.}, res=0., move_speed=.7, attack_speed=100.,
             alive=True, lifecycle='active', state_id=1, state='移动',
             spawn_frame=120, end_frame=None, end_reason=None,
             is_summon=False, position={'x': 1.5, 'y': 2.5}),
        dict(instance_uid='2', roster_id=2, spawn_order=2, eid='enemy_1048_hirman', name='碎岩者',
             code='B2', hp=600., max_hp=9800., hp_known=True, hp_ratio=600./9800.,
             atk=750., **{'def': 120.}, res=20., move_speed=.55, attack_speed=86.,
             alive=False, lifecycle='departed', state_id=None, state='已离场',
             spawn_frame=240, end_frame=330, end_reason='finish_7',
             is_summon=False, position={'x': 3.25, 'y': 4.0}),
        dict(instance_uid='3', roster_id=3, spawn_order=3, eid='enemy_1506_patrt', name='重装防御者',
             code='B3', hp=5000., max_hp=13800., hp_known=True, hp_ratio=.36,
             atk=320., **{'def': 400.}, res=0., move_speed=.45, attack_speed=100.,
             alive=True, lifecycle='active', state_id=2, state='攻击',
             spawn_frame=300, end_frame=None, end_reason=None,
             is_summon=False, position={'x': 6.0, 'y': 3.5}),
    ]
    enemy = dict(status='ready', error=None, ready=True, rows=enemies, non_team_native_damage=500,
                 damage_scope='entered',
                 totals=dict(entered_count=3, alive_count=2,
                             entered_total_hp=26000., remaining_hp=6200.,
                             stage_total_hp_estimate=36000., stage_total_hp_complete=False,
                             departed_hp=600., departed_hp_count=1,
                             recovered_hp=400., damage_capacity_with_recovery=26400.,
                             hp_complete=True),
                 series=series, last_point=series[-1])
    sample = dict(battle_id='synthetic-live', session='synthetic-live', frame=360,
        game_time=12., battle_metadata={'stage_id': 'Activities/ACT3D0/level_act3d0_08'},
        captured_monotonic=time.monotonic(), statistics_complete=True,
        event_status='failed', event_error='合成样本 · 未连接游戏',
        enemy=enemy, statistics=original['statistics'],
        rows=[{'id':'legacy','damage':999999999}])
    fixture = directory / 'fixture.json'
    fixture.write_text(json.dumps(sample, ensure_ascii=False), encoding='utf-8')
    return fixture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--migrate-v1', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT/'runtime'/'ark19-wpf')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not args.bundle:
        subprocess.run([os.environ.get('ARKDPS_DOTNET', 'dotnet'), 'build', str(ROOT/'desktop/ArkDPS.Desktop/ArkDPS.Desktop.csproj'),
                    '-c', 'Release', '--nologo'], cwd=ROOT, check=True)
    # Each invocation owns a NEW directory. Never overwrite a user's settings.
    import tempfile
    directory = Path(tempfile.mkdtemp(prefix='state-', dir=output))
    fixture = seed(directory)
    if args.migrate_v1:
        (directory/'layout.json').write_text(json.dumps(dict(version=1, panels=[dict(panel_id='default',
            category='damage', ranking='total', rate_basis='recent_5s')])), encoding='utf-8')
    executable = (args.bundle.resolve()/'ArkDPS.Desktop.exe') if args.bundle else ROOT/'desktop/ArkDPS.Desktop/bin/Release/net10.0-windows/ArkDPS.Desktop.exe'
    # Reject a mismatched worker before accepting any snapshots.
    if not args.bundle:
        incompatible = Path(tempfile.mkdtemp(prefix='wrong-version-', dir=output))
        (incompatible/'desktop_bridge.py').write_text(
            'import json,sys' + chr(10) +
            'command=json.loads(sys.stdin.readline())' + chr(10) +
            'print(json.dumps(dict(version=999,id=command["id"],ok=True,kind="hello",result={})),flush=True)' + chr(10), encoding='utf-8')
        rejected = subprocess.run([str(executable), '--repo', str(incompatible), '--python', sys.executable,
            '--offline', '--state-dir', str(incompatible/'state'), '--verify', str(incompatible/'evidence')],
            cwd=ROOT, timeout=30)
        assert rejected.returncode != 0
        assert '协议版本' in (incompatible/'evidence/failure.txt').read_text('utf-8')
    receipts = []
    for phase in ('first-launch', 'restart'):
        target = output/phase
        command = [str(executable)] + ([] if args.bundle else ['--repo', str(ROOT), '--python', sys.executable])
        subprocess.run(command + ['--offline', '--state-dir', str(directory), '--fixture', str(fixture),
                        '--verify', str(target)], cwd=ROOT, timeout=120, check=True)
        receipt = json.loads((target/'receipt.json').read_text('utf-8'))
        assert ('share scope reaches live, history, HUD, details and ranking PNG with legacy fallback'
                in receipt['checks']), 'share scope UI checks did not run'
        if args.bundle:
            assert receipt['worker_runtime']['python'] == '3.12.10'
            assert Path(receipt['worker_runtime']['executable']).resolve() == (args.bundle/'python/python.exe').resolve()
            assert receipt['worker_runtime']['frida'] == '17.26.1'
        receipts.append(receipt)
    import struct
    for phase in ('first-launch', 'restart'):
        for theme in ('lone-trail', 'original'):
            width, height = struct.unpack('>II', (output/phase/(theme+'-ranking.png')).read_bytes()[16:24])
            assert width == 900 and height > 900, 'full ranking export clipped to visible viewport'
    assert receipts[1]['restored_layout'] == receipts[0]['layout'], 'fresh process did not read exact saved layout'
    files = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in output.rglob('*.png')}
    receipt = dict(status='PASS_OFFLINE_WPF', head=subprocess.check_output([os.environ.get('ARKDPS_GIT', 'git'),'rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                   checks=['Native WPF + versioned parent-child stdio JSON Lines', 'Separate process restart matches saved v3 layout'],
                   phases=receipts, screenshots_sha256=files,
                   limitations=['Synthetic data only; no MuMu/game input acceptance. See phase receipts for physical tray result.'])
    (output/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS_OFFLINE_WPF', output)


if __name__ == '__main__':
    main()
