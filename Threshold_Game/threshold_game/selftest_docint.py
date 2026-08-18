"""selftest_docint.py — the Azure adapter, offline. No credentials, no cost.

Everything here runs against recorded payload shapes. Run it before spending
money on a live batch.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import docint

FAILS: list[str] = []


def check(label: str, got, want) -> None:
    if got != want:
        FAILS.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def test_urls() -> None:
    s = docint.Settings(endpoint="https://x.cognitiveservices.azure.com/", key="k")

    # 2024-11-30 and later live under /documentintelligence/.
    check("route 2024-11-30", s.resolved_route(), "documentintelligence")

    # Anything earlier is /formrecognizer/. A mismatch 404s with a message
    # about the MODEL, which sends you hunting in the wrong place entirely.
    old = docint.Settings(endpoint="https://x/", key="k", api_version="2023-07-31")
    check("route 2023-07-31", old.resolved_route(), "formrecognizer")

    # An explicit route wins over the api-version.
    forced = docint.Settings(endpoint="https://x/", key="k", route="formrecognizer")
    check("forced route", forced.resolved_route(), "formrecognizer")

    # prebuilt-layout MUST carry the feature flag. Without it the service
    # returns HTTP 200 and zero key-value pairs — a successful, empty and
    # entirely misleading response.
    check("keyValuePairs flag present", "features=keyValuePairs" in s.analyze_url(), True)

    check("analyze url", s.analyze_url(),
          "https://x.cognitiveservices.azure.com/documentintelligence"
          "/documentModels/prebuilt-layout:analyze"
          "?api-version=2024-11-30&features=keyValuePairs")

    # A trailing slash on the endpoint must not double up.
    check("no double slash", "//documentintelligence" in s.analyze_url(), False)

    # A model that is not prebuilt-layout does not get the flag.
    doc = docint.Settings(endpoint="https://x/", key="k", model="prebuilt-document",
                          api_version="2023-07-31")
    check("no flag for prebuilt-document", "features=" in doc.analyze_url(), False)


def test_require() -> None:
    try:
        docint.Settings(endpoint="", key="").require()
        FAILS.append("require(): empty credentials did not raise")
    except docint.DocIntError as e:
        check("require names endpoint", "endpoint" in str(e).lower(), True)
        check("require names key", "key" in str(e).lower(), True)


def test_parse_retry_after() -> None:
    # Numeric delta-seconds form
    check("retry_after numeric", docint._parse_retry_after("120"), 120.0)
    
    # Non-numeric HTTP-date form (falls back to default)
    check("retry_after http-date",
          docint._parse_retry_after("Fri, 31 Dec 2027 23:59:59 GMT"), 20.0)
    
    # Missing header
    check("retry_after missing", docint._parse_retry_after(None), 20.0)
    
    # Empty string
    check("retry_after empty", docint._parse_retry_after(""), 20.0)
    
    # Zero is valid
    check("retry_after zero", docint._parse_retry_after("0"), 0.0)
    
    # Float string is valid
    check("retry_after float", docint._parse_retry_after("1.5"), 1.5)
    
    # Invalid format (malformed date or garbage) falls back
    check("retry_after garbage",
          docint._parse_retry_after("not-a-number-or-date"), 20.0)

    # THE THREE float() PARSES BUT time.sleep() WILL NOT ACCEPT.
    # "-5" and "nan" both raise ValueError: sleep length must be
    # non-negative — AFTER the document has been billed, so the crash costs
    # money and throws away the response it just paid for. "inf" does not
    # raise but would pin the client at the 60s cap on a header that means
    # nothing. All three are unusable headers, so all three take the default.
    check("retry_after negative", docint._parse_retry_after("-5"), 20.0)
    check("retry_after nan", docint._parse_retry_after("nan"), 20.0)
    check("retry_after inf", docint._parse_retry_after("inf"), 20.0)

    # And the clamp at the sleep site itself, which is what every sleep in
    # this module actually passes through.
    check("bounded negative", docint._bounded_wait(-5.0), 0.0)
    check("bounded nan", docint._bounded_wait(float("nan")), 20.0)
    check("bounded inf", docint._bounded_wait(float("inf")), 60.0)
    check("bounded caps at 60", docint._bounded_wait(3600.0), 60.0)
    check("bounded passes a sane wait", docint._bounded_wait(2.5), 2.5)

    # The real assertion: whatever comes back is something time.sleep()
    # will take. Cheap to prove — sleep(0) is free.
    import time as _t
    for header in ("-5", "nan", "inf", "0", "120", None, "garbage"):
        _t.sleep(0 * docint._bounded_wait(docint._parse_retry_after(header)))


def test_endpoint_scheme() -> None:
    """An endpoint pasted without its scheme must fail HERE, with a message.

    urllib raises ValueError: unknown url type for "x.cognitiveservices.
    azure.com" — and ValueError is not URLError, so it sails past the
    friendly handler in _request() and lands as a raw traceback.
    """
    try:
        docint.Settings(endpoint="x.cognitiveservices.azure.com",
                        key="k").require()
        FAILS.append("require(): scheme-less endpoint did not raise")
    except docint.DocIntError as e:
        check("scheme error names https", "https://" in str(e), True)

    # Both schemes are accepted; a good endpoint must not be blocked.
    docint.Settings(endpoint="https://x/", key="k").require()
    docint.Settings(endpoint="http://localhost:8080", key="k").require()


def test_key_not_in_repr() -> None:
    """A traceback frame, a print(), or Streamlit's exception page will
    render this dataclass. None of them may render the key."""
    s = docint.Settings(endpoint="https://x/", key="super-secret-key")
    check("key absent from repr", "super-secret-key" in repr(s), False)
    check("endpoint still in repr", "https://x/" in repr(s), True)


def test_preflight() -> None:
    """The credential check that runs before the cache is consulted.

    It must be strict enough to catch a wrong key and lenient enough never
    to block a working resource — so ONLY 401/403 (and a connectivity
    failure, which _request already raises for) are fatal. A 404 here may
    mean nothing more than that this resource does not serve the
    model-listing route.
    """
    # 1. Empty credentials fail before any request is even attempted.
    reached = []

    def must_not_be_called(url, settings, *a, **k):
        reached.append(url)
        return 200, {}, {}

    try:
        docint.preflight(docint.Settings(endpoint="", key=""),
                         request_fn=must_not_be_called)
        FAILS.append("preflight(): empty credentials did not raise")
    except docint.DocIntError as e:
        check("preflight names the missing endpoint",
              "endpoint" in str(e).lower(), True)
    check("preflight made no request without credentials", reached, [])

    # 2. Status classification. 401/403 fatal, everything else survivable.
    check("401 is fatal", docint._preflight_verdict(401) is None, False)
    check("403 is fatal", docint._preflight_verdict(403) is None, False)
    check("200 proceeds", docint._preflight_verdict(200), None)
    check("404 proceeds", docint._preflight_verdict(404), None)
    check("429 proceeds", docint._preflight_verdict(429), None)
    check("500 proceeds", docint._preflight_verdict(500), None)
    check("401 message names the key",
          "key" in docint._preflight_verdict(401).lower(), True)

    # 3. End to end through an injected transport — no network, no cost.
    #    A distinctive key, so "did the key leak" is a real question.
    good = docint.Settings(endpoint="https://x/", key="SECRET-KEY-VALUE")
    seen = []

    def transport(status):
        def fn(url, settings, *a, **k):
            seen.append(url)
            return status, {}, {}
        return fn

    docint.preflight(good, request_fn=transport(200))
    docint.preflight(good, request_fn=transport(404))     # must NOT raise
    docint.preflight(good, request_fn=transport(500))     # must NOT raise
    try:
        docint.preflight(good, request_fn=transport(401))
        FAILS.append("preflight(): 401 did not raise")
    except docint.DocIntError as e:
        check("401 message never contains the key",
              "SECRET-KEY-VALUE" in str(e), False)

    # 4. The URL it asks for is the unbilled listing route, not an analyse,
    #    and the key travels in the header, never in the query string.
    check("preflight lists models", seen[0],
          "https://x/documentintelligence/documentModels"
          "?api-version=2024-11-30")
    check("preflight bills nothing", ":analyze" in seen[0], False)
    check("key never in the url", "SECRET-KEY-VALUE" in seen[0], False)


def test_clean_label() -> None:
    # The ordinary case: strip the trailing colon.
    check("colon", docint._clean_label("Claim Ref.:"), "Claim Ref.")

    # DO NOT STRIP THE FULL STOP. Half the labels on a real form abbreviate:
    # "D.O.B.", "Sum Ins.", "Claim Ref.", "Policy No.". Tidying the trailing
    # "." off those makes them labels the map has never heard of, and an
    # unmapped label does not raise — the field simply reports as missing.
    check("full stop kept", docint._clean_label("Policy No.:"), "Policy No.")

    # And the other half of the same bug, which only the LIVE service shows.
    # DI reads a page as words, and the final full stop of an abbreviation is
    # its own word. Rejoined, "D.O.B.:" comes back as "D.O.B .:" — strip only
    # the colon and you get "D.O.B .", which is just as unmapped. The mock
    # could not have caught this: its labels are clean strings.
    check("tokenised full stop", docint._clean_label("D.O.B .:"), "D.O.B.")
    check("tokenised, no colon", docint._clean_label("Sum Ins ."), "Sum Ins.")

    # Line breaks and runs of whitespace collapse.
    check("newline", docint._clean_label("Repair Estimate\n  Total :"),
          "Repair Estimate Total")


def _pair(label: str, value: str, conf: float, page: int = 1) -> dict:
    return {
        "key": {"content": label,
                "boundingRegions": [{"pageNumber": page}]},
        "value": {"content": value,
                  "boundingRegions": [{"pageNumber": page}]},
        "confidence": conf,
    }


def test_adapt() -> None:
    payload = {"analyzeResult": {
        "pages": [{"pageNumber": 1}, {"pageNumber": 2}],
        "keyValuePairs": [
            _pair("Claim Ref.:", "CLM-2026-4526", 0.96),
            _pair("D.O.B .:", "1984-02-17", 0.93),          # the tokenised case
            _pair("Sum Ins.:", "6400.00", 0.92),
            # APP-0005's two estimates. If the adapter keys by field name
            # these collapse into one and the conflict check has nothing to
            # fire on — which is the entire point of that document.
            _pair("Repair Estimate Total:", "2150.00", 0.94, 2),
            _pair("Repair Estimate Total:", "2450.00", 0.91, 2),
            _pair("Underwriter Notes:", "n/a", 0.88),       # not a game field
        ]}}

    kvs, unmapped = docint.adapt(payload, "APP-0005")

    check("kv count", len(kvs), 5)
    check("d.o.b. mapped", [k["field"] for k in kvs].count("date_of_birth"), 1)
    check("sum insured mapped", kvs[2]["field"], "sum_insured")

    dupes = [k for k in kvs if k["field"] == "repair_estimate_total"]
    check("duplicates preserved", len(dupes), 2)
    check("duplicate values", sorted(k["value"] for k in dupes),
          ["2150.00", "2450.00"])

    check("confidence is a float", isinstance(kvs[0]["confidence"], float), True)
    check("confidence value", kvs[0]["confidence"], 0.96)
    check("page carried", dupes[0]["page"], 2)

    # An unmapped label is reported, never silently dropped.
    check("unmapped reported", unmapped, ["Underwriter Notes"])

    # A pair with an empty key is skipped without blowing up.
    empty = {"analyzeResult": {"keyValuePairs": [_pair("", "x", 0.5)]}}
    check("empty key skipped", docint.adapt(empty, "X")[0], [])

    # No keyValuePairs member at all — the shape you get when the
    # features=keyValuePairs flag is missing. Must not raise.
    check("no pairs member", docint.adapt({"analyzeResult": {}}, "X")[0], [])


def test_poll_rate_limit() -> None:
    """The poll loop under a permanent 429. Offline: _request is replaced.

    F2 made the poll loop retry 429 instead of raising, which is right — on
    the free tier a 429 on a poll is the common case, not an edge case. But
    it is the riskiest behavioural change in the wave, because a retry loop
    that does not bound itself is worse than the raise it replaced.

    Retry-After: 0 is a legal header, so `wait` can be exactly zero. Before
    the floor, this exact scenario issued 4,513,483 GETs in three seconds —
    one round trip apiece, at an endpoint that had just said "rate limited".
    THE REQUEST COUNT IS THE ASSERTION. "It terminates" was already true of
    the spinning version; it terminated at the deadline, having hammered the
    service for the entire budget.
    """
    import time as _t
    real_request = docint._request

    def transport(status, retry_after):
        """202 to the POST, then `status` forever to the polls."""
        seen = {"n": 0}

        def fn(url, settings, method="GET", body=None, content_type=None):
            seen["n"] += 1
            if method == "POST":
                return 202, {"Operation-Location": "https://x/op/1"}, {}
            return status, {"Retry-After": retry_after}, {"_error_body": "slow down"}
        return fn, seen

    # timeout=2 keeps this test to about two seconds of real sleeping.
    s = docint.Settings(endpoint="https://x/", key="k", timeout=2)

    try:
        # 1. Permanent 429, Retry-After: 0 — the spin case.
        fn, seen = transport(429, "0")
        docint._request = fn
        t0 = _t.time()
        try:
            docint.analyze(b"%PDF-", "APP-0001", s)
            FAILS.append("poll 429: a permanent 429 never terminated")
        except docint.DocIntError as e:
            check("poll 429 says rate-limited", "rate-limited" in str(e), True)
            check("poll 429 names the budget", "budget" in str(e), True)
        # 1 POST + a poll per second of budget. Anything in the thousands is
        # the hot loop back; anything at all above ~10 is not a backoff.
        check("poll 429 is bounded, not a hot loop", seen["n"] <= 10, True)
        check("poll 429 still used the budget", _t.time() - t0 >= 1.0, True)

        # 2. Permanent 429 with a wait far longer than the budget. It must
        #    raise rather than sleep past the deadline the caller asked for.
        fn, seen = transport(429, "600")
        docint._request = fn
        t0 = _t.time()
        try:
            docint.analyze(b"%PDF-", "APP-0002", s)
            FAILS.append("poll 429: a 600s wait did not raise")
        except docint.DocIntError:
            pass
        check("long wait raises at once", seen["n"], 2)          # POST + 1 poll
        check("long wait did not sleep", _t.time() - t0 < 1.0, True)

        # 3. And the ordinary path still works: 429, then a success.
        states = iter([(429, {"Retry-After": "0"}, {}),
                       (200, {}, {"status": "succeeded", "analyzeResult": {}})])

        def recovering(url, settings, method="GET", body=None, content_type=None):
            if method == "POST":
                return 202, {"Operation-Location": "https://x/op/1"}, {}
            return next(states)

        docint._request = recovering
        payload = docint.analyze(b"%PDF-", "APP-0003", s)
        check("a 429 mid-poll recovers", payload.get("status"), "succeeded")
    finally:
        docint._request = real_request


def main() -> int:
    test_urls()
    test_require()
    test_endpoint_scheme()
    test_key_not_in_repr()
    test_preflight()
    test_parse_retry_after()
    test_poll_rate_limit()
    test_clean_label()
    test_adapt()
    if FAILS:
        print(f"FAIL — {len(FAILS)} problem(s):")
        for f in FAILS:
            print("  " + f)
        return 1
    print("PASS — docint offline checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
