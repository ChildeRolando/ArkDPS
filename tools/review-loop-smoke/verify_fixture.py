"""Isolated ArkDPS Review Loop wiring acceptance check."""
import json
from pathlib import Path

fixture = json.loads(Path(__file__).with_name("fixture.json").read_text(encoding="utf-8"))
declared, observed = fixture["declared_count"], len(fixture["samples"])
if declared != observed:
    raise SystemExit(f"FAIL: declared_count={declared}; actual sample count={observed}")
print(f"PASS: declared_count={declared}; actual sample count={observed}")
