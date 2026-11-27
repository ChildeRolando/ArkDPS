"""Retain fixed-binary/source receipts for cold class gate regressions."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
from production_root_class_bundle import ROOT, root_class_bundle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--expect-broken', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = root_class_bundle(args.source_root)
    script = args.output / 'root-class.js'
    script.write_text(source, encoding='utf8')
    run = subprocess.run([str(args.binary.resolve()), str(script.resolve())],
                         capture_output=True, text=True, encoding='utf8', timeout=30)
    (args.output / 'stdout.json').write_text(run.stdout, encoding='utf8')
    (args.output / 'stderr.log').write_text(run.stderr, encoding='utf8')
    digest = lambda data: hashlib.sha256(data).hexdigest()
    identity = json.loads((args.binary.parent / 'build-receipt.json').read_text())
    assert identity['binary_sha256'] == digest(args.binary.read_bytes())
    receipt = dict(build=identity, source_root=str(args.source_root.resolve()),
                   runtime_source_sha256=digest((args.source_root/'event_probe_runtime.js').read_bytes()),
                   bundle_sha256=digest(source.encode()), binary_sha256=digest(args.binary.read_bytes()),
                   returncode=run.returncode, expect_broken=args.expect_broken, passed=False)
    target = args.output / 'receipt.json'
    target.write_text(json.dumps(receipt, indent=2), encoding='utf8')
    if args.expect_broken:
        assert run.returncode != 0 and 'native_sampling_gap:v1,t=0,r=1,p=0,f=20481,g=2,m=0,k=10.10' in run.stderr
        receipt.update(passed=True, reproduced_old_failure=True)
    else:
        assert run.returncode == 0, (run.returncode, run.stderr)
        result = json.loads(run.stdout)
        assert len(result['cases']) == 6 and len(result['rows']) == 13
        assert result['missing']['native_status'] == 9
        receipt.update(passed=True, result=result)
    target.write_text(json.dumps(receipt, indent=2), encoding='utf8')
    print(target)


if __name__ == '__main__':
    main()
