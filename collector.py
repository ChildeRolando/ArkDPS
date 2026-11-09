"""Read-only Android collector. Upstream discovery is pinned in vendor/.

Only cumulative character damage and the game's fixed-step clock are published.
All data for one sample is read in a device-side frame-guarded transaction.
"""
from __future__ import annotations
import json
import math
import re
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / 'vendor' / 'ark-source'
sys.path.insert(0, str(VENDOR))
from tools.enemy_health import game_structs as gs
from tools.enemy_health.enemy_reader import EnemyReader
from tools.enemy_health.memcore import TcpChannel
from tools.stage_global_state.memory import ReadFailure
from tools.deploy_tracker.ak_deploy_reader import DeployTrackerReader, LOGGER_CONTROLLER
from character_catalog import load_character_names
from connection import (ConnectionDiagnostics, DetectionStatus, VersionStatus,
                        EXPECTED_GAME_VERSION, ForwardRegistry)
from roster import RosterReader, RosterSnapshot, RosterUnavailable

DEFAULT_ADB = r'C:\Program Files\Netease\MuMu Player 12\nx_main\adb.exe'
DEFAULT_SERIAL = '127.0.0.1:16384'
PORT = 27381
# Unity native object's managed back-reference, verified against the current
# Android 2.7.91 runtime. connect() gates versions before using this layout.
NATIVE_MANAGED_REFERENCE = 0x28


class SampleUnavailable(RuntimeError):
    pass


class BattleChanged(SampleUnavailable):
    pass


class ProcessChanged(BattleChanged):
    """A successful lookup confirmed process absence or a different identity."""


class UnsupportedVersion(SampleUnavailable):
    """The transport works, but statistics for this game version are unverified.

    The diagnostic attached here deliberately reports connection_ok=True and
    version_status=incompatible so the GUI can distinguish "not connected"
    from "connected but unsupported".
    """

    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics


def check_battle_ownership(bc, native, managed_reference, logger_owner):
    if not native or managed_reference != bc or logger_owner != bc:
        raise BattleChanged('战局归属已变化；保留最后有效记录并重新定位')


def u64(data, offset=0):
    return struct.unpack_from('<Q', data, offset)[0]


def i32(data, offset=0):
    return struct.unpack_from('<i', data, offset)[0]


def finite(value):
    if not math.isfinite(value) or abs(value) > 1e15:
        raise SampleUnavailable('伤害字段无效，请重新定位')
    return value


def decode_character(key, summary, type_head, types, names):
    length = i32(key, 0x10)
    if not 1 <= length <= 110:
        raise SampleUnavailable('干员标识长度无效')
    cid = key[0x14:0x14 + length * 2].decode('utf-16-le', errors='strict')
    if not re.fullmatch(r'[A-Za-z0-9_#.-]+', cid):
        raise SampleUnavailable('干员标识无效')
    if i32(type_head, gs.ListInternal.SIZE) != gs.DamageType.E_NUM:
        raise SampleUnavailable('伤害分类结构不匹配，可能需要适配游戏版本')
    values = [finite(x) for x in struct.unpack('<6f', types)]
    if any(x < -0.01 for x in values):
        raise SampleUnavailable('分类伤害出现负值，保留未知状态')
    damage = sum(values[n] for n in (1, 2, 3, 5))
    raw = finite(struct.unpack_from('<f', summary, gs.CharAdvancedStatsFields.OUTPUT_DAMAGE_TOTAL)[0])
    return dict(id=cid, name=names.get(cid, cid), damage=damage,
                physical=values[1], arts=values[2], true=values[3],
                elemental=values[5], healing=values[4], raw_output=raw)


