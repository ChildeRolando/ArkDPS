"""Build/run tiny actual-engine replay gates, with caller-supplied offline dependencies.
No MinHook/assembly entry points are executed: this is not DLL/IPC/Game/FPS acceptance.
"""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--minhook-header', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compiler', default='g++')
    parser.add_argument('--optimization', choices=['O0', 'O2'], default='O0')
    parser.add_argument('--fixture', choices=['bounded', 'production-hp'], default='bounded')
    parser.add_argument('--build-only', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    deps = output / 'dependencies'
    deps.mkdir(exist_ok=True)
    stage = args.stage.resolve()
    named = ['quickjs.o', 'dtoa.o', 'libregexp.o', 'libunicode.o', 'cutils.o']
    objects = [stage / name for name in named]
    if not all(p.is_file() for p in objects):
        objects = [stage / f'object-{i}.o' for i in range(5)]
    headers = stage / 'quickjs-headers' if (stage / 'quickjs-headers/quickjs.h').is_file() else stage
    json_header = stage / 'json.hpp'
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest(json_header) == '9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6'
    inputs = [*objects, headers / 'quickjs.h', json_header, args.minhook_header.resolve()]
    for source, name in zip(inputs, [*named, 'quickjs.h', 'json.hpp', 'MinHook.h']):
        shutil.copyfile(source, deps / name)
    # Declarations required by the included engine translation unit. Its normal
    # initialize_on_worker path is never called; bridge.js is evaluated from disk.
    for header, symbol in [('bridge_source.h','arkdps_bridge_source'), ('publication_source.h','arkdps_publication_source'), ('cold_plan_source.h','arkdps_cold_plan_source')]:
        (deps / header).write_text('// Syntax/link declaration only; never executed.\ninline constexpr const char ' + symbol + '[]="";\n')
    binary = output / 'owned-batch-test.exe'
    command = [args.compiler, '-std=c++17', '-static', '-' + args.optimization, '-pthread', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
               '-I', str(ROOT/'native_pc/native_event_runtime'), '-isystem', str(deps),
               str(ROOT/'tests'/('production_hp_actual_engine.cpp' if args.fixture=='production-hp' else 'owned_bounded_replay_test.cpp')), str(ROOT/'native_pc/native_event_runtime/native_capture.cpp'),
               *[str(deps/name) for name in named], '-lpsapi', '-o', str(binary)]
    build = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=90)
    (output/'build.log').write_text(build.stdout + build.stderr)
    assert build.returncode == 0, (build.returncode, build.stdout, build.stderr)
    if args.build_only:
        (output/'build-receipt.json').write_text(json.dumps({'executed':False,'fixture':args.fixture,'optimization':args.optimization,'binary_sha256':digest(binary),'dependency_sha256':{str(p):digest(p) for p in inputs}},indent=2))
        print('BUILT_OWNED_REPLAY', binary)
        return
    assert args.fixture=='bounded', 'production HP fixture requires --build-only and its explicit workload runner'
    cases = []
    for case in ['normal', 'fault', 'early', 'pending', 'string', 'nan', 'lease', 'health', 'nativefault', 'mode', 'phase', 'root', 'cp', 'hook', 'status', 'planned', 'false-health', 'no-cap', 'bool-cap', 'mutable-cap']:
        run = subprocess.run([str(binary), case], cwd=ROOT, capture_output=True, text=True, timeout=10)
        (output/(case+'.log')).write_text(run.stdout + run.stderr)
        assert run.returncode == 0 and 'PASS_OWNED_BATCH' in run.stdout, (case, run.returncode, run.stdout, run.stderr)
        cases.append({'case': case, 'pass': True})
    receipt = {'scope': 'actual engine prepare/finalize/controller + OwnedReplayView + QuickJS, self-owned memory',
               'excluded': ['detours', 'assembly ingress', 'IPC', 'production HP business source', 'throughput', 'Game FPS'],
               'optimization': args.optimization, 'cases': cases, 'binary_sha256': digest(binary),
               'dependency_sha256': {str(p): digest(p) for p in inputs}}
    (output/'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print('PASS_OWNED_BATCH_ALL', output/'receipt.json')

if __name__ == '__main__':
    main()
