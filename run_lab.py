"""
run_lab.py — process the ten forms, score the outcome, compare policies.

    python3 run_lab.py                     # the brief's flat 0.85 threshold
    python3 run_lab.py --policy tiered     # per-field thresholds
    python3 run_lab.py --compare           # both, side by side
    python3 run_lab.py --show APP-0003     # one document, in full

Where the ten documents come from is a setting, not an argument:

    DOCINT_PROVIDER=mock     ten synthetic responses, offline, free  (default)
    DOCINT_PROVIDER=azure    ten real PDFs through Azure Document Intelligence

Set it in `.env` (see `.env.example`), or override for one run:

    python3 run_lab.py --make-pdfs             # render docs/APP-XXXX.pdf first
    python3 run_lab.py --provider azure --compare

Writes to out/<provider>/:
    profiles/APP-XXXX.json   the Azure Function's response, per document
    extraction_report.md     the report for your ADR
    field_results.csv        every field, every document, every verdict
    di_raw/APP-XXXX.json     azure only — the untouched service response
    confidence_delta.md      azure only — mock's numbers against the real ones
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "docint_lab"))

import provider as provider_mod                            # noqa: E402
from config import ConfigError                             # noqa: E402
from extract import extract, POLICIES, SCHEMA              # noqa: E402

di = None      # bound to the active provider in main()
OUT = HERE / "out"


def score(result: dict, truth: dict) -> dict:
    """
    The number that matters is not "how many fields did we accept".
    It is "how many WRONG values did we accept" — because those are the ones
    that reach a human with no warning attached.
    """
    accepted = result["decision_profile"]
    correct = wrong = 0
    wrong_fields = []

    for fname, got in accepted.items():
        if fname not in truth:
            continue
        want = truth[fname]
        if want in ("CONFLICT", "ARITHMETIC_MISMATCH", "AMBIGUOUS"):
            wrong += 1
            wrong_fields.append((fname, got, want))
            continue
        if want is None:
            wrong += 1
            wrong_fields.append((fname, got, "should be absent"))
            continue
        ok = (abs(got - want) < 0.01) if isinstance(want, float) else (str(got) == str(want))
        if ok:
            correct += 1
        else:
            wrong += 1
            wrong_fields.append((fname, got, want))

    required = [f for f, s in SCHEMA.items() if s["required"]]
    return {
        "accepted": len(accepted),
        "correct": correct,
        "wrong_accepted": wrong,
        "wrong_fields": wrong_fields,
        "flagged": len(result["review_queue"]),
        "missing": len(result["missing_required"]),
        "auto_rate": round(correct / len(required), 2),
    }


def run(policy_name: str, quiet: bool = False) -> tuple[list[dict], dict]:
    pol = POLICIES[policy_name]
    rows, totals = [], {"accepted": 0, "correct": 0, "wrong_accepted": 0,
                        "flagged": 0, "missing": 0}

    if not quiet:
        print(f"\nPolicy: {pol.name}")
        print(f"{'doc':<10} {'acc':>4} {'ok':>4} {'WRONG':>6} {'flag':>5} {'miss':>5}  note")
        print("-" * 78)

    for did in di.all_ids():
        res = extract(di.analyze(did), pol)
        sc = score(res, di.truth(did))
        res["score"] = sc
        rows.append(res)
        for k in totals:
            totals[k] += sc[k]
        if not quiet:
            flag = "  <-- " + ", ".join(f"{f}={g}" for f, g, _ in sc["wrong_fields"][:2]) \
                if sc["wrong_accepted"] else ""
            print(f"{did:<10} {sc['accepted']:>4} {sc['correct']:>4} "
                  f"{sc['wrong_accepted']:>6} {sc['flagged']:>5} {sc['missing']:>5}  "
                  f"{di.note(did)[:28]}{flag}")
        (OUT / "profiles" / f"{did}.json").write_text(json.dumps(res, indent=2, default=str))

    if not quiet:
        print("-" * 78)
        print(f"{'TOTAL':<10} {totals['accepted']:>4} {totals['correct']:>4} "
              f"{totals['wrong_accepted']:>6} {totals['flagged']:>5} {totals['missing']:>5}")
    return rows, totals


def show(doc_id: str) -> int:
    if doc_id not in di.all_ids():
        print(f"No such document. Try: {', '.join(di.all_ids())}")
        return 1
    print(f"\n{doc_id} — {di.note(doc_id)}\n" + "=" * 70)
    for pname in ("flat_0.85", "tiered"):
        res = extract(di.analyze(doc_id), POLICIES[pname])
        sc = score(res, di.truth(doc_id))
        print(f"\n  POLICY: {pname}")
        print(f"  accepted {sc['accepted']}  correct {sc['correct']}  "
              f"WRONG-ACCEPTED {sc['wrong_accepted']}  flagged {sc['flagged']}")
        if sc["wrong_fields"]:
            for f, got, want in sc["wrong_fields"]:
                print(f"    WRONG  {f}: accepted '{got}'  should be '{want}'")
        for r in res["review_queue"]:
            print(f"    flag   {r['field']:<24} conf {r['confidence']:.2f}  "
                  f"'{r['raw']}' -> {r['value']}  {r['notes']}")
        for m in res["missing_required"]:
            print(f"    MISSING {m}")
        for c in res["conflicts"]:
            print(f"    CONFLICT {c['field']}: p{c['a']['page']}={c['a']['value']} "
                  f"vs p{c['b']['page']}={c['b']['value']}")
        for v in res["validations"]:
            print(f"    VALIDATION {v['code']}: {v['detail']}")
        for d in res["dropped"]:
            print(f"    dropped  {d['label']} (p{d['page']}) — {d['reason']}")
    return 0


def write_report(results: dict[str, tuple[list, dict]]) -> None:
    with open(OUT / "field_results.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["policy", "document", "field", "raw", "value", "confidence",
                    "page", "verdict", "notes"])
        for pname, (rows, _) in results.items():
            for r in rows:
                for fn, v in r["decision_profile"].items():
                    w.writerow([pname, r["document_id"], fn, "", v, "", "", "accept", ""])
                for q in r["review_queue"]:
                    w.writerow([pname, r["document_id"], q["field"], q["raw"], q["value"],
                                q["confidence"], q["page"], q["verdict"], "|".join(q["notes"])])

    md = ["# Document Intelligence extraction report — DecisionStream AI", "",
          f"Documents processed: **{len(di.all_ids())}**",
          f"Source: **{di.name}** "
          + ("(synthetic responses — the confidences were chosen to make a "
             "teaching point, not measured)" if di.name == "mock"
             else "(Azure AI Document Intelligence — measured confidences; "
                  "see `confidence_delta.md`)"), "",
          "## Policy comparison", "",
          "| Policy | Accepted | Correct | **Wrong accepted** | Flagged | Missing |",
          "|---|---|---|---|---|---|"]
    for pname, (_, t) in results.items():
        md.append(f"| {pname} | {t['accepted']} | {t['correct']} | "
                  f"**{t['wrong_accepted']}** | {t['flagged']} | {t['missing']} |")

    md += ["", "### How to read this table", "",
           "**Wrong accepted** is the only column that can hurt a customer. Those "
           "are values the pipeline passed through as correct, with no warning "
           "attached, that were not correct.",
           "",
           "Flagged is not failure. A flagged field costs a handler thirty seconds. "
           "A wrongly accepted identifier costs a rework cycle, a complaint, or a "
           "regulatory finding — and nobody knows it happened until someone "
           "notices downstream.", "",
           "## Per-document notes", "",
           "| Document | Pages | What makes it hard |", "|---|---|---|"]
    for d in di.DOCUMENTS:
        md.append(f"| {d['id']} | {d['pages']} | {d['note']} |")

    md += ["", "## Questions to answer before this goes in the ADR", "",
           "1. What is your accept threshold for an IDENTIFIER, and why is it "
           "different from your threshold for a name?",
           "2. APP-0003 returned a claim reference with a letter O in place of a "
           "zero, at confidence 0.88. What caught it — the threshold, or the "
           "pattern check? What does that tell you about relying on confidence?",
           "3. Your pipeline repairs some OCR errors automatically. Should a "
           "repaired value ever be auto-accepted? Who decided?",
           "4. APP-0007 contained driving licence data. Your pipeline dropped it. "
           "Was that the right call, and where is that decision recorded?",
           "5. What is the handler cost of your flag rate at 4,000 documents a "
           "month, and does the business know that number?",
           "6. What is your review trigger for re-tuning these thresholds?", ""]

    (OUT / "extraction_report.md").write_text("\n".join(md))


def main() -> int:
    global di, OUT

    ap = argparse.ArgumentParser(description="Document Intelligence extraction lab")
    ap.add_argument("--policy", default="flat_0.85", choices=list(POLICIES))
    ap.add_argument("--compare", action="store_true", help="run both policies")
    ap.add_argument("--show", metavar="DOC_ID", help="one document, both policies, in full")
    ap.add_argument("--provider", choices=("mock", "azure"),
                    help="override DOCINT_PROVIDER from .env for this run")
    ap.add_argument("--make-pdfs", action="store_true",
                    help="render the ten documents to docs/ and exit "
                         "(what the azure provider posts)")
    ap.add_argument("--selftest", action="store_true",
                    help="prove the azure adapter reproduces the mock pipeline, "
                         "offline, before you spend anything")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    print("DecisionStream AI — Document Intelligence extraction lab")
    print("=" * 78)

    if args.selftest:
        import selftest
        print("\nAdapter self-test — no network, no cost.\n")
        return selftest.run(args.verbose)

    if args.make_pdfs:
        import make_pdfs
        from config import get_settings
        doc_dir = get_settings(args.provider).doc_dir
        print(f"\nRendering the ten case application forms to {doc_dir}/\n")
        made = make_pdfs.make_all(doc_dir)
        print(f"\n{len(made)} PDFs written. These are what "
              f"DOCINT_PROVIDER=azure posts to the service.")
        return 0

    # ----------------------------------------------------------------------
    # The switch. Everything below this line is provider-agnostic.
    # ----------------------------------------------------------------------
    try:
        di, settings = provider_mod.get(args.provider)
    except ConfigError as e:
        print(f"\nConfiguration error:\n  {e}\n")
        return 2
    except Exception as e:                       # azure preflight failures
        print(f"\n{type(e).__name__}:\n  {e}\n")
        return 2

    print(provider_mod.describe(settings))
    OUT = settings.out_dir / settings.provider
    (OUT / "profiles").mkdir(parents=True, exist_ok=True)

    if args.show:
        return show(args.show.upper())

    names = list(POLICIES) if args.compare else [args.policy]
    results = {}
    try:
        for n in names:
            rows, totals = run(n)
            results[n] = (rows, totals)
    except Exception as e:
        # A billed batch that stops loudly beats one that half-succeeds and
        # writes a report nobody knows is incomplete.
        print(f"\n{type(e).__name__}:\n  {e}\n")
        return 1

    write_report(results)

    if args.compare:
        print("\n" + "=" * 78)
        for n in names:
            t = results[n][1]
            print(f"  {n:<20} {t['correct']:>3} correct, "
                  f"{t['wrong_accepted']:>2} WRONG accepted, {t['flagged']:>3} flagged")
        a = results["flat_0.85"][1]
        b = results["flat_0.85_checked"][1]
        c = results["tiered"][1]
        # Say what the numbers say. An earlier version asserted the checks
        # removed "all" of them, which was true of the mock and false the first
        # time real data left one behind — the worst kind of wrong, because the
        # sentence sits directly under a table that contradicts it.
        removed = a["wrong_accepted"] - b["wrong_accepted"]
        print("\n  READ THIS CAREFULLY:")
        print(f"  Adding the CHECKS removed {removed} of {a['wrong_accepted']} wrong "
              f"acceptances (+{b['flagged'] - a['flagged']} flags).")
        if b["wrong_accepted"]:
            print(f"  {b['wrong_accepted']} still got through. Find it in the table above "
                  f"and explain it")
            print(f"  before you present anything else on this slide.")
        print(f"  Adding TIERED THRESHOLDS on top removed "
              f"{b['wrong_accepted'] - c['wrong_accepted']} more "
              f"(+{c['flagged'] - b['flagged']} flags).")
        if b["wrong_accepted"] == c["wrong_accepted"]:
            print("\n  The validation did the work. The threshold tuning bought nothing")
            print("  here except a bigger review queue. Be able to say that out loud —")
            print("  and be ready for a client who assumes tighter thresholds are safer.")
        else:
            print("\n  The tiered thresholds caught something the checks did not. Say")
            print("  which field, and what its cost per flag is, before you recommend it.")

    print(f"\nWritten: {OUT/'extraction_report.md'}")
    print(f"         {OUT/'field_results.csv'}")
    print(f"         {OUT/'profiles'}/  ({len(di.all_ids())} JSON profiles)")

    if settings.provider == "azure":
        import azure_docint
        (OUT / "confidence_delta.md").write_text(azure_docint.confidence_delta(settings))
        print(f"         {OUT/'confidence_delta.md'}   <-- read this one")
        print(f"         {OUT/'di_raw'}/  (untouched service responses)")

        # Pages the service never read. Printed AFTER the table, because the
        # table is what it corrects: those fields are counted as missing there
        # and they are not extraction failures.
        if azure_docint.PAGE_TRUNCATIONS:
            print("\n" + "=" * 78)
            print("  PAGES THE SERVICE DID NOT READ")
            for note in azure_docint.PAGE_TRUNCATIONS:
                print(f"    {note}")
            print("\n  A free-tier (F0) resource analyses the first two pages and stops.")
            print("  No error, no warning — the response just ends early. Those fields")
            print("  are counted as MISSING above, which is the wrong word for them:")
            print("  nobody showed the model the page. Upgrade to S0 to read them all.")
            print("\n  Do not quote a per-document field count from this run without")
            print("  saying which documents were truncated.")

    print("\nNow open extraction_report.md and answer the six questions at the bottom.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
