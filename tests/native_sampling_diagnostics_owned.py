"""Execute the actual server diagnostic filter in an isolated standard C++ host."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compiler', default='g++')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    server = (ROOT / 'native_pc/native_event_runtime/server.cpp').read_text(encoding='utf8')
    start = server.index('std::string safe_diagnostic(')
    end = server.index('\nDWORD WINAPI worker(', start)
    function = server[start:end]
    fixed = [
        'sampling plan requires independent clock roots and cold fallback',
        'sampling clock bracket is not independent and paired',
        'invalid sampling capture plan', 'sampling checkpoint serial exhausted',
        'sampling owned view rejected structural capture',
        'sampling checkpoint violated owned boundary', 'sampling publication failed',
        'sampling publication certificate mismatch', 'invalid sampling transaction operation',
        'invalid sample request identity', 'sample request no longer active',
        'sample request transaction binding changed', 'sample request identity exhausted',
        'native sampling capture plan changed during setup', 'sampling checkpoint lacks native authority', 'invalid sampling checkpoint', 'sampling publication lacks native authority',
    ]
    cases = [(text, text) for text in fixed]
    cases += [('InternalError: ' + text, text) for text in fixed]
    cases += [(f'sampling structural capture error: {code}', f'sampling structural capture error: {code}')
              for code in range(11)]
    malicious = ['sampling structural capture error: 11', 'sampling structural capture error: -1',
                 'sampling structural capture error: 01', 'sampling structural capture error: 99999999999999999',
                 'sampling publication failed; token=PRIVATE', 'sampling publication failed 0xPRIVATE',
                 'PRIVATE sampling publication failed', 'sampling publication certificate mismatch /PRIVATE',
                 'sampling publication failed' + 'x' * 5000]
    cases += [(text, '') for text in malicious]
    # JS stacks can follow a safe first line, but never cross the filter.
    cases.append(('Error: sampling publication failed\nPRIVATE stack 0xPRIVATE', 'sampling publication failed'))
    stage = 'native_sampling_failure:v1,s=7,c=32,j=599'
    gap = 'native_sampling_gap:v1,t=18446744073709551615,r=65535,p=65535,f=-1,g=16,m=1048576,k=0.31,q=1:999999.2:999999.1:999999'
    for text in [stage, 'native_sampling_failure:v1,s=1,c=0,j=0', gap, stage + '; ' + gap,
                 'InternalError: ' + stage + '; ' + gap]:
        cases.append((text, text.removeprefix('InternalError: ')))
    for old, new in [('s=7', 's=8'), ('s=7', 's=0'), ('c=32', 'c=33'), ('j=599', 'j=600'),
                     ('j=599', 'j=99'), ('j=599', 'j=001')]:
        cases.append((stage.replace(old, new), ''))
    for old, new in [('18446744073709551615', '18446744073709551616'), ('r=65535', 'r=65536'),
                     ('p=65535', 'p=65536'), ('f=-1', 'f=-2'), ('g=16', 'g=17'), ('g=16', 'g=0'),
                     ('m=1048576', 'm=1048577'), ('k=0.31', 'k=32'),
                     ('k=0.31', 'k=' + '.'.join(['1'] * 17)),
                     ('q=1:999999.2:999999.1:999999', 'q=3:1'),
                     ('q=1:999999.2:999999.1:999999', 'q=1:1.1:1.1:1.1:1')]:
        cases.append((gap.replace(old, new), ''))
    cases.append((stage + '; ' + gap + '; token=PRIVATE', ''))
    encoded = ',\n'.join('{' + json.dumps(text) + ',' + json.dumps(expected) + '}' for text, expected in cases)
    source = '#include <algorithm>\n#include <cstdint>\n#include <string>\n#include <vector>\n#include <regex>\n#include <iostream>\n' + function
    source += '\nint main(){const std::vector<std::pair<std::string,std::string>> cases{' + encoded + '};\n'
    source += r'''
for(const auto& test:cases){const auto value=safe_diagnostic(test.first.c_str(),"mark");
 const auto expected=std::string("native runtime mark failed; error_type=")+
  (test.first.rfind("InternalError:",0)==0?"InternalError":test.first.rfind("Error:",0)==0?"Error":"std::exception")+
  (test.second.empty()?"":"; "+test.second);
 if(value!=expected||value.find("PRIVATE")!=std::string::npos){std::cerr<<"diagnostic case failed\n";return 1;}}
std::cout<<"PASS_SAMPLING_DIAGNOSTICS "<<cases.size()<<"\n";return 0;}
'''
    cpp = args.output / 'diagnostic-filter.cpp'
    cpp.write_text(source, encoding='utf8')
    binary = args.output / 'diagnostic-filter.exe'
    build = subprocess.run([args.compiler, '-std=c++17', '-O2', '-static', '-Wall', '-Wextra', '-Werror',
                            str(cpp.resolve()), '-o', str(binary.resolve())], capture_output=True,
                           text=True, encoding='utf8', timeout=60)
    (args.output / 'build.log').write_text(build.stdout + build.stderr, encoding='utf8')
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(binary.resolve())], capture_output=True, text=True, timeout=10)
    (args.output / 'run.log').write_text(run.stdout + run.stderr, encoding='utf8')
    assert run.returncode == 0, (run.stdout, run.stderr)
    digest = lambda value: hashlib.sha256(value).hexdigest()
    receipt = dict(passed=True, cases=len(cases), server_sha256=digest(server.encode()),
                   extracted_function_sha256=digest(function.encode()),
                   binary_sha256=digest(binary.read_bytes()), scope='actual safe_diagnostic function, isolated native host')
    (args.output / 'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf8')
    print(run.stdout.strip())


if __name__ == '__main__':
    main()
