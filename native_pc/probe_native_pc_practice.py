"""Optional current SquadPage selection; never binds a subsequent battle.

Exact accepted PC DLL/metadata only. The caller owns PID/birth/build and outer
watermark guards. This callback invokes no methods and opens/writes nothing.
Only the last UI page-stack entry is considered; cached pages are not searched.
"""

import struct

from . import probe_native_pc_flow as flow

presence = flow.presence
CONTROLLER_TYPE_INDEX = 16863
STACK_ELEMENT_TYPE_INDEX = 16842
PAGE_TYPE_INDEX = 16892
SQUAD_TYPE_INDEX = 18477
PARAMS_TYPE_INDEX = 18476
STAGE_TYPE_INDEX = 9929
MAX_STACK = 64
MAX_CAPACITY = 4096
MAX_READ_CALLS = 512
MAX_READ_BYTES = 64 * 1024
TYPE_I4, TYPE_OBJECT, TYPE_ARRAY, TYPE_GENERIC = 0x08, 0x1C, 0x1D, 0x15
CONTROLLER_FIELDS = ((10, 'm_pageStack', 0x68, (TYPE_GENERIC,), False),)
STACK_FIELDS = ((1, 'pageInst', 0x18, (flow.TYPE_CLASS,), False),)
LIST_FIELDS = ((1, '_items', 0x10, (TYPE_ARRAY,), False),
               (2, '_size', 0x18, (TYPE_I4,), False),
               (3, '_version', 0x1C, (TYPE_I4,), False))
PAGE_FIELDS = ((2, 'm_pageState', 0x28, (flow.TYPE_VALUETYPE,), False),
               (3, 'm_args', 0x30, (TYPE_OBJECT,), False))
PARAMS_FIELDS = ((1, 'isPractice', 0x28, (flow.TYPE_BOOL,), False),
                 (10, 'battleStageInfo', 0x48, (flow.TYPE_VALUETYPE,), False))
STAGE_FIELDS = ((1, 'stageId', 0x10, (flow.TYPE_STRING,), False),)


class PracticeRead(flow.FlowRead):
    def read(self, address, size):
        presence.require(self.calls < MAX_READ_CALLS and
                         self.bytes_requested + size <= MAX_READ_BYTES,
                         'practice_selection_read_budget_exhausted')
        return presence.CheckedRead.read(self, address, size)