class Collector:
    def __init__(self, adb=DEFAULT_ADB, serial=DEFAULT_SERIAL, log=print):
        self.log = log
        self.core = EnemyReader(adb_path=adb, adb_serial=serial,
            cache_file=str(ROOT / 'runtime' / 'unused.pkl'), log=log)
        self.forward_registry = ForwardRegistry()
        self.core.mc.forward_registry = self.forward_registry
        # Dedicated port: do not share the upstream app's stream.
        self.names = load_character_names()
        self.last_pid_check = 0.0
        self.cache_path = ROOT / 'runtime' / 'address_hint.json'
        self._roster_reader = None
        self._lifecycle_reader = None
        self._lifecycle_maps_refresh_at = float('-inf')
        self.adb_path = adb
        self.serial = serial
        self.diagnostics = ConnectionDiagnostics(
            status=DetectionStatus.NO_INSTANCE, adb_path=adb,
            selected_serial=serial, version_expected=EXPECTED_GAME_VERSION)

    def _valid_battle(self, bc):
        mc=self.core.mc
        if not mc.is_ptr(bc) or mc.read_klass_name(bc) != 'BattleController':
            return False
        native=mc.read_ptr(bc + gs.BattleControllerFields.UNITY_CACHED_PTR)
        logger=mc.read_ptr(bc + gs.BattleControllerFields.M_LOGGER)
        if not native or not logger or not mc.is_ptr(native) or not mc.is_ptr(logger):
            return False
        try:
            check_battle_ownership(bc,native,mc.read_ptr(native+NATIVE_MANAGED_REFERENCE),
                                   mc.read_ptr(logger+LOGGER_CONTROLLER))
        except BattleChanged:
            return False
        return mc.read_klass_name(logger) == 'BattleLogger'

    def _locate_active_battle(self):
        loc=DeployTrackerReader(self.core.mc)
        loc._channel=self.core._chan
        loc.set_status_callback(self.log)
        objects=loc._scan_class_objects(('BattleController',))
        candidates=loc._battle_controller_candidates(objects.get('BattleController',()))
        valid=[bc for bc in sorted(candidates) if self._valid_battle(bc)]
        if not valid:
            raise SampleUnavailable('未找到当前战斗。进入关卡后点击“重新定位”')
        if len(valid)!=1:
            raise SampleUnavailable('发现多个有效战局，无法确认当前局；停止发布数据')
        self.core.bc_addr=valid[0]

    def connect(self, force=False, *, locate=True, lifecycle=False):
        self.log('正在连接 MuMu…')
        self._lifecycle_reader = None
        self.core.bc_addr = 0
        # MemCore owns its channel; its creation uses the class port default.
        TcpChannel.PORT = PORT
        self.core.connect()
        if not hasattr(self, 'diagnostics'):
            self.diagnostics = ConnectionDiagnostics()
        self.adb_path = getattr(self, 'adb_path', None)
        self.serial = getattr(self, 'serial', None)
        self.diagnostics.adb_path = self.adb_path
        self.diagnostics.selected_serial = self.serial
        if not self.core.mc.process_instance:
            self.diagnostics.connection_ok = False
            raise SampleUnavailable('无法验证游戏进程身份，请重试连接')
        self.diagnostics.connection_ok = True
        self.diagnostics.status = DetectionStatus.READY_UNIQUE
        package_info = self.core.mc.shell(f'dumpsys package {self.core.mc.package}', timeout=10)
        version = re.search(r'versionName=([^\s]+)', package_info)
        expected = EXPECTED_GAME_VERSION or json.loads(
            (VENDOR / 'game_data/manifest.json').read_text(encoding='utf-8'))['android_game_version']
        if not version:
            self.diagnostics.version_status = VersionStatus.UNKNOWN
            self.diagnostics.message = '已连接，但无法解析游戏版本；拒绝读取统计'
            raise UnsupportedVersion(self.diagnostics.message, self.diagnostics)
        actual = version.group(1)
        self.diagnostics.version_actual = actual
        if actual != expected:
            self.diagnostics.version_status = VersionStatus.INCOMPATIBLE
            self.diagnostics.message = (
                f'当前仅验证 Android {expected}，检测到 {actual}；需更新适配后再读取')
            raise UnsupportedVersion(
                f'当前仅验证 Android {expected}，检测到 {actual}；需更新适配后再读取',
                self.diagnostics)
        self.diagnostics.version_status = VersionStatus.COMPATIBLE
        self.core._chan = self.core.mc.channel()
        self.last_pid_check = time.monotonic()
        if lifecycle:
            self.locate_lifecycle()
        elif locate:
            self.locate_battle(force=force)

    def locate_lifecycle(self):
        """Locate live singleton slots after process/version certification."""
        if self.diagnostics.version_status != VersionStatus.COMPATIBLE:
            raise UnsupportedVersion('版本未通过校验，拒绝定位战局', self.diagnostics)
        from tools.enemy_health.battle_lifecycle import locate_lifecycle
        locator = DeployTrackerReader(self.core.mc)
        try:
            self._lifecycle_reader = locate_lifecycle(self.core.mc, None, locator)
            self._lifecycle_reader.m.channel = self.core._chan
        finally:
            locator.close()

    def locate_battle(self, force=False):
        """Locate after a version-checked connection, allowing pre-battle hooks."""
        if self.diagnostics.version_status != VersionStatus.COMPATIBLE:
            raise UnsupportedVersion('版本未通过校验，拒绝定位战局', self.diagnostics)
        self.core.bc_addr = 0
        hint = None
        try:
            hint = json.loads(self.cache_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        if not force and hint and hint.get('pid') == self.core.mc.pid and hint.get('birth') == list(self.core.mc.process_instance or ()):
            bc = int(hint.get('bc', 0))
            if self._valid_battle(bc):
                self.core.bc_addr = bc
        if not self.core.bc_addr:
            self.log('正在定位本局，首次通常需要约 10–30 秒…')
            self._locate_active_battle()
        if not self.core._resolve_battle_clock():
            raise SampleUnavailable('未找到游戏逻辑时钟')
        self._roster_reader = None
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(dict(pid=self.core.mc.pid,
            birth=self.core.mc.process_instance, bc=self.core.bc_addr)), encoding='utf-8')

    def _base_ops(self):
        B, L, S = gs.BattleControllerFields, gs.BattleLoggerFields, gs.BattleStatsFields
        return [
            ('direct', self.core.bc_addr, max(B.UNIT_MANAGER + 8, B.M_REAL_PLAY_TIME + 4)),
            ('deref', 0, B.M_LOGGER, 0, L.READ_SIZE),
            ('deref', 1, L.STATS, 0, S.READ_SIZE),
            ('deref', 2, S.CHAR_ADVANCED_STATS, 0, 0x20),
        ]

    def read(self):
        started = time.perf_counter()
        captured_monotonic = time.monotonic()
        if time.monotonic() - self.last_pid_check > 2:
            _package, pid = self.core.mc._pid_for_known_package()
            if pid != self.core.mc.pid:
                raise ProcessChanged('游戏进程已变化，正在重新定位')
            self.last_pid_check = time.monotonic()
        channel = self.core._chan
        headers = channel.transaction_read(self._base_ops())
        if any(x is None for x in headers):
            raise SampleUnavailable('本局地址已失效，请重新定位')
        if not u64(headers[0],gs.BattleControllerFields.UNITY_CACHED_PTR):
            raise BattleChanged('上一局已离场，正在重新定位')
        count = i32(headers[3], gs.ListInternal.SIZE)
        if not 0 <= count <= 128:
            raise SampleUnavailable('干员统计容器异常')
        ops = self._base_ops()
        F = gs.BattleControllerStaticFields
        clock_size = F.DELTA_PLAY_TIME_FP - F.FIXED_FRAME_COUNT + 8
        ops.append(('direct', self.core._bc_static_fields + F.FIXED_FRAME_COUNT, clock_size))
        pair_index = len(ops)
        if count:
            ops.append(('deref', 3, gs.ListInternal.ITEMS, gs.Il2CppArray.ITEMS, count * 16))
        indices = []
        for n in range(count):
            key = len(ops)
            ops.append(('deref', pair_index, n * 16, 0, 256))
            summary = len(ops)
            ops.append(('deref', pair_index, n * 16 + 8, 0, gs.CharAdvancedStatsFields.READ_SIZE))
            head = len(ops)
            ops.append(('deref', summary, gs.CharAdvancedStatsFields.OUTPUT_DAMAGE_BY_TYPE_TOTAL, 0, 0x20))
            types = len(ops)
            ops.append(('deref', head, gs.ListInternal.ITEMS, gs.Il2CppArray.ITEMS, 24))
            indices.append((key, summary, head, types))
        native_check=len(ops)
        ops.append(('deref',0,gs.BattleControllerFields.UNITY_CACHED_PTR,NATIVE_MANAGED_REFERENCE,8))
        # Re-read the ownership chain inside the same guarded transaction.
        tail = len(ops)
        for op in self._base_ops():
            if op[0] == 'direct':
                ops.append(op)
            else:
                ops.append((op[0], op[1] + tail, *op[2:]))
        values, guard = channel.guarded_transaction_read(ops,
            self.core._bc_static_fields + F.FIXED_FRAME_COUNT)
        if not guard['complete'] or any(x is None for x in values):
            raise SampleUnavailable('采样跨帧或读取不完整，正在重试')
        B, L, S = gs.BattleControllerFields, gs.BattleLoggerFields, gs.BattleStatsFields
        if not u64(values[0], B.UNITY_CACHED_PTR) or not u64(values[tail], B.UNITY_CACHED_PTR):
            raise BattleChanged('战斗已离场；当前显示为最后有效记录')
        check_battle_ownership(self.core.bc_addr,u64(values[0],B.UNITY_CACHED_PTR),
                               u64(values[native_check]),u64(values[1],LOGGER_CONTROLLER))
        if u64(values[tail+1],LOGGER_CONTROLLER)!=self.core.bc_addr:
            raise BattleChanged('统计记录的战局归属已变化')
        for index, off in ((0, B.UNITY_CACHED_PTR), (0, B.M_LOGGER), (0, B.LEVEL_DATA), (1, L.STATS), (2, S.CHAR_ADVANCED_STATS), (3, gs.ListInternal.ITEMS)):
            if u64(values[index], off) != u64(values[tail + index], off):
                raise SampleUnavailable('战局对象已切换，正在重试')
        if any(i32(values[n], gs.ListInternal.SIZE) != count for n in (3, tail + 3)):
            raise SampleUnavailable('统计列表变化，正在重试')
        frame, game_time, _delta = self.core._decode_battle_clock_snapshot(values[4])
        if frame != guard['start'] or game_time is None or game_time < 0:
            raise SampleUnavailable('逻辑时间无效')
        rows = [decode_character(*(values[n] for n in ids), self.names) for ids in indices]
        if len({r['id'] for r in rows}) != len(rows):
            raise SampleUnavailable('重复干员标识，拒绝重复计数')
        global_total = finite(struct.unpack_from('<f', values[2], S.TOTAL_DAMAGE)[0])
        state = i32(values[0], B.M_STATE)
        if state not in (0, 1, 2, 3):
            raise SampleUnavailable('战斗状态无效')
        session = ':'.join(map(str, (*self.core.mc.process_instance, self.core.mc.pid,
            self.core.bc_addr, u64(values[1], L.STATS), u64(values[0], B.LEVEL_DATA))))
        return dict(session=session, frame=frame, game_time=game_time,
            battle_identity=(self.core.bc_addr, u64(values[0], B.UNITY_CACHED_PTR)),
            damage_basis='client character counters; includes overkill; excludes healing',
            state=state, speed=i32(values[0], B.M_SPEED_LEVEL), rows=rows,
            global_total=abs(global_total), sum_damage=sum(r['damage'] for r in rows),
            read_ms=round((time.perf_counter() - started) * 1000, 2),
            observed_at=time.time(), captured_monotonic=captured_monotonic, guard=guard)

    def read_lifecycle(self):
        if self._lifecycle_reader is None:
            raise SampleUnavailable('生命周期尚未连接')
        # This check brackets every paired acquisition, including menu polls.
        # Cached metadata must never be reused across PID/birth reuse.
        _package, pid = self.core.mc._pid_for_known_package()
        if pid != self.core.mc.pid:
            raise ProcessChanged('游戏进程已变化')
        raw = self.core.mc.shell(
            f'cat /proc/sys/kernel/random/boot_id /proc/{int(pid)}/stat', timeout=5)
        try:
            boot, stat = raw.strip().split('\n', 1)
            identity = (boot.strip(), int(stat.rsplit(')', 1)[1].split()[19]))
        except (ValueError, IndexError) as exc:
            raise SampleUnavailable('无法核对游戏进程出生标识') from exc
        if identity != self.core.mc.process_instance:
            raise ProcessChanged('游戏进程出生标识已变化')
        self.last_pid_check = time.monotonic()
        try:
            return self._lifecycle_reader.sample()
        except ReadFailure as exc:
            if exc.reason != 'binding_invalid' or self.last_pid_check - self._lifecycle_maps_refresh_at < 1:
                raise
            # Scene loads can allocate objects outside the process's cached
            # readable ranges. Refresh the ranges, then repeat all identity checks.
            self._lifecycle_maps_refresh_at = self.last_pid_check
            self.core.mc.reload_maps()
            return self._lifecycle_reader.sample()

    def bind_battle(self, address):
        """Bind only the live singleton selected by the lifecycle reader."""
        if self.core.bc_addr == address and self.core._bc_static_fields:
            return
        self.core.bc_addr = address
        self._roster_reader = None
        if not self.core._resolve_battle_clock():
            raise SampleUnavailable('未能验证当前单例的逻辑时钟')

    def read_roster(self, max_attempts: int = 8) -> RosterSnapshot:
        """Read stable statistic keys, host mapping and live membership.

        The transaction is frame-guarded and tied to the current battle
        controller; callers must treat RosterUnavailable as a gap, never as an
        empty roster.
        """
        if getattr(self, '_roster_reader', None) is None or                 self._roster_reader.bc_addr != self.core.bc_addr:
            static_fields = getattr(self.core, '_bc_static_fields', 0)
            if not self.core.bc_addr or not static_fields:
                raise RosterUnavailable('战局或逻辑时钟尚未定位')
            self._roster_reader = RosterReader(
                self.core.mc, self.core.bc_addr, static_fields, self.log)
        return self._roster_reader.read(max_attempts=max_attempts)

    def close(self):
        try:
            self.core.close()
        finally:
            try:
                self.core.mc.close()
            finally:
                for serial, local in list(self.forward_registry.owned):
                    self.forward_registry.release(serial, local)
