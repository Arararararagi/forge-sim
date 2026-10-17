#!/usr/bin/env python3
"""Validate forge-sim sim-mode JSONL output against the required schema.

Usage: python3 validate_jsonl.py <path.jsonl>

Exits 0 if every non-empty line is a JSON object containing all required
keys; exits 1 with a diagnostic otherwise.
"""
import json
import os
import sys

REQUIRED_KEYS = [
    "seed",
    "game",
    "ms",
    "turns",
    "winner",
    "winnerSeat",
    "decks",
    "profiles",
    "finalLife",
    "commanderTurns",
    "hands",
    "eliminationOrder",
]


def validate_jsonl(path: str) -> list:
    """Return a list of error strings (empty if valid)."""
    errors = []
    if not os.path.isfile(path):
        return [f"file not found: {path}"]
    if os.path.getsize(path) == 0:
        return [f"file is empty: {path}"]

    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"line {lineno}: invalid JSON ({e})")
                continue
            if not isinstance(row, dict):
                errors.append(f"line {lineno}: not a JSON object")
                continue
            missing = [k for k in REQUIRED_KEYS if k not in row]
            if missing:
                errors.append(f"line {lineno}: missing keys {missing}")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_jsonl.py <path.jsonl>", file=sys.stderr)
        return 2
    path = sys.argv[1]
    errors = validate_jsonl(path)
    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1
    with open(path, "r", encoding="utf-8") as f:
        n = sum(1 for line in f if line.strip())
    print(f"OK: {n} game row(s), all required keys present in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
