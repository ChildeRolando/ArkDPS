"""Read the battle roster and live unit membership from the game process.

Statistics need stable source keys and summon attribution. The client keeps a
per-player roster of ``BattleCharacterData`` entries (characters and tokens)
plus the live unit list. Tokens carry ``tokenOrHostKey``/``tokenOrHostUniqueId``
so they can be attributed to the host that created them. Two entities can
share one token data entry, therefore attribution is per statistic key, never
per live entity.

Offsets below were verified on Android 2.7.91 (MuMu 12) against IL2CPP field
metadata and live memory. The battle-controller layout is re-checked before
use, so a drifted layout fails closed instead of guessing owners. Reads are
grouped in a frame-guarded device-side transaction; a frame change rejects the
whole roster snapshot.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

# Verified 2026-11-08, Android 2.7.91 / MuMu 12.
BATTLE_CONTROLLER_PLAYER_DATA_LIST = 0x170
BATTLE_CONTROLLER_UNIT_MANAGER = 0x2C0
BATTLE_PLAYER_DATA_SIDE = 0x10
BATTLE_PLAYER_DATA_CHARACTERS = 0x18
BATTLE_PLAYER_DATA_TOKENS = 0x20
BATTLE_CHARACTER_PREFAB_KEY = 0x50
BATTLE_CHARACTER_UNIQUE_ID = 0x6C
BATTLE_CHARACTER_IS_TOKEN = 0x90
BATTLE_CHARACTER_IS_PREDEFINED = 0x91
BATTLE_CHARACTER_HOST_KEY = 0x98
BATTLE_CHARACTER_HOST_UID = 0xA0
BATTLE_CHARACTER_READ_SIZE = 0xB0
CHARACTER_DATA = 0x538
UNIT_MANAGER_CHARACTERS = 0x18
LIST_POINTER = 0x10
LIST_COUNT = 0x18
UNORDERED_COUNT = 0x20
IL2CPP_ARRAY_LENGTH = 0x18
IL2CPP_ARRAY_ITEMS = 0x20
IL2CPP_CLASS_FIELDS = 0x80
IL2CPP_CLASS_FIELD_COUNT = 0x124
IL2CPP_STRING_LENGTH = 0x10
IL2CPP_STRING_CHARS = 0x14
MAX_STRING_CHARS = 110
CLOCK_FRAME_OFFSET = 0x14

MAX_PLAYERS = 4
MAX_CHARACTERS = 64
MAX_TOKENS = 64
MAX_LIVE_UNITS = 128

_ID = re.compile(r'[A-Za-z0-9_#.-]+')


class RosterUnavailable(RuntimeError):
    """The roster could not be read completely or its layout is unverified."""


@dataclass(frozen=True)
class CharacterEntry:
    cid: str
    unique_id: int
    is_token: bool
    is_predefined: bool
    host_key: str | None
    host_uid: int | None
    player_side: int


@dataclass(frozen=True)
class RosterSnapshot:
    frame: int
    entries: tuple[CharacterEntry, ...] = ()
    present_unique_ids: frozenset[int] = frozenset()
    present_known: bool = False

    @property
    def tokens(self) -> tuple[CharacterEntry, ...]:
        return tuple(e for e in self.entries if e.is_token)


@dataclass
class _Ops:
    operations: list[tuple] = field(default_factory=list)

    def direct(self, addr: int, size: int) -> int:
        self.operations.append(('direct', int(addr), int(size)))
        return len(self.operations) - 1

    def deref(self, ref: int, offset: int, addend: int, size: int) -> int:
        self.operations.append(('deref', int(ref), int(offset), int(addend), int(size)))
        return len(self.operations) - 1

    def list_items(self, ref: int, pointer_offset: int = LIST_POINTER,
                   count_offset: int = LIST_COUNT, bound: int = 8) -> int:
        self.operations.append(('list_items', int(ref), int(pointer_offset),
                                int(count_offset), int(bound)))
        return len(self.operations) - 1


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from('<H', data, offset)[0]


def _i32(data: bytes, offset: int) -> int:
    return struct.unpack_from('<i', data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from('<I', data, offset)[0]


def _u64(data: bytes, offset: int = 0) -> int:
    return struct.unpack_from('<Q', data, offset)[0]


def decode_string(raw: bytes | None) -> str | None:
    """Decode an Il2CppString snapshot, rejecting impossible lengths."""
    if not raw or len(raw) < IL2CPP_STRING_CHARS:
        return None
    length = _i32(raw, IL2CPP_STRING_LENGTH)
    if not 1 <= length <= MAX_STRING_CHARS:
        return None
    end = IL2CPP_STRING_CHARS + length * 2
    if len(raw) < end:
        return None
    try:
        value = raw[IL2CPP_STRING_CHARS:end].decode('utf-16-le')
    except UnicodeDecodeError:
        return None
    return value if _ID.fullmatch(value) else None


def class_fields(mc, klass: int) -> dict[str, int]:
    """Return verified instance-field offsets declared directly on klass."""
    if not klass:
        return {}
    fields_ptr = mc.read_ptr(klass + IL2CPP_CLASS_FIELDS)
    raw_count = mc.read(klass + IL2CPP_CLASS_FIELD_COUNT, 2)
    if not fields_ptr or not raw_count:
        return {}
    count = _u16(raw_count, 0)
    if not 1 <= count <= 2000:
        return {}
    raw = mc.read(fields_ptr, count * 32)
    if not raw or len(raw) < count * 32:
        return {}
    out: dict[str, int] = {}
    for index in range(count):
        base = index * 32
        name_ptr = _u64(raw, base)
        parent = _u64(raw, base + 16)
        offset = struct.unpack_from('<i', raw, base + 24)[0]
        if parent != klass or not name_ptr:
            continue
        name = mc.read_cstring(name_ptr, 128)
        if name and not name.startswith('__Hotfix') and offset >= 0:
            out[name] = offset
    return out


def check_battle_layout(mc, bc_addr: int) -> None:
    """Fail closed when the battle controller layout is not the verified one."""
    klass = mc.read_ptr(bc_addr)
    fields = class_fields(mc, klass)
    expected = {'m_playerDataList': BATTLE_CONTROLLER_PLAYER_DATA_LIST,
                '<unitManager>k__BackingField': BATTLE_CONTROLLER_UNIT_MANAGER}
    for name, offset in expected.items():
        if fields.get(name) != offset:
            actual = fields.get(name)
            raise RosterUnavailable(
                f'BattleController.{name} 偏移不匹配（期望 {offset:#x}，实际 '
                f'{actual if actual is None else hex(actual)}）；拒绝读取归属')


def build_operations(bc_addr: int) -> tuple[list[tuple], dict]:
    """Build the roster transaction and the indices needed to parse it."""
    ops = _Ops()
    bc = ops.direct(bc_addr, BATTLE_CONTROLLER_UNIT_MANAGER + 0x30)
    player_list = ops.deref(bc, BATTLE_CONTROLLER_PLAYER_DATA_LIST, 0, 0x20)
    player_items = ops.list_items(player_list, bound=MAX_PLAYERS * 8)
    players = []
    for player_index in range(MAX_PLAYERS):
        player = ops.deref(player_items, player_index * 8, 0, 0x28)
        # characters/tokens are Il2Cpp arrays (not List<T>): max_length at
        # +0x18 and the element slots at +0x20.
        char_count = ops.deref(player, BATTLE_PLAYER_DATA_CHARACTERS,
                               IL2CPP_ARRAY_LENGTH, 4)
        char_items = ops.deref(player, BATTLE_PLAYER_DATA_CHARACTERS,
                               IL2CPP_ARRAY_ITEMS, MAX_CHARACTERS * 8)
        token_count = ops.deref(player, BATTLE_PLAYER_DATA_TOKENS,
                                IL2CPP_ARRAY_LENGTH, 4)
        token_items = ops.deref(player, BATTLE_PLAYER_DATA_TOKENS,
                                IL2CPP_ARRAY_ITEMS, MAX_TOKENS * 8)
        chars = []
        for index in range(MAX_CHARACTERS):
            data = ops.deref(char_items, index * 8, 0, BATTLE_CHARACTER_READ_SIZE)
            key = ops.deref(data, BATTLE_CHARACTER_PREFAB_KEY, 0,
                            IL2CPP_STRING_CHARS + MAX_STRING_CHARS * 2)
            host = ops.deref(data, BATTLE_CHARACTER_HOST_KEY, 0,
                             IL2CPP_STRING_CHARS + MAX_STRING_CHARS * 2)
            chars.append((data, key, host))
        tokens = []
        for index in range(MAX_TOKENS):
            data = ops.deref(token_items, index * 8, 0, BATTLE_CHARACTER_READ_SIZE)
            key = ops.deref(data, BATTLE_CHARACTER_PREFAB_KEY, 0,
                            IL2CPP_STRING_CHARS + MAX_STRING_CHARS * 2)
            host = ops.deref(data, BATTLE_CHARACTER_HOST_KEY, 0,
                             IL2CPP_STRING_CHARS + MAX_STRING_CHARS * 2)
            tokens.append((data, key, host))
        players.append({'player': player, 'characters': chars, 'tokens': tokens,
                        'char_count': char_count, 'token_count': token_count})
    unit_manager = ops.deref(bc, BATTLE_CONTROLLER_UNIT_MANAGER, 0, 0x30)
    # UnitManager.characters is an UnorderedArray: items +0x10, count +0x20.
    unit_list = ops.deref(unit_manager, UNIT_MANAGER_CHARACTERS, 0, 0x28)
    live_as_list = ops.list_items(unit_list, count_offset=LIST_COUNT,
                                  bound=MAX_LIVE_UNITS * 8)
    live_as_unordered = ops.list_items(unit_list, count_offset=UNORDERED_COUNT,
                                       bound=MAX_LIVE_UNITS * 8)
    live = []
    for items_index in (live_as_list, live_as_unordered):
        slots = []
        for index in range(MAX_LIVE_UNITS):
            data_ptr = ops.deref(items_index, index * 8, CHARACTER_DATA, 8)
            data = ops.deref(data_ptr, 0, 0, BATTLE_CHARACTER_READ_SIZE)
            slots.append((data, data_ptr))
        live.append({'items': items_index, 'slots': slots})
    return ops.operations, {'players': players, 'live': live}


def _slots(result: bytes | None) -> int:
    return len(result) // 8 if result else 0


def _entry(data: bytes, key_raw: bytes | None, host_raw: bytes | None,
           player_side: int) -> CharacterEntry | None:
    if not data or len(data) < BATTLE_CHARACTER_READ_SIZE:
        return None
    cid = decode_string(key_raw)
    if not cid:
        return None
    is_token = bool(data[BATTLE_CHARACTER_IS_TOKEN])
    host_key = decode_string(host_raw) if is_token else None
    host_uid = _u32(data, BATTLE_CHARACTER_HOST_UID) if is_token else None
    return CharacterEntry(cid=cid, unique_id=_u32(data, BATTLE_CHARACTER_UNIQUE_ID),
                          is_token=is_token,
                          is_predefined=bool(data[BATTLE_CHARACTER_IS_PREDEFINED]),
                          host_key=host_key, host_uid=host_uid,
                          player_side=player_side)


def parse_results(results: list[bytes | None], layout: dict,
                  frame: int) -> RosterSnapshot:
    """Parse transaction results; unknown or garbled entries are omitted."""
    entries: list[CharacterEntry] = []
    for player in layout['players']:
        player_raw = results[player['player']]
        if not player_raw:
            continue
        side = _i32(player_raw, BATTLE_PLAYER_DATA_SIDE)
        for group, count_index in (('characters', 'char_count'),
                                   ('tokens', 'token_count')):
            count_raw = results[player[count_index]]
            count = _i32(count_raw, 0) if count_raw and len(count_raw) >= 4 else 0
            count = min(max(count, 0), len(player[group]))
            for data_index, key_index, host_index in player[group][:count]:
                data = results[data_index]
                if not data:
                    continue
                entry = _entry(data, results[key_index], results[host_index], side)
                if entry:
                    entries.append(entry)
    present: set[int] = set()
    present_known = False
    # Prefer the actual UnorderedArray layout; fall back to List<T> when it
    # is unavailable. Never treat an unread occupied slot as absent.
    for group in reversed(layout['live']):
        items = results[group['items']]
        if items is None:
            continue
        present_known = len(items) % 8 == 0 and _slots(items) <= len(group['slots'])
        for index in range(min(_slots(items), len(group['slots']))):
            if not _u64(items, index * 8):
                continue
            data = results[group['slots'][index][0]]
            if not data or len(data) < BATTLE_CHARACTER_READ_SIZE:
                present_known = False
                continue
            present.add(_u32(data, BATTLE_CHARACTER_UNIQUE_ID))
        break
    return RosterSnapshot(frame=frame, entries=tuple(entries),
                          present_unique_ids=frozenset(present),
                          present_known=present_known)


class RosterReader:
    """Frame-guarded roster reader bound to one battle controller."""

    def __init__(self, mc, bc_addr: int, static_fields: int, status=None):
        self.mc = mc
        self.bc_addr = bc_addr
        self.static_fields = static_fields
        self.status = status
        self.verified = False

    def verify(self) -> None:
        check_battle_layout(self.mc, self.bc_addr)
        self.verified = True

    def read(self, max_attempts: int = 8) -> RosterSnapshot:
        if not self.verified:
            self.verify()
        operations, layout = build_operations(self.bc_addr)
        guard_addr = self.static_fields + CLOCK_FRAME_OFFSET
        values, guard = self.mc.channel().guarded_transaction_read(
            operations, guard_addr, guard_size=4, max_attempts=max_attempts)
        if not guard.get('complete'):
            raise RosterUnavailable('归属读取跨帧，拒绝本次快照')
        # Null slots are expected for unused list entries; the parser skips
        # them. A frame change rejects the entire snapshot above.
        return parse_results(values, layout, int(guard['start']))
