"""
selftest.py — prove the azure adapter works BEFORE you spend money on it.

    python3 run_lab.py --selftest

WHAT IT CHECKS
--------------
It takes the ten mock documents, re-encodes them in the shape Azure AI
Document Intelligence actually returns — `analyzeResult.keyValuePairs`, keys
with trailing colons, selection marks as `:selected:`, page numbers buried in
`boundingRegions` — and pushes them back through `azure_docint.adapt()`.

Then it asserts the pipeline produces EXACTLY what the mock provider produced.

That is the assertion worth having. If the adapter drops a colon, loses a page
number, or fails to recognise a selection mark, the run does not crash — it
quietly returns fewer fields and a different decision profile, and the report
still looks perfectly reasonable. This is the only thing standing between you
and that.

    A failing test costs you a minute.
    A silent adapter bug costs you the meeting where somebody checks a number.

There is no network call here. Run it on the plane.
"""

from __future__ import annotations

import copy

import azure_docint
import mock_docint
from extract import extract, POLICIES


def _as_di_response(doc: dict) -> dict:
    """
    The mock's key-value pairs, re-encoded the way the service really returns
    them. The awkward bits are deliberate — they are the ones that actually
    break adapters:

      * the key content carries the trailing colon printed on the form
      * a key can wrap, so the content has a newline in it
      * the page number is inside boundingRegions, not on the pair
      * a ticked box is the literal string ":selected:"
    """
    pairs = []
    for i, kv in enumerate(doc["kv"]):
        value = str(kv["value"])
        if value.lower() == "selected":
            value = ":selected:"
        elif value.lower() == "unselected":
            value = ":unselected:"

        label = kv["label"] + ":"
        if i % 4 == 3:                       # a key that wrapped on the page
            label = label.replace(" ", "\n", 1)

        pairs.append({
            "key": {
                "content": label,
                "boundingRegions": [{"pageNumber": kv["page"],
                                     "polygon": [1.0, 1.0, 2.0, 1.0, 2.0, 1.2, 1.0, 1.2]}],
                "spans": [{"offset": i * 40, "length": len(label)}],
            },
            "value": {
                "content": value,
                "boundingRegions": [{"pageNumber": kv["page"],
                                     "polygon": [2.5, 1.0, 4.0, 1.0, 4.0, 1.2, 2.5, 1.2]}],
                "spans": [{"offset": i * 40 + len(label), "length": len(value)}],
            },
            "confidence": kv["confidence"],
        })

    return {
        "status": "succeeded",
        "analyzeResult": {
            "apiVersion": "2024-11-30",
            "modelId": "prebuilt-layout",
            "stringIndexType": "textElements",
            "pages": [{"pageNumber": p, "angle": 0.0, "width": 8.5, "height": 11.0,
                       "unit": "inch", "words": [], "lines": [], "selectionMarks": []}
                      for p in range(1, doc["pages"] + 1)],
            "keyValuePairs": pairs,
            "paragraphs": [],
            "styles": [],
        },
    }


