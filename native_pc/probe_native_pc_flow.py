"""Optional raw GameFlow witness for the exact accepted Windows build.

Call within NativePcBattleProbe.sample's same-frame or guarded idle callback.
The caller supplies a build-checked module address in class_definition_table_slot,
bounds the outer reader and owns the process/battle guards. No method
invocation, process API or write is performed here. Unknown remains unknown;
scene transitions and singleton absence never become a battle outcome here.

Disk-only evidence, 2026-11-21:
GameAssembly.dll SHA-256
cf7956bf88a61986859ef55c026d231713e2d12cf69ffd9a19779ab491cf76ff;
global-metadata.dat SHA-256
e58fb854ae519bc22f7d8bc7ac83502425a51b896a907a3374f5215202f9caa5.
Metadata v29: 92-byte typedefs, strings/fields/typedef header0x18/0x60/0xA0;
DLL field-offset/type-pointer tables RVA0x87CE000/0x7F16AC0. Runtime class
definition table slot RVA0x8FD7890 is already checked by the outer probe.
GameFlowController typedef1327/count76 directly inherits instantiated
SingletonMonoBehaviour`1 (generic definition39058/count2), then MonoBehaviour.
s_instance field0 is static+0x0, VAR before generic inflation. Flow fields4/5
are m_isTransiting+0x20 BOOL and m_sceneBundle+0x28 VALUETYPE SceneBundle.
SceneBundle typedef1334/count3 is a struct: boxed fromScene/toScene/param
offsets0x10/0x18/0x20 become flow+0x28/+0x30/+0x38 after removing its boxed
header. Its two scene fields are STRING, param is CLASS. All checked roots,
metadata prefixes, reference/value bytes and strings are reread before return.
"""

import struct

from . import probe_native_pc_presence as presence


FLOW_TYPE_INDEX = 1327
BUNDLE_TYPE_INDEX = 1334
MAX_READ_CALLS = 256
MAX_READ_BYTES = 32 * 1024
MAX_SCENE_UNITS = 256
TYPE_BOOL = 0x02
TYPE_STRING = 0x0E
TYPE_VALUETYPE = 0x11
TYPE_CLASS = 0x12
TYPE_VAR = 0x13
FLOW_FIELDS = ((4, 'm_isTransiting', 0x20, (TYPE_BOOL,), False),
               (5, 'm_sceneBundle', 0x28, (TYPE_VALUETYPE,), False))
SINGLETON_FIELDS = ((0, 's_instance', 0, (TYPE_VAR, TYPE_CLASS), True),)
BUNDLE_FIELDS = ((0, 'fromScene', 0x10, (TYPE_STRING,), False),
                 (1, 'toScene', 0x18, (TYPE_STRING,), False),
                 (2, 'param', 0x20, (TYPE_CLASS,), False))


class FlowRead(presence.CheckedRead):
    def read(self, address, size):
        presence.require(self.calls < MAX_READ_CALLS and
                         self.bytes_requested + size <= MAX_READ_BYTES,
                         'game_flow_read_budget_exhausted')
        return super().read(address, size)

    def identity(self, klass):
        presence.pointer(klass)
        if klass not in self.known_classes:
            names = struct.unpack('<QQ', self.guarded(klass + 0x10, 16))
            parts = []
            for address in names:
                raw = self.guarded(address, 128)
                end = raw.find(b'\0')
                presence.require(end >= 0, 'game_flow_metadata_name_length')
                try:
                    parts.append(raw[:end].decode('ascii', errors='strict'))
                except UnicodeDecodeError:
                    raise presence.PresenceError('game_flow_metadata_name_encoding') from None
            self.known_classes[klass] = tuple(parts)
        return self.known_classes[klass]

    def require_identity(self, klass, name, namespace):
        presence.require(self.identity(klass) == (name, namespace), 'game_flow_class_identity')

    def fields(self, klass, expected_count, fields):
        count, = struct.unpack('<H', self.guarded(klass + presence.CLASS_FIELD_COUNT, 2))
        presence.require(count == expected_count, 'game_flow_field_count')
        table = self.ref(klass + presence.CLASS_FIELDS, guard=True)
        for index, name, offset, codes, is_static in fields:
            presence.require(0 <= index < count, 'game_flow_field_index')
            name_ref, type_ref, owner, actual, _ = struct.unpack(
                '<QQQiI', self.guarded(table + index * 32, 32))
            presence.require(owner == klass and actual == offset and
                             self.guarded(name_ref, len(name) + 1) == name.encode('ascii') + b'\0',
                             'game_flow_field_layout')
            presence.pointer(type_ref)
            _data, bits = struct.unpack('<QI', self.guarded(type_ref, 12))
            presence.require((bits >> 16) & 0xFF in codes and
                             bool(bits & 0x10) == is_static, 'game_flow_field_type')

    def scene(self, address):
        if not address:
            return ''
        presence.pointer(address)
        if address not in self.strings:
            klass = self.ref(address, guard=True)
            self.require_identity(klass, 'String', 'System')
            count, = struct.unpack('<i', self.guarded(address + 0x10, 4))
            presence.require(0 <= count <= MAX_SCENE_UNITS, 'game_flow_scene_length')
            raw = self.guarded(address + 0x14, count * 2) if count else b''
            try:
                value = raw.decode('utf-16-le', errors='strict')
            except UnicodeDecodeError:
                raise presence.PresenceError('game_flow_scene_encoding') from None
            presence.require(all(ord(char) >= 0x20 and ord(char) != 0x7F for char in value),
                             'game_flow_scene_control_character')
            self.strings[address] = value
        return self.strings[address]


