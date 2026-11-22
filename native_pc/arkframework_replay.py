"""Read recorded regeneration settlements from BetterBattle's schema-1 timeline."""

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path


def _integer(value, label, minimum=None):
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ValueError(f"Invalid timeline {label}")
    return value


def _text(value, label, *, empty=False):
    if not isinstance(value, str) or (not empty and not value):
        raise ValueError(f"Invalid timeline {label}")
    return value


def _version(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def read_regeneration_snapshot(path: Path) -> dict:
    """Return a replacement view of recorded hits, never an append-only event batch.

    The producer can rewrite both the file and its timeline history. Hit indices
    preserve multiplicity inside this revision only. The timeline has no global
    event sequence, process identity or end-of-battle coverage barrier.
    """
    path = Path(path)
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        raw = stream.read()
        after = os.fstat(stream.fileno())
    if (_version(before) != _version(after) or _version(after) != _version(path.stat())
            or len(raw) != after.st_size):
        raise ValueError("BetterBattle rewrote the timeline during the read")
    doc = json.loads(raw)
    if not isinstance(doc, dict) or _integer(doc.get("schema"), "schema") != 1:
        raise ValueError("Unsupported BetterBattle timeline schema")

    meta = doc.get("meta")
    if not isinstance(meta, dict):
        raise ValueError("Missing timeline battle metadata")
    level = _text(meta.get("level_id"), "level_id")
    seed = _integer(meta.get("seed"), "seed")
    stage = _text(meta.get("stage_id"), "stage_id")
    if level != doc.get("level_id") or stage != doc.get("stage") or seed != _integer(doc.get("seed"), "seed"):
        raise ValueError("Timeline battle metadata disagree")
    root = _text(doc.get("root_id"), "root_id")
    result = {
        "kind": "regeneration_snapshot",
        "source": "betterbattle.timeline",
        "battle": {
            "stage_id": stage,
            "level_id": level,
            "seed": seed,
            "created_unix_ms": _integer(meta.get("created_unix_ms"), "created_unix_ms", 1),
        },
        "revision": _integer(doc.get("revision"), "revision", 0),
        "max_frame": _integer(doc.get("max_frame"), "max_frame", 0),
        "coverage": "recorded_hits",
        "events": [],
    }
    if _text(doc.get("source"), "source") != "game" or _text(meta.get("source"), "meta source") != "game":
        result["coverage"] = "non_game_timeline"
        return result
    if any(type(doc.get(flag)) is not bool for flag in ("jumping", "advancing")):
        raise ValueError("Missing timeline playback state")
    if doc["jumping"] or doc["advancing"]:
        result["coverage"] = "timeline_jumping"
        return result
    nodes = doc.get("nodes")
    if not isinstance(nodes, list) or not nodes or not all(isinstance(n, dict) for n in nodes):
        raise ValueError("Missing timeline nodes")
    current = _text(doc.get("current_id"), "current_id")
    active_path = doc.get("active_path")
    names = {_text(node.get("id"), "node ID") for node in nodes}
    if (len(names) != len(nodes) or root not in names or not isinstance(active_path, list)
            or not active_path or active_path[0] != root or active_path[-1] != current
            or any(not isinstance(name, str) or name not in names for name in active_path)):
        raise ValueError("Invalid timeline node path")
    if len(nodes) != 1:
        result["coverage"] = "branched_timeline"
        return result
    if nodes[0].get("parent_id") != "" or active_path != [root]:
        raise ValueError("Invalid root timeline node")

    hits = doc.get("damage_hits")
    if not isinstance(hits, list):
        raise ValueError("Missing timeline damage_hits")
    for index, hit in enumerate(hits):
        if not isinstance(hit, dict) or hit.get("node_id") != root:
            raise ValueError(f"Unexpected timeline node in damage_hits[{index}]")
        if _integer(hit.get("type"), "hit type", 0) != 6:
            continue
        frame = _integer(hit.get("frame"), "hit frame", 0)
        amount = hit.get("value")
        if (frame > result["max_frame"] or type(amount) not in (int, float)
                or not 0 <= amount <= sys.float_info.max):
            raise ValueError(f"Invalid regeneration in damage_hits[{index}]")
        result["events"].append({
            "type": "regeneration",
            "node_id": root,
            "hit_index": index,
            "frame": frame,
            "actor_id": _text(hit.get("actor"), "actor", empty=True),
            "actor_uid": _integer(hit.get("actor_uid"), "actor_uid", 0),
            "target_id": _text(hit.get("target"), "target", empty=True),
            "target_uid": _integer(hit.get("target_uid"), "target_uid", 0),
            "actual_amount": amount,
        })
    return result


class RegenerationRecorder:
    """Keep the latest valid regeneration snapshot for each replay in this run."""

    def __init__(self, timeline_path: Path, output_dir: Path):
        self.timeline_path = Path(timeline_path)
        self.output_dir = Path(output_dir)
        self._last_version = None
        self._next_poll = float("-inf")
        self._last_reported_error = None

    def poll_reporting(self, now: float, audit, *, force=False) -> Path | None:
        """Report changed captures and each distinct failure to the owning collector."""
        try:
            saved = self.poll_if_due(now, force=force)
        except (OSError, ValueError) as exc:
            reason = str(exc)
            if reason != self._last_reported_error:
                audit("arkframework_timeline_unavailable", {"reason": reason})
                self._last_reported_error = reason
            return None
        if saved is not None:
            self._last_reported_error = None
            audit("arkframework_regeneration_saved", {"file": saved.name})
        return saved

    def poll_if_due(self, now: float, *, force=False) -> Path | None:
        """Throttle successful and failed polls using the caller's monotonic clock."""
        if not force and now < self._next_poll:
            return None
        self._next_poll = now + 1.0
        return self.poll()

    def poll(self) -> Path | None:
        """Atomically replace changed exports. Reads and writes can be retried on error."""
        version = _version(self.timeline_path.stat())
        if version == self._last_version:
            return None
        snapshot = read_regeneration_snapshot(self.timeline_path)
        if version != _version(self.timeline_path.stat()):
            raise ValueError("BetterBattle rewrote the timeline during the poll")
        identity = json.dumps(snapshot["battle"], sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        destination = self.output_dir / f"regeneration-{digest}.json"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.output_dir,
                                             prefix=".regeneration-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(snapshot, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                stream.write("\n")
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self._last_version = version
        return destination


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Save the published BetterBattle regeneration snapshot.")
    parser.add_argument("timeline", type=Path, help="BetterBattle battle_timeline.json")
    parser.add_argument("output_dir", type=Path, help="Directory for ArkDPS regeneration captures")
    args = parser.parse_args()
    try:
        print(RegenerationRecorder(args.timeline, args.output_dir).poll())
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")