def _checks() -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        out.append((name, bool(ok), detail))

    # ---- 1. every document survives the round trip identically ------------
    for doc in mock_docint.DOCUMENTS:
        did = doc["id"]
        adapted = azure_docint.adapt(_as_di_response(doc), did)
        native = mock_docint.analyze(did)

        check(f"{did}: page count",
              adapted["pages"] == native["pages"],
              f"adapted {adapted['pages']} vs mock {native['pages']}")
        check(f"{did}: field count",
              len(adapted["key_value_pairs"]) == len(native["key_value_pairs"]),
              f"adapted {len(adapted['key_value_pairs'])} vs "
              f"mock {len(native['key_value_pairs'])}")

        for pol in POLICIES.values():
            a = extract(copy.deepcopy(adapted), pol)
            n = extract(copy.deepcopy(native), pol)
            for key in ("decision_profile", "missing_required", "needs_human"):
                check(f"{did}/{pol.name}: {key}",
                      a[key] == n[key],
                      f"adapted {a[key]!r} != mock {n[key]!r}")

    # ---- 2. the specific quirks, called out by name -----------------------
    check("trailing colon stripped from key",
          azure_docint._clean_label("Claim Reference:") == "Claim Reference")
    check("wrapped key collapsed to one line",
          azure_docint._clean_label("Vehicle\nRegistration :") == "Vehicle Registration")
    # A regression pin. Over-tidying the trailing full stop off an abbreviated
    # label unmaps it, and an unmapped label reports as a MISSING field rather
    # than as an error. Every alias below is really in LABEL_MAP.
    for abbreviated in ("D.O.B.", "Sum Ins.", "Claim Ref.", "Policy No."):
        check(f"full stop survives on {abbreviated!r}",
              azure_docint._clean_label(abbreviated + ":") == abbreviated,
              f"got {azure_docint._clean_label(abbreviated + ':')!r}")
    # The mirror image of the bug above, and the mock could never have shown
    # it. The real service tokenises the final full stop as its own word and
    # rejoins the key with a space — "D.O.B." comes back as "D.O.B .:". Strip
    # the colon only and you get "D.O.B .", which LABEL_MAP has never heard of,
    # so the field reports MISSING with no error. Measured against the live
    # service on 2026-08-16: three fields on APP-0002 were lost exactly here.
    for abbreviated in ("D.O.B.", "Sum Ins.", "Claim Ref.", "Policy No."):
        spaced = abbreviated[:-1] + " .:"          # what the service really sends
        check(f"service spacing {spaced!r} maps back to {abbreviated!r}",
              azure_docint._clean_label(spaced) == abbreviated,
              f"got {azure_docint._clean_label(spaced)!r}")
    check("a space before a comma is closed up too",
          azure_docint._clean_label("Address Line 1 , Town:") == "Address Line 1, Town",
          f"got {azure_docint._clean_label('Address Line 1 , Town:')!r}")
    check("a decimal point is not disturbed",
          azure_docint._clean_label("Excess 1.5 Percent:") == "Excess 1.5 Percent")

    # ---- 2b. pages the service silently declined to read -------------------
    #        A free-tier (F0) resource analyses the FIRST TWO PAGES ONLY and
    #        says nothing about it: HTTP 200, no error, no warning field, a
    #        `pages` array that simply stops. Proven against the live service
    #        on 2026-08-16 — identical text returned as page 1 of a 1-page PDF
    #        and vanished as page 3 of a 3-page PDF. Undetected, the lab
    #        reports those fields as MISSING and the learner concludes the
    #        extraction failed, when the page was never read at all.
    check("truncation is reported when the service returns fewer pages",
          azure_docint._truncation_note("APP-0007", declared=3, returned=2) is not None)
    check("no truncation note when every page came back",
          azure_docint._truncation_note("APP-0001", declared=2, returned=2) is None)
    check("no truncation note when the page count is unknown",
          azure_docint._truncation_note("APP-0001", declared=None, returned=2) is None)
    check("the note names the document and both counts",
          all(t in (azure_docint._truncation_note("APP-0007", 3, 2) or "")
              for t in ("APP-0007", "3", "2")),
          repr(azure_docint._truncation_note("APP-0007", 3, 2)))

    #        Knowing a page was skipped means knowing how many we sent, which
    #        is a property of the PDF, not of the response.
    import make_pdfs
    for n in (1, 2, 4):
        built = make_pdfs._build_pdf([[make_pdfs._Text(64, 700, f"Page {i + 1}")]
                                      for i in range(n)])
        check(f"a {n}-page PDF declares {n} page(s)",
              azure_docint._pdf_page_count(built) == n,
              f"got {azure_docint._pdf_page_count(built)}")
    check("an unparseable PDF gives None, not a wrong number",
          azure_docint._pdf_page_count(b"not a pdf") is None)
    check("':selected:' understood",
          azure_docint._normalise_selection(":selected:") == "selected")
    check("'[X]' understood",
          azure_docint._normalise_selection("[X]") == "selected")
    check("page number read from boundingRegions",
          azure_docint._page_of({"boundingRegions": [{"pageNumber": 3}]}) == 3)
    check("page defaults to 1 when regions are absent",
          azure_docint._page_of({}) == 1)

    # ---- 3. a key with NO value — the service does return these -----------
    #        It must become an empty value, not a crash and not a dropped row,
    #        because "the label was on the form and the box was blank" is a
    #        different fact from "the label was not on the form at all".
    orphan = {"analyzeResult": {"pages": [{"pageNumber": 1}], "keyValuePairs": [
        {"key": {"content": "Policy Number:", "boundingRegions": [{"pageNumber": 1}]},
         "value": None, "confidence": 0.91}]}}
    ad = azure_docint.adapt(orphan, "APP-TEST")
    check("key with no value is kept, with an empty value",
          len(ad["key_value_pairs"]) == 1 and ad["key_value_pairs"][0]["value"] == "",
          repr(ad["key_value_pairs"]))

    # ---- 4. an empty response is empty, not an exception ------------------
    #        This is what prebuilt-layout returns WITHOUT features=keyValuePairs:
    #        HTTP 200, no error, no pairs. Nothing tells you but the count.
    empty = azure_docint.adapt({"analyzeResult": {"pages": [{"pageNumber": 1}]}}, "APP-TEST")
    check("layout without features=keyValuePairs yields zero fields, not a crash",
          empty["key_value_pairs"] == [] and empty["pages"] == 1)

    # ---- 5. the URL is built the way the service expects -------------------
    from config import Settings
    from pathlib import Path

    def s(api_version, model, route="auto"):
        return Settings("azure", "https://x.cognitiveservices.azure.com/", "k", model,
                        api_version, route, 120, 25, True, Path("."), Path("."))

    check("2024-11-30 routes to /documentintelligence/",
          s("2024-11-30", "prebuilt-layout").resolved_route() == "documentintelligence")
    check("2023-07-31 routes to /formrecognizer/",
          s("2023-07-31", "prebuilt-document").resolved_route() == "formrecognizer")
    check("prebuilt-layout gets features=keyValuePairs",
          "features=keyValuePairs" in s("2024-11-30", "prebuilt-layout").analyze_url())
    check("prebuilt-document does not get the feature flag",
          "features=" not in s("2023-07-31", "prebuilt-document").analyze_url())
    check("an explicit route overrides the api-version",
          s("2024-11-30", "prebuilt-layout", "formrecognizer").resolved_route()
          == "formrecognizer")

    return out


def run(verbose: bool = False) -> int:
    results = _checks()
    failed = [r for r in results if not r[1]]

    if verbose:
        for name, ok, detail in results:
            print(f"  {'ok  ' if ok else 'FAIL'}  {name}"
                  + (f"   {detail}" if detail and not ok else ""))
    else:
        for name, ok, detail in failed:
            print(f"  FAIL  {name}   {detail}")

    print(f"\n  {len(results) - len(failed)}/{len(results)} checks passed.")
    if failed:
        print("\n  The adapter does NOT reproduce the mock pipeline. Fix that before\n"
              "  you run against Azure — a wrong adapter does not error, it just\n"
              "  quietly returns a different decision profile.")
        return 1
    print("\n  The adapter reproduces the mock pipeline exactly, on a response\n"
          "  shaped the way the real service shapes one. The seam is sound.")
    return 0
