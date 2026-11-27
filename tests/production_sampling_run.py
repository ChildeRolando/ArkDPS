"""Run and retain the full production sampling test over synthetic owned bytes."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
from production_sampling_bundle import sampling_bundle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = sampling_bundle()
    script = args.output / 'production-sampling.js'
    script.write_text(source, encoding='utf8')
    run = subprocess.run([str(args.binary.resolve()), str(script.resolve())],
                         capture_output=True, text=True, encoding="utf8", timeout=30)
    (args.output / 'stdout.json').write_text(run.stdout, encoding='utf8')
    (args.output / 'stderr.log').write_text(run.stderr, encoding='utf8')
    digest = lambda data: hashlib.sha256(data).hexdigest()
    identity = json.loads((args.binary.parent / 'build-receipt.json').read_text())
    assert identity['binary_sha256'] == digest(args.binary.read_bytes())
    receipt = dict(build=identity, bundle_sha256=digest(source.encode()),
                   returncode=run.returncode, passed=False,
                   scope='real shared capturedClosedRecordProjection and full scope/publication/cold over owned synthetic memory',
                   excluded=['Game', 'FPS', 'detours', 'IPC',
                             'request_on_worker fresh/retry/confirm transaction; separately covered by owned-address fixture'])
    receipt_file = args.output / 'receipt.json'
    receipt_file.write_text(json.dumps(receipt, indent=2), encoding='utf8')
    assert run.returncode == 0, (run.returncode, run.stderr)
    result = json.loads(run.stdout)
    assert result['scope_fields'] == 64 and result['publication_fields'] == 1604
    assert all(row['raw'] == row['processed'] == row['closed'] == row['sequence'] == 0
               for row in result['rows'])
    receipt.update(passed=True, result=result)
    receipt_file.write_text(json.dumps(receipt, indent=2), encoding='utf8')
    print(receipt_file)


if __name__ == '__main__':
    main()
