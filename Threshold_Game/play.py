"""
play.py — the terminal version, for tinkering next to your editor in VS Code.

    python3 play.py                    # score policy.json
    python3 play.py --watch            # re-score every time you save it
    python3 play.py --doc APP-0003     # one document, field by field
    python3 play.py --compare          # the reference strategies
    python3 play.py --hint             # what each document is testing

THE SETUP THAT MAKES THIS WORK
------------------------------
Split your VS Code window. policy.json on the left, a terminal running
--watch on the right. Change a number, hit save, and the score moves before
you have looked back up.

That loop is the whole point. Under a second and people experiment. Over five
and they start planning instead, which is a different and much slower way to
learn something.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "threshold_game"))

from game import (DOCUMENTS, FIELDS, CHECKS, POINTS, DEFAULT_THRESHOLDS,   # noqa: E402
                  DEFAULT_CHECKS, play, strategy, load)

POLICY = HERE / "policy.json"
TUNING_CEILING = 252
GLOBAL_BEST = 720

G, R, Y, B, DIM, X = "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[2m", "\033[0m"


def ensure_policy() -> None:
    if POLICY.exists():
        return
    POLICY.write_text(json.dumps({
        "_comment": "Edit the numbers. Save. Watch the score. "
                    "Thresholds: above the bar accept, below it flag.",
        "thresholds": DEFAULT_THRESHOLDS,
        "_checks_comment": "Each check can override a high confidence when the "
                           "value does not look like what it claims to be.",
        "checks": DEFAULT_CHECKS,
    }, indent=2) + "\n")
    print(f"Created {POLICY.name} with the brief's settings. Open it and start tinkering.")


def banner(t: dict) -> str:
    if t["wrong_accepts"] == 0 and t["score"] > TUNING_CEILING:
        return (f"{G}Zero wrong accepts, and past {TUNING_CEILING} — the ceiling for "
                f"threshold tuning with every check off. Best known is {GLOBAL_BEST}.{X}")
    if t["wrong_accepts"] == 0:
        return f"{B}Zero wrong accepts. Now see how many flags you can give back.{X}"
    return (f"{R}{t['wrong_accepts']} wrong value(s) accepted. Each costs "
            f"{abs(POINTS['wrong_accept'])} — more than seventy flags.{X}")


def show(th: dict, ck: dict, quiet: bool = False) -> dict:
    r = play(th, ck)
    t = r["total"]
    col = G if t["score"] > 0 else R
    on = [k for k, v in ck.items() if v]

    print(f"\n  {col}SCORE {t['score']:>6}{X}     "
          f"{G}{t['correct_accepts']:>3} correct{X}   "
          f"{R if t['wrong_accepts'] else DIM}{t['wrong_accepts']:>2} WRONG{X}   "
          f"{Y}{t['flags']:>3} flagged{X}   "
          f"{DIM}{t['missing']} missing{X}")
    print(f"  {DIM}checks on: {', '.join(on) if on else 'none'}{X}")
    print(f"\n  {banner(t)}\n")

    if quiet:
        return r
    print(f"  {'document':<11} {'score':>7} {'ok':>4} {'wrong':>6} {'flag':>5}   note")
    print("  " + "-" * 74)
    for row in r["rows"]:
        doc = next(d for d in DOCUMENTS if d["id"] == row["doc_id"])
        c = G if row["wrong_accepts"] == 0 else R
        print(f"  {row['doc_id']:<11} {c}{row['score']:>7}{X} {row['correct_accepts']:>4} "
              f"{R if row['wrong_accepts'] else DIM}{row['wrong_accepts']:>6}{X} "
              f"{row['flags']:>5}   {DIM}{doc['note']}{X}")
    return r


def detail(doc_id: str, th: dict, ck: dict) -> int:
    doc = next((d for d in DOCUMENTS if d["id"] == doc_id), None)
    if not doc:
        print(f"No such document. Try: {', '.join(d['id'] for d in DOCUMENTS)}")
        return 1
    r = play(th, ck)
    row = next(x for x in r["rows"] if x["doc_id"] == doc_id)
    fields = r["per_doc"][doc_id]["result"]["fields"]

    print(f"\n  {B}{doc_id}{X} — {doc['note']}")
    print(f"  {DIM}{doc['hint']}{X}")
    print(f"\n  score {G if row['wrong_accepts'] == 0 else R}{row['score']}{X}\n")
    print(f"  {'field':<24} {'raw':<18} {'parsed':<16} {'conf':>5} {'bar':>5} "
          f"{'verdict':<8} {'pts':>5}  why")
    print("  " + "-" * 116)
    for d in row["detail"]:
        f = fields.get(d["field"])
        c = G if d["points"] > 0 else (Y if d["verdict"] == "flag" else R)
        print(f"  {d['field']:<24} {str(f.raw)[:17] if f else '':<18} "
              f"{str(f.value)[:15] if f else '':<16} "
              f"{f.confidence if f else 0:>5.2f} {th.get(d['field'], .85):>5.2f} "
              f"{c}{d['verdict']:<8}{X} {c}{d['points']:>+5}{X}  {DIM}{d['why']}{X}")
    miss = r["per_doc"][doc_id]["result"]["missing"]
    if miss:
        print(f"\n  {R}missing required: {', '.join(miss)} "
              f"({POINTS['missing_unflagged']} each){X}")
    drop = r["per_doc"][doc_id]["result"]["dropped"]
    if drop:
        print(f"\n  {G}dropped before the profile: "
              f"{', '.join(x['field'] for x in drop)}{X}")
    return 0


def compare() -> int:
    names = ["brief", "accept_everything", "flag_everything", "tuned_hard",
             "tuned_extreme", "checks_only", "checks_and_tuning"]
    print(f"\n  {'strategy':<24} {'score':>7} {'ok':>4} {'WRONG':>6} {'flags':>6}")
    print("  " + "-" * 52)
    for n in names:
        t, c = strategy(n)
        r = play(t, c)["total"]
        col = G if r["score"] > 0 else R
        print(f"  {n:<24} {col}{r['score']:>7}{X} {r['correct_accepts']:>4} "
              f"{r['wrong_accepts']:>6} {r['flags']:>6}")
    print("  " + "-" * 52)
    print(f"\n  A 4,000-trial search over thresholds with every check OFF tops out")
    print(f"  at {Y}{TUNING_CEILING}{X}, and still lets two wrong values through.")
    print(f"  Switching the checks on and leaving every threshold at 0.85 scores {G}576{X}.")
    print(f"\n  {DIM}Tuning moves who looks at a value.{X}")
    print(f"  {DIM}Validation changes whether the value is right.{X}")
    return 0


def hints() -> int:
    print()
    for d in DOCUMENTS:
        print(f"  {B}{d['id']}{X}  {d['note']}")
        print(f"      {DIM}{d['hint']}{X}\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="The Threshold Game")
    ap.add_argument("--watch", action="store_true", help="re-score on every save")
    ap.add_argument("--doc", metavar="ID", help="one document, field by field")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--hint", action="store_true")
    args = ap.parse_args()

    print(f"\n{B}THE THRESHOLD GAME{X}  ·  DecisionStream AI  ·  ten case application forms")
    print(f"{DIM}  +{POINTS['correct_accept']} correct accept   "
          f"{POINTS['wrong_accept']} WRONG accept   "
          f"{POINTS['flag']} flag   "
          f"{POINTS['missing_unflagged']} missing required{X}")

    if args.compare:
        return compare()
    if args.hint:
        return hints()

    ensure_policy()
    th, ck = load(POLICY)

    if args.doc:
        return detail(args.doc.upper(), th, ck)

    if not args.watch:
        show(th, ck)
        print(f"\n  {DIM}Edit {POLICY.name} and run again, or use --watch.{X}\n")
        return 0

    print(f"\n  {DIM}Watching {POLICY.name}. Edit and save. Ctrl-C to stop.{X}")
    last, best = None, None
    try:
        while True:
            mt = POLICY.stat().st_mtime
            if mt != last:
                last = mt
                try:
                    th, ck = load(POLICY)
                except json.JSONDecodeError as e:
                    print(f"\n  {R}policy.json is not valid JSON: {e}{X}")
                    time.sleep(0.4)
                    continue
                r = show(th, ck)
                s = r["total"]["score"]
                if best is None or s > best:
                    best = s
                    print(f"\n  {G}new best this session: {best}{X}")
                else:
                    print(f"\n  {DIM}best this session: {best}{X}")
            time.sleep(0.4)
    except KeyboardInterrupt:
        print(f"\n\n  best this session: {best}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
