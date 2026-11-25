"""Read selected lifecycle/skill signatures from a v29 disk metadata file only.

No process handle, DLL loading, runtime getter or game connection is used.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def inspect(path):
    raw = Path(path).read_bytes()
    header = struct.unpack_from('<80I', raw)
    if header[:2] != (0xFAB11BAF, 29) or header[41] % 92 or header[13] % 32:
        raise ValueError('unsupported metadata schema')
    for offset, size in (header[6:8], header[12:14], header[16:18], header[18:20],
                         header[22:24], header[24:26], header[40:42]):
        if offset + size > len(raw):
            raise ValueError('truncated metadata table')
    def text(index):
        if not 0 <= index < header[7]:
            raise ValueError('invalid string index')
        start = header[6] + index
        return raw[start:raw.index(0, start, header[6] + header[7])].decode('utf-8')
    definitions = [struct.unpack_from('<17i8H2I', raw, header[40] + index * 92)
                   for index in range(header[41] // 92)]
    byval = {row[2]: index for index, row in enumerate(definitions)}
    def name(index):
        row = definitions[index]
        if row[3] in byval:
            return name(byval[row[3]]) + '.' + text(row[0])
        return '.'.join(part for part in (text(row[1]), text(row[0])) if part)
    def typename(index):
        return name(byval[index]) if index in byval else 'type-index:' + str(index)
    selected = {'Torappu.Battle.' + item for item in ('BObject', 'Character', 'BasicSkill',
        'BattleController', 'Entity.FinishReason', 'Ability.FinishReason')}
    defaults = {struct.unpack_from('<3i', raw, header[16] + index)[0]:
                struct.unpack_from('<3i', raw, header[16] + index)[1:]
                for index in range(0, header[17], 12)}
    def default_int(offset):
        first = raw[header[18] + offset]
        # All selected real enum values fit the v29 signed compressed byte.
        if first >= 0x80:
            raise ValueError('unsupported selected enum default')
        return -(first // 2) - 1 if first & 1 else first // 2
    result = dict(metadata_version=29, metadata_sha256=hashlib.sha256(raw).hexdigest(),
                  metadata_bytes=len(raw), source='Disk metadata only; field offsets remain runtime-verified', types={})
    wanted = {'Born', 'OnCharacterFinished', 'OnCastFinish', 'OnAfterSkill', 'OnSkillStart',
              'SwitchToSkillState', 'OnSkillCastSucceed', 'OnSkillFinish', 'SwitchOutFromSkillState'}
    for index, row in enumerate(definitions):
        full = name(index)
        if full not in selected:
            continue
        item = dict(type_definition_index=index, byval_type_index=row[2], declaring_type=typename(row[3]),
                    methods=[], enum_literals={})
        for method_index in range(row[9], row[9] + row[17]):
            method = struct.unpack_from('<6i4H', raw, header[12] + method_index * 32)
            if text(method[0]) not in wanted:
                continue
            parameters = []
            for parameter_index in range(method[3], method[3] + method[-1]):
                parameter = struct.unpack_from('<3i', raw, header[22] + parameter_index * 12)
                parameters.append(dict(name=text(parameter[0]), type=typename(parameter[2]), type_index=parameter[2]))
            item['methods'].append(dict(method_definition_index=method_index, name=text(method[0]),
                return_type=typename(method[2]), parameters=parameters, flags=method[6], static=bool(method[6] & 0x10)))
        if full.endswith('.FinishReason'):
            for field in range(row[8], row[8] + row[19]):
                definition = struct.unpack_from('<3i', raw, header[24] + field * 12)
                if field in defaults:
                    item['enum_literals'][text(definition[0])] = default_int(defaults[field][1])
        result['types'][full] = item
    if result['types'].keys() != selected:
        raise ValueError('missing selected metadata types')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('metadata', type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect(args.metadata), indent=2))
