"""Read candidate identity in a portable build without depending on installed Git."""
import hashlib
import json
from pathlib import PurePosixPath
import re
import subprocess


def load_manifest(root, *, verify=False):
    data = json.loads((root / 'build-manifest.json').read_text(encoding='utf-8'))
    if (data.get('schema_version') != 1 or
            not isinstance(data.get('head'), str) or
            not re.fullmatch(r'[a-f0-9]{40}', data['head']) or
            not isinstance(data.get('files'), dict) or not data['files']):
        raise ValueError('invalid build-manifest.json')
    for name, digest in data['files'].items():
        if not isinstance(name, str):
            raise ValueError('invalid manifest file entry')
        path = PurePosixPath(name)
        if ('\\' in name or ':' in name or path.is_absolute() or '..' in path.parts or
                not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest)):
            raise ValueError('invalid manifest file entry')
        if verify and hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f'bundle checksum mismatch: {name}')
    return data


def candidate_head(root, expected=None):
    """Resolve the candidate commit and refuse dirty or mismatched checkouts.

    Portability matters here: a frozen bundle reads build-manifest.json instead,
    but every build path must still prove it packages exactly one committed HEAD.
    """
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    if expected and head != expected:
        raise RuntimeError(f'candidate mismatch: expected {expected}, got {head}')
    dirty = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=normal'],
                                    cwd=root, text=True).strip()
    if dirty:
        raise RuntimeError('release builds require a clean committed worktree')
    return head