def capture_game_flow(read, context):
    """Return raw scene scalars only; never label abandoned, victory or loss.

    Null scene strings are represented as empty strings. Missing/uninitialized
    class tables, singleton storage or concrete objects are unavailable, never
    evidence of a finished/absent battle. Generic class resolution is passive.
    """
    reader = FlowRead(read)
    try:
        presence.require(isinstance(context, dict) and 'class_definition_table_slot' in context,
                         'game_flow_class_table_slot_missing')
        slot = presence.pointer(context['class_definition_table_slot'])
        table = reader.ref(slot, nullable=True, guard=True)
        presence.require(table != 0, 'game_flow_class_table_uninitialized')
        klass = reader.ref(table + FLOW_TYPE_INDEX * 8, nullable=True, guard=True)
        bundle_class = reader.ref(table + BUNDLE_TYPE_INDEX * 8, nullable=True, guard=True)
        presence.require(klass != 0 and bundle_class != 0, 'game_flow_class_uninitialized')
        reader.require_identity(klass, 'GameFlowController', 'Torappu')
        reader.fields(klass, 76, FLOW_FIELDS)
        parent = reader.ref(klass + presence.CLASS_PARENT, guard=True)
        reader.require_identity(parent, 'SingletonMonoBehaviour`1', 'Torappu')
        reader.fields(parent, 2, SINGLETON_FIELDS)
        ancestor = reader.ref(parent + presence.CLASS_PARENT, guard=True)
        reader.require_identity(ancestor, 'MonoBehaviour', 'UnityEngine')
        reader.require_identity(bundle_class, 'SceneBundle', 'Torappu')
        reader.fields(bundle_class, 3, BUNDLE_FIELDS)
        bundle_parent = reader.ref(bundle_class + presence.CLASS_PARENT, guard=True)
        reader.require_identity(bundle_parent, 'ValueType', 'System')
        statics = reader.ref(parent + presence.native.CLASS_STATICS, nullable=True, guard=True)
        presence.require(statics != 0, 'game_flow_singleton_storage_uninitialized')
        flow = reader.ref(statics, nullable=True, guard=True)
        presence.require(flow != 0, 'game_flow_singleton_unavailable')
        presence.require(reader.ref(flow, guard=True) == klass, 'game_flow_concrete_class')
        native = reader.ref(flow + 0x10, nullable=True, guard=True)
        presence.require(native != 0, 'game_flow_native_missing')
        raw = reader.guarded(flow + 0x20, 0x20)
        transiting = raw[0]
        presence.require(transiting in (0, 1), 'game_flow_transiting_flag')
        source, target, param = struct.unpack_from('<QQQ', raw, 8)
        presence.pointer(param, nullable=True)  # Its contents are outside this witness.
        source_scene, target_scene = reader.scene(source), reader.scene(target)
        reader.validate()
    except presence.PresenceError as error:
        reason = str(error).replace('presence_', 'game_flow_', 1)
    except presence.native.ResearchFailure as error:
        reason = ('game_flow_read_budget_exhausted' if error.reason in
                  ('read_budget_exhausted', 'optional_read_budget_exhausted')
                  else 'game_flow_parent_reader_unavailable')
    except (presence.native.access.ApiFailure, OSError):
        reason = 'game_flow_read_failed'
    else:
        return {'game_flow_status': 'available', 'game_flow': {
            'transiting': bool(transiting), 'source_scene': source_scene,
            'target_scene': target_scene,
        }}
    return {'game_flow_status': 'unavailable', 'game_flow_reason': reason, 'game_flow': None}
