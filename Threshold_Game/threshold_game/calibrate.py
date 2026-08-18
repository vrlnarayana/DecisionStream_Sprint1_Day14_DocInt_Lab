"""
calibrate.py — measure what the live service actually reports, per document.

TRAINER TOOL. Not part of the learner flow, and not reachable from the board.

render.py claims APP-0010 will come back around 0.55-0.67 and APP-0003 around
0.69-0.90. Those are targets, not facts. This prints what the service really
says so PARAMS can be tuned until the claim is true.

    AZURE_DOCINT_ENDPOINT=https://... AZURE_DOCINT_KEY=... \
        .venv/bin/python threshold_game/calibrate.py

Credentials are read from the environment at run time and are never written
anywhere. Each run costs ten analyses unless they are already cached.
"""

from __future__ import annotations

import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import docint
import providers
import render

TARGETS = {
    "clean":       (0.90, 0.97),
    "light":       (0.87, 0.91),
    "handwriting": (0.69, 0.90),
    "poor_scan":   (0.55, 0.67),
}


def main() -> int:
    endpoint = os.environ.get("AZURE_DOCINT_ENDPOINT", "")
    key = os.environ.get("AZURE_DOCINT_KEY", "")
    if not (endpoint and key):
        print("Set AZURE_DOCINT_ENDPOINT and AZURE_DOCINT_KEY in the "
              "environment.\nThey are read at run time and never stored.")
        return 2

    settings = docint.Settings(endpoint=endpoint, key=key)
    try:
        docs, report = providers.get_documents(
            "azure", settings=settings,
            on_progress=lambda i, n, d: print(f"  [{i}/{n}] {d}", flush=True))
    except docint.DocIntError as e:
        # Every message this raises was written to be read. A traceback on
        # top of one only buries it — and the usual causes here (wrong key,
        # wrong region, endpoint pasted without its scheme, resource behind
        # a VNet) are configuration, not a bug in this file.
        print(f"\n{e}", file=sys.stderr)
        return 2

    print(f"\n{report['billed']} analysed, {report['cached']} from cache.\n")
    print(f"{'document':<12}{'profile':<14}{'n':>3}  {'min':>6}{'mean':>7}"
          f"{'max':>7}   target        verdict")
    print("-" * 78)

    out_of_band = 0
    for doc in docs:
        confs = [k["confidence"] for k in doc["kv"]]
        prof = render.PROFILES[doc["id"]]
        lo, hi = TARGETS[prof]
        if not confs:
            print(f"{doc['id']:<12}{prof:<14}{0:>3}  "
                  f"{'— no fields returned —':>27}   "
                  f"{lo:.2f}-{hi:.2f}   TOO DEGRADED")
            out_of_band += 1
            continue
        mn, mean, mx = min(confs), statistics.mean(confs), max(confs)
        if mean < lo:
            verdict, bad = "too low  (ease off)", True
        elif mean > hi:
            verdict, bad = "too high (degrade more)", True
        else:
            verdict, bad = "in band", False
        out_of_band += bad
        print(f"{doc['id']:<12}{prof:<14}{len(confs):>3}  {mn:>6.3f}{mean:>7.3f}"
              f"{mx:>7.3f}   {lo:.2f}-{hi:.2f}   {verdict}")

    print()
    if report["unmapped"]:
        print("Unmapped labels (add to docint.LABEL_MAP if a field is missing):")
        for u in report["unmapped"]:
            print(f"  {u}")
        print()
    if out_of_band:
        print(f"{out_of_band} document(s) outside their target band. "
              f"Adjust render.PARAMS, then:")
        print("  .venv/bin/python -c \"import sys;sys.path.insert(0,"
              "'threshold_game');import providers;providers.clear_cache()\"")
        print("and run this again — the cache is keyed on the image "
              "fingerprint, so changed parameters re-analyse automatically.")
        return 1
    print("All documents inside their target confidence bands.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
