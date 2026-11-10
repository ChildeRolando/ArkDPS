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
        value.update(name=name, damage=damage, healing=(i % 3) * 4567.89,
            recent_damage=(12-i)*97.23, recent_healing=(i % 3)*103.12,
            present=i not in (3, 8), shield_absorbed=2334.56 if i == 2 else None,
            engagement_damage=(12-i)*123.45, engagement_healing=(i % 3)*215.43,
            active_damage=9.3, active_healing=4.2, event_coverage_complete=False,
            sources=[owner], damage_by_type={'physical': damage*.55, 'arts': damage*.3,
                'elemental': damage*.1, 'true': damage*.05},
            recent_damage_by_type={'physical': (12-i)*97.23},
            recent_damage_window=[7., 12.], recent_healing_window=[7., 12.])
        rows[owner] = value
    original['statistics']['rows'] = rows
    archive = BattleRecord.from_dict(original)
    history = BattleHistory(directory / 'history')
    history.save(archive)
    history.save(record(1, stage_id='Obt/Roguelike/RO6/level_rogue6_1-3',
                        practice=None, outcome='unknown'))
    sample = dict(battle_id='synthetic-live', session='synthetic-live', frame=360,
        game_time=12., battle_metadata={'stage_id': 'Activities/ACT3D0/level_act3d0_08'},
        captured_monotonic=time.monotonic(), statistics_complete=True,
        event_status='failed', event_error='合成样本 · 未连接游戏',
        statistics=original['statistics'], rows=[{'id':'legacy','damage':999999999}])
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
