"""selftest_providers.py — the seam, with a fake service. No credentials."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import docint
import providers
from game import DEFAULT_CHECKS, DEFAULT_THRESHOLDS, DOCUMENTS, play

FAILS: list[str] = []

# A real Settings instance, not object() — the cache key now depends on
# .model / .api_version / .resolved_route(), so a stand-in with no such
# attributes would no longer exercise the real code path. This stays
# entirely offline: analyze_fn is always injected below, so docint.analyze
# (the only thing that would actually reach the endpoint) is never called.
FAKE_SETTINGS = docint.Settings(endpoint="https://fake.invalid/",
                                key="not-a-real-key")


def no_preflight(settings) -> None:
    """The credential check, stubbed out. Injected into every azure call
    below so this file stays entirely offline — the real preflight makes an
    authenticated GET."""


def check(label: str, got, want) -> None:
    if got != want:
        FAILS.append(f"{label}: got {got!r}, want {want!r}")


def fake_analyze(pdf: bytes, doc_id: str, settings) -> dict:
    """Stand in for the service: echo the mock kv back as DI-shaped pairs,
    but with every confidence pinned to 0.99 — which is what a real service
    reading a CLEAN pdf actually does, and the reason render.py degrades."""
    import render
    doc = next(d for d in DOCUMENTS if d["id"] == doc_id)
    pairs = []
    for kv in doc["kv"]:
        pairs.append({
            "key": {"content": render.LABELS[kv["field"]] + ":",
                    "boundingRegions": [{"pageNumber": kv.get("page", 1)}]},
            "value": {"content": str(kv["value"]),
                      "boundingRegions": [{"pageNumber": kv.get("page", 1)}]},
            "confidence": 0.99,
        })
    return {"analyzeResult": {"pages": [{"pageNumber": 1}, {"pageNumber": 2}],
                              "keyValuePairs": pairs}}


def run_checks() -> None:
    # 1. mock is unchanged and needs no settings.
    docs, report = providers.get_documents("mock")
    check("mock is the same object list", docs, DOCUMENTS)
    check("mock bills nothing", report["billed"], 0)

    # 2. azure, through the fake, returns ten documents.
    providers.clear_cache()
    calls: list[str] = []

    def counting(pdf, doc_id, settings):
        calls.append(doc_id)
        return fake_analyze(pdf, doc_id, settings)

    adocs, areport = providers.get_documents(
        "azure", settings=FAKE_SETTINGS, analyze_fn=counting,
        preflight_fn=no_preflight)
    check("azure doc count", len(adocs), 10)
    check("azure called once per document", len(calls), 10)

    # 3. TRUTH IS NEVER TAKEN FROM THE SERVICE. This is the load-bearing
    #    assertion of the whole change: if truth came from the service, the
    #    game would be scoring the service against itself.
    for a, m in zip(adocs, DOCUMENTS):
        check(f"{m['id']} truth preserved", a["truth"], m["truth"])
        check(f"{m['id']} id preserved", a["id"], m["id"])
        check(f"{m['id']} note preserved", a["note"], m["note"])
        check(f"{m['id']} hint preserved", a["hint"], m["hint"])

    # 4. The kv DID come from the service — the fake pins 0.99.
    check("azure confidence used", adocs[0]["kv"][0]["confidence"], 0.99)

    # 5. The engine scores azure documents without knowing the difference.
    scored = play(dict(DEFAULT_THRESHOLDS), dict(DEFAULT_CHECKS),
                  documents=adocs)
    check("engine scored all ten", len(scored["rows"]), 10)

    # 6. Second call is served from cache — no further billed calls.
    calls.clear()
    providers.get_documents("azure", settings=FAKE_SETTINGS,
                            analyze_fn=counting, preflight_fn=no_preflight)
    check("cache prevented re-billing", len(calls), 0)

    # 6b. A settings change (model, api_version or route) invalidates the
    #     cache even though the document and its rendering are unchanged.
    #     render.fingerprint() only covers the document/degradation, not
    #     what was asked of the service, so this has to be a SEPARATE key
    #     component — otherwise a learner who switches from prebuilt-layout
    #     to prebuilt-document (to see the "features=keyValuePairs only
    #     applies to layout" lesson) would silently get back the previous
    #     model's cached answer instead.
    calls.clear()
    other_settings = docint.Settings(endpoint="https://fake.invalid/",
                                     key="not-a-real-key",
                                     model="prebuilt-document")
    providers.get_documents("azure", settings=other_settings,
                            analyze_fn=counting, preflight_fn=no_preflight)
    check("settings change forces re-analysis", len(calls), 10)

    # 7. APP-0005's duplicate estimates survive the round trip.
    five = next(d for d in adocs if d["id"] == "APP-0005")
    dupes = [k for k in five["kv"] if k["field"] == "repair_estimate_total"]
    check("APP-0005 conflict survives", len(dupes), 2)

    # 8. search_bounds returns a sane ordered pair. Few trials on purpose —
    #    this asserts the shape and the ordering, not the numbers.
    ceiling, best = providers.search_bounds(trials=40, seed=1)
    check("ceiling is an int", isinstance(ceiling, int), True)
    check("best >= ceiling", best >= ceiling, True)

    # 9. A response with no keyValuePairs member is reported, not swallowed.
    #    That is exactly what the service returns when the
    #    features=keyValuePairs flag is missing: HTTP 200, recognised text,
    #    and no pairs. Silently, it looks like eighty missing fields.
    providers.clear_cache()
    _, empty_report = providers.get_documents(
        "azure", settings=FAKE_SETTINGS, preflight_fn=no_preflight,
        analyze_fn=lambda pdf, doc_id, st_: {"analyzeResult": {
            "pages": [{"pageNumber": 1}]}})
    check("every document reported empty", len(empty_report["empty"]), 10)

    # 10. THE CREDENTIAL CHECK RUNS ON A CACHE HIT TOO. This is the whole
    #     point of preflight living in get_documents rather than in
    #     docint.analyze(): analyze() is never called when the cache is
    #     warm, so a check that only lives there checks nothing on the run
    #     that matters. Warm the cache first, then prove a failing
    #     preflight still stops the call.
    providers.clear_cache()
    providers.get_documents("azure", settings=FAKE_SETTINGS,
                            analyze_fn=fake_analyze, preflight_fn=no_preflight)

    def refusing(settings) -> None:
        raise docint.DocIntError("credentials rejected")

    try:
        providers.get_documents("azure", settings=FAKE_SETTINGS,
                                analyze_fn=fake_analyze,
                                preflight_fn=refusing)
        FAILS.append("a warm cache served results despite a failing preflight")
    except docint.DocIntError:
        pass

    # 11. A truncated cache entry — the file an interrupted write leaves
    #     behind — is a miss, not a permanent JSONDecodeError on every
    #     later run.
    providers.clear_cache()
    docs_a, _ = providers.get_documents("azure", settings=FAKE_SETTINGS,
                                        analyze_fn=fake_analyze,
                                        preflight_fn=no_preflight)
    victim = sorted(providers.CACHE_DIR.glob("*.json"))[0]
    victim.write_text('{"analyzeResult": {"keyVal')          # interrupted
    calls.clear()
    docs_b, corrupt_report = providers.get_documents(
        "azure", settings=FAKE_SETTINGS, analyze_fn=counting,
        preflight_fn=no_preflight)
    check("corrupt entry re-analysed, not raised", corrupt_report["billed"], 1)
    check("the other nine still came from cache", corrupt_report["cached"], 9)
    check("corrupt entry recovered identically",
          [k["value"] for k in docs_b[0]["kv"]],
          [k["value"] for k in docs_a[0]["kv"]])

    providers.clear_cache()


def main() -> int:
    # THE TESTS MUST NOT TOUCH THE REAL CACHE. clear_cache() below runs
    # against the module-global CACHE_DIR, which is the same directory a
    # BILLED run writes to — so running the selftests after a live
    # calibration (the natural pre-session order) used to silently delete
    # ten paid-for responses and re-bill them on the next live run.
    real = providers.CACHE_DIR
    with tempfile.TemporaryDirectory(prefix="threshold-cache-") as tmp:
        providers.CACHE_DIR = Path(tmp)
        try:
            run_checks()
        finally:
            providers.CACHE_DIR = real

    if FAILS:
        print(f"FAIL — {len(FAILS)} problem(s):")
        for f in FAILS:
            print("  " + f)
        return 1
    print("PASS — providers seam: truth preserved, cache works, conflict "
          "survives, preflight guards cache hits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