def capture_practice_selection(read, context):
    """Return a raw stage/practice choice from the current stack top only."""
    reader = PracticeRead(read)
    try:
        presence.require(isinstance(context, dict) and 'class_definition_table_slot' in context,
                         'practice_selection_class_table_slot_missing')
        table = reader.ref(presence.pointer(context['class_definition_table_slot']), nullable=True, guard=True)
        presence.require(table != 0, 'practice_selection_class_table_uninitialized')
        classes = {}
        for key, index, name, namespace, count, fields in (
            ('controller', CONTROLLER_TYPE_INDEX, 'UIPageController', 'Torappu.UI', 108, CONTROLLER_FIELDS),
            ('stack', STACK_ELEMENT_TYPE_INDEX, 'StackElmt', '', 3, STACK_FIELDS),
            ('page', PAGE_TYPE_INDEX, 'UIPage', 'Torappu.UI', 93, PAGE_FIELDS),
            ('squad', SQUAD_TYPE_INDEX, 'SquadPage', 'Torappu.UI', 8, ()),
            ('params', PARAMS_TYPE_INDEX, 'Params', '', 16, PARAMS_FIELDS),
            ('stage', STAGE_TYPE_INDEX, 'BattleStageInfo', 'Torappu.Battle', 21, STAGE_FIELDS),
        ):
            klass = reader.ref(table + index * 8, nullable=True, guard=True)
            presence.require(klass != 0, 'practice_selection_class_uninitialized')
            reader.require_identity(klass, name, namespace)
            reader.fields(klass, count, fields)
            classes[key] = klass
        parent = reader.ref(classes['controller'] + presence.CLASS_PARENT, guard=True)
        reader.require_identity(parent, 'SingletonMonoBehaviour`1', 'Torappu')
        reader.fields(parent, 2, flow.SINGLETON_FIELDS)
        reader.require_identity(reader.ref(parent + presence.CLASS_PARENT, guard=True), 'MonoBehaviour', 'UnityEngine')
        state_page = reader.ref(classes['squad'] + presence.CLASS_PARENT, guard=True)
        reader.require_identity(state_page, 'StateEnginePage', 'Torappu.UI')
        presence.require(reader.ref(state_page + presence.CLASS_PARENT, guard=True) == classes['page'],
                         'practice_selection_page_inheritance')
        presence.require(reader.ref(classes['params'] + 0x50, guard=True) == classes['squad'],
                         'practice_selection_params_declaring_type')
        for key in ('stack', 'params', 'stage'):
            reader.require_identity(reader.ref(classes[key] + presence.CLASS_PARENT, guard=True), 'ValueType', 'System')
        statics = reader.ref(parent + presence.native.CLASS_STATICS, nullable=True, guard=True)
        presence.require(statics != 0, 'practice_selection_singleton_storage_uninitialized')
        controller = reader.ref(statics, nullable=True, guard=True)
        presence.require(controller != 0, 'practice_selection_singleton_unavailable')
        presence.require(reader.ref(controller, guard=True) == classes['controller'], 'practice_selection_controller_type')
        presence.require(reader.ref(controller + 0x10, nullable=True, guard=True) != 0,
                         'practice_selection_native_missing')
        stack = reader.ref(controller + 0x68, nullable=True, guard=True)
        presence.require(stack != 0, 'practice_selection_stack_unavailable')
        list_class = reader.ref(stack, guard=True)
        reader.require_identity(list_class, 'List`1', 'System.Collections.Generic')
        reader.fields(list_class, 6, LIST_FIELDS)
        array, count, _version = struct.unpack('<Qii', reader.guarded(stack + 0x10, 16))
        presence.require(0 < count <= MAX_STACK, 'practice_selection_stack_count')
        presence.pointer(array)
        array_class = reader.ref(array, guard=True)
        reader.require_identity(array_class, 'StackElmt[]', '')
        capacity, = struct.unpack('<Q', reader.guarded(array + 0x18, 8))
        presence.require(count <= capacity <= MAX_CAPACITY, 'practice_selection_stack_capacity')
        # StackElmt boxed size48 -> inline stride32; pageInst boxed+18 -> inline+8.
        page = reader.ref(array + 0x20 + (count - 1) * 32 + 8, nullable=True, guard=True)
        presence.require(page != 0, 'practice_selection_top_page_unavailable')
        presence.require(reader.ref(page, guard=True) == classes['squad'], 'practice_selection_top_not_squad')
        presence.require(reader.ref(page + 0x10, nullable=True, guard=True) != 0,
                         'practice_selection_page_native_missing')
        reader.guarded(page + 0x28, 4)  # Raw page-state watermark, no enum inference.
        args = reader.ref(page + 0x30, nullable=True, guard=True)
        presence.require(args != 0, 'practice_selection_params_unavailable')
        presence.require(reader.ref(args, guard=True) == classes['params'], 'practice_selection_params_type')
        practice, = struct.unpack('<B', reader.guarded(args + 0x28, 1))
        presence.require(practice in (0, 1), 'practice_selection_boolean')
        stage = reader.ref(args + 0x48, nullable=True, guard=True)
        presence.require(stage != 0, 'practice_selection_stage_missing')
        stage_id = reader.scene(stage)
        presence.require(stage_id and stage_id.strip() == stage_id,
                         'practice_selection_stage_id')
        reader.validate()
    except presence.PresenceError as error:
        reason = str(error).replace('game_flow_', 'practice_selection_', 1).replace('presence_', 'practice_selection_', 1)
    except presence.native.ResearchFailure as error:
        reason = ('practice_selection_read_budget_exhausted' if error.reason in
                  ('read_budget_exhausted', 'optional_read_budget_exhausted')
                  else 'practice_selection_parent_reader_unavailable')
    except (presence.native.access.ApiFailure, OSError):
        reason = 'practice_selection_read_failed'
    else:
        return {'practice_selection_status': 'available',
                'practice_selection': {'stage_id': stage_id, 'practice': bool(practice)}}
    return {'practice_selection_status': 'unavailable', 'practice_selection_reason': reason,
            'practice_selection': None}
