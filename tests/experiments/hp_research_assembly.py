"""Explicit research source assembly for the standalone QuickJS reference host."""
import json
from pathlib import Path


def assemble_research_runtime(runtime):
    patches = json.loads(Path(__file__).with_name('hp_research_patches.json').read_text(encoding='utf8'))
    for patch in patches:
        target = patch['marker'] + '\n' + patch['product']
        if runtime.count(target) != 1:
            raise ValueError('HP research assembly point changed: ' + patch['marker'])
        inserted = (Path(__file__).with_name(patch['research_file']).read_text(encoding='utf8')
                    if 'research_file' in patch else patch['research'])
        runtime = runtime.replace(target, inserted)
    return runtime
