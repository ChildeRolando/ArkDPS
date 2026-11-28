"""Transfer fixed, already-verified rc50 CI bytes into its existing draft only."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import zipfile

REPO = 'ChildeRolando/ArkDPS'
HEAD = '284d95afc52fd7cacdddef6f1afd80e9de0b9992'
TAG = 'v1.0.0-rc.50'
RUN = 46155306694
ARTIFACT = 15268390357
RELEASE = 461635250
OUTER_SHA = '4071ea7c7537dd0e507a4b19b343103946ad677dd4014bbfea2916a0393c21e3'
FULL = 'ArkDPS-1.0.0-rc.50-win-x64.zip'
DELTA = 'ArkDPS-1.0.0-rc.50-win-x64-update.zip'
ZIP_SHA = {
    FULL: '58edf5d61e9e2566e5bb0fdc79d839700dd54c0cbc8429ecc39945f2337f1021',
    DELTA: 'b6a72106e955c8162a7d4d46664ff8d4a3a1aadc2f4c395577805c8f439e5d17',
}
NAMES = (FULL, FULL + '.sha256', FULL + '.acceptance.json', DELTA, DELTA + '.sha256')


def api(endpoint):
    return json.loads(subprocess.check_output(['gh', 'api', 'repos/' + REPO + '/' + endpoint]))


def file_sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def verify_tag():
    obj = api('git/ref/tags/' + TAG)['object']
    for _ in range(3):
        if obj['type'] == 'commit':
            assert obj['sha'] == HEAD, 'Tag points to another commit'
            return
        assert obj['type'] == 'tag'
        obj = api('git/tags/' + obj['sha'])['object']
    raise AssertionError('Tag did not resolve to the fixed build')


def verify_draft(release, paths):
    assert release['id'] == RELEASE and release['tag_name'] == TAG
    assert release['draft'] is True and release['prerelease'] is True
    names = [a['name'] for a in release['assets']]
    assert len(names) == len(set(names)) and set(names) <= set(NAMES)
    existing = {}
    for asset in release['assets']:
        path = paths[asset['name']]
        assert asset['state'] == 'uploaded', 'Do not replace an in-flight or partial asset'
        assert asset['size'] == path.stat().st_size
        assert asset['digest'] == 'sha256:' + file_sha(path), 'Do not clobber mismatched assets'
        existing[asset['name']] = asset
    return existing


def original_assets(archive, out):
    assert file_sha(archive) == OUTER_SHA, 'CI archive bytes changed'
    paths = {}
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        for name in NAMES:
            member = 'ci-build/' + name
            assert names.count(member) == 1, 'Missing or duplicated original CI asset'
            data = z.read(member)
            path = out / name
            path.write_bytes(data)
            paths[name] = path
        for name, expected in ZIP_SHA.items():
            assert file_sha(paths[name]) == expected
            assert paths[name + '.sha256'].read_text('ascii').strip().split() == [expected, name]
        acceptance = json.loads(paths[FULL + '.acceptance.json'].read_text('utf-8'))
        assert acceptance['head'] == HEAD and acceptance['archive'] == FULL
        assert acceptance['sha256'] == ZIP_SHA[FULL]
        with zipfile.ZipFile(paths[FULL]) as full, zipfile.ZipFile(paths[DELTA]) as delta:
            manifest = full.read('ArkDPS/metadata/release.json')
            assert delta.read('ArkDPS/metadata/release.json') == manifest
            identity = json.loads(manifest)
            assert identity['head'] == HEAD and identity['version'] == '1.0.0-rc.50'
    return paths


def main():
    assert os.environ['GITHUB_REPOSITORY'] == REPO
    assert os.environ['GITHUB_REF'] == 'refs/heads/codex/rc50-ci-asset-upload'
    assert os.environ['GITHUB_EVENT_NAME'] == 'workflow_dispatch'
    run = api('actions/runs/' + str(RUN))
    assert run['head_sha'] == HEAD and run['head_branch'] == 'main' and run['event'] == 'push'
    assert run['status'] == 'completed' and run['conclusion'] == 'success'
    assert run['path'].split('@')[0] == '.github/workflows/windows.yml'
    artifact = api('actions/artifacts/' + str(ARTIFACT))
    assert artifact['id'] == ARTIFACT and artifact['expired'] is False
    assert artifact['name'] == 'windows-wpf-candidate-' + HEAD
    assert artifact['workflow_run']['id'] == RUN and artifact['workflow_run']['head_sha'] == HEAD
    assert artifact['digest'] == 'sha256:' + OUTER_SHA
    verify_tag()
    out = Path('runtime/rc50-asset-transfer')
    out.mkdir(parents=True, exist_ok=False)
    archive = out / 'original-ci.zip'
    with archive.open('wb') as stream:
        subprocess.run(['gh', 'api', 'repos/' + REPO + '/actions/artifacts/' + str(ARTIFACT) + '/zip'],
                       stdout=stream, check=True)
    paths = original_assets(archive, out)
    # Validate every existing asset before the first upload. Never delete, replace,
    # create or publish a release, and never substitute freshly generated sidecars.
    existing = verify_draft(api('releases/' + str(RELEASE)), paths)
    for name in NAMES:
        if name not in existing:
            subprocess.run(['gh', 'release', 'upload', TAG, str(paths[name]), '--repo', REPO], check=True)
    release = api('releases/' + str(RELEASE))
    assert set(verify_draft(release, paths)) == set(NAMES)
    verify_tag()
    receipt = dict(result='PASS_DRAFT_ORIGINAL_ASSETS_UPLOADED', build_head=HEAD,
        build_main_run=RUN, original_artifact=ARTIFACT, outer_sha256=OUTER_SHA,
        upload_run=os.environ['GITHUB_RUN_ID'], upload_workflow_sha=os.environ['GITHUB_SHA'],
        tag=TAG, release_id=RELEASE, draft_still=True, published=False,
        assets=[dict(name=n, size=paths[n].stat().st_size, sha256=file_sha(paths[n])) for n in NAMES])
    (out / 'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
