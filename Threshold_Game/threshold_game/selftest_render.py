"""selftest_render.py — the rendered PDFs must be two pages and reproducible."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import render
from game import DOCUMENTS


def main() -> int:
    fails: list[str] = []

    # 1. Every document has a degradation profile, and every profile is known.
    known = {"clean", "light", "handwriting", "poor_scan"}
    for doc in DOCUMENTS:
        prof = render.PROFILES.get(doc["id"])
        if prof is None:
            fails.append(f"{doc['id']}: no profile assigned")
        elif prof not in known:
            fails.append(f"{doc['id']}: unknown profile {prof!r}")

    # 2. Every scored field has a printed label, including the four extras
    #    the arithmetic and scope_gate checks depend on.
    needed = set()
    for doc in DOCUMENTS:
        for kv in doc["kv"]:
            needed.add(kv["field"])
    for field in sorted(needed):
        if field not in render.LABELS:
            fails.append(f"no printed label for field {field!r}")

    # 3. At most two pages, always — the free tier reads two and stays silent.
    for doc in DOCUMENTS:
        n = len(render.pages(doc))
        if n < 1 or n > 2:
            fails.append(f"{doc['id']}: rendered {n} pages, want 1 or 2")

    # 4. Deterministic. Hash the IMAGES, not the PDF — Pillow stamps
    #    /CreationDate into PDF output, so PDF bytes differ every run.
    for doc in DOCUMENTS[:3]:
        if render.fingerprint(doc) != render.fingerprint(doc):
            fails.append(f"{doc['id']}: fingerprint is not reproducible")

    # 5. Distinct documents do not collide.
    prints = {d["id"]: render.fingerprint(d) for d in DOCUMENTS}
    if len(set(prints.values())) != len(prints):
        fails.append("two documents produced the same fingerprint")

    # 6. build_pdf returns something that is actually a PDF.
    pdf = render.build_pdf(DOCUMENTS[0])
    if not pdf.startswith(b"%PDF"):
        fails.append("build_pdf did not return PDF bytes")

    if fails:
        print(f"FAIL — {len(fails)} problem(s):")
        for f in fails:
            print("  " + f)
        return 1
    print(f"PASS — render: profiles, labels, page cap, determinism "
          f"({len(DOCUMENTS)} documents).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
