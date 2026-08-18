"""selftest_seam.py — play() must score whatever document list it is given."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from game import DEFAULT_CHECKS, DEFAULT_THRESHOLDS, DOCUMENTS, play

CHECKS_OFF = dict(DEFAULT_CHECKS)


def check(label: str, got, want, fails: list) -> None:
    if got != want:
        fails.append(f"{label}: got {got!r}, want {want!r}")


def main() -> int:
    fails: list[str] = []
    th = dict(DEFAULT_THRESHOLDS)

    # 1. No documents argument behaves exactly as before.
    baseline = play(th, CHECKS_OFF)
    check("default doc count", len(baseline["rows"]), 10, fails)

    # 2. An explicit list of one document scores only that document.
    one = play(th, CHECKS_OFF, documents=[DOCUMENTS[0]])
    check("single doc count", len(one["rows"]), 1, fails)
    check("single doc id", one["rows"][0]["doc_id"], "APP-0001", fails)

    # 3. Passing DOCUMENTS explicitly equals passing nothing.
    explicit = play(th, CHECKS_OFF, documents=DOCUMENTS)
    check("explicit == default", explicit["total"], baseline["total"], fails)

    # 4. An empty list scores zero, it does not fall back to DOCUMENTS.
    empty = play(th, CHECKS_OFF, documents=[])
    check("empty doc count", len(empty["rows"]), 0, fails)
    check("empty score", empty["total"]["score"], 0, fails)

    if fails:
        print(f"FAIL — {len(fails)} problem(s):")
        for f in fails:
            print("  " + f)
        return 1
    print("PASS — play() honours the documents argument (4 checks).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
