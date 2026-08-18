"""
docint.py — Azure AI Document Intelligence, over stdlib urllib.

Ported from the Day-14 lab's azure_docint.py, which was run against a live
resource on 2026-08-16. Every comment below that reads like a warning is
one this code has already paid for once.

There is no SDK here on purpose. The whole client is one POST, one poll loop
and one adapter, and a learner can read all of it.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


class DocIntError(RuntimeError):
    pass


@dataclass
class Settings:
    endpoint: str
    # repr=False so an accidental print(settings), a traceback frame or a
    # Streamlit exception page never renders the subscription key in plain
    # text. A dataclass repr would otherwise do exactly that.
    key: str = field(repr=False)
    model: str = "prebuilt-layout"
    api_version: str = "2024-11-30"
    route: str = "auto"
    timeout: int = 120

    def resolved_route(self) -> str:
        if self.route != "auto":
            return self.route
        # 2024-11-30 was the GA rename. String comparison is correct here
        # because the format is fixed-width ISO.
        return ("documentintelligence" if self.api_version >= "2024-11-30"
                else "formrecognizer")

    def analyze_url(self) -> str:
        base = self.endpoint.rstrip("/")
        url = (f"{base}/{self.resolved_route()}/documentModels/"
               f"{self.model}:analyze?api-version={self.api_version}")
        # prebuilt-document was RETIRED in the 2024-11-30 GA API. Key-value
        # pair extraction moved into prebuilt-layout behind an opt-in flag,
        # and WITHOUT the flag layout returns no keyValuePairs at all — a
        # successful, empty, entirely misleading response.
        if self.model == "prebuilt-layout":
            url += "&features=keyValuePairs"
        return url

    def require(self) -> None:
        missing = [n for n, v in (("endpoint", self.endpoint),
                                  ("key", self.key)) if not str(v).strip()]
        if missing:
            raise DocIntError(
                "Azure Document Intelligence needs an "
                + " and a ".join(missing)
                + ".\n  Enter them in the sidebar under 'Azure Document "
                  "Intelligence'. They are held for this session only and "
                  "are never written to disk."
            )
        # An endpoint pasted without its scheme makes urllib raise
        # ValueError: unknown url type — which is NOT a URLError, so it
        # sails straight past the friendly handler in _request() and
        # surfaces as a raw traceback. Catch it here, where we can say
        # what is actually wrong.
        ep = str(self.endpoint).strip()
        if not (ep.startswith("https://") or ep.startswith("http://")):
            raise DocIntError(
                f"The endpoint must start with https:// — got {ep!r}.\n"
                f"  Copy the whole Endpoint value from the resource's "
                f"'Keys and Endpoint' blade,\n  which looks like "
                f"https://<resource>.cognitiveservices.azure.com/."
            )


def _request(url: str, settings: Settings, method: str = "GET",
             body: bytes | None = None, content_type: str | None = None):
    headers = {"Ocp-Apim-Subscription-Key": settings.key}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=settings.timeout) as resp:
            raw = resp.read()
            return resp.status, dict(resp.headers), (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:800]
        return e.code, dict(e.headers or {}), {"_error_body": detail}
    except urllib.error.URLError as e:
        raise DocIntError(
            f"Could not reach {url}\n  {e.reason}\n"
            f"  Check the endpoint, and check the resource is not firewalled "
            f"to a VNet you are not on."
        ) from e


def _explain(status: int, payload: dict, settings: Settings) -> str:
    body = payload.get("_error_body", json.dumps(payload)[:800])
    hint = ""
    if status == 404:
        hint = (f"\n  404 is almost always the ROUTE, not the model.\n"
                f"  api-version {settings.api_version} resolved to "
                f"/{settings.resolved_route()}/.\n"
                f"  Override the route in the sidebar's advanced expander.")
    elif status in (401, 403):
        hint = ("\n  401/403 is the key or the region. A key from a different "
                "Azure AI resource\n  returns exactly this.")
    elif status == 429:
        hint = ("\n  429 is the free (F0) tier rate limit. This client already "
                "retries and waits;\n  seeing it here means it gave up.")
    return f"HTTP {status} from Document Intelligence.{hint}\n  body: {body}"


DEFAULT_RETRY_WAIT = 20.0
MAX_RETRY_WAIT = 60.0


def _parse_retry_after(header_value: str | None) -> float:
    """Parse Retry-After, returning a wait that is always safe to sleep on.

    RFC 7231 allows two forms:
    - delta-seconds: "120" → float
    - HTTP-date: "Fri, 31 Dec 2027 23:59:59 GMT" → non-numeric

    We only handle delta-seconds. Anything else — a missing header, an
    HTTP-date, garbage — falls back to 20 seconds rather than raising.

    AND SO DOES ANYTHING NON-FINITE OR NEGATIVE. float() is happy to parse
    "-5", "nan" and "inf"; time.sleep() is not, and raises ValueError on the
    first two. That crash lands AFTER the document has been billed, so it
    costs money and loses the response. A header we cannot use is not a
    reason to sleep for a negative time — it is a reason to use the default.
    """
    if not header_value:
        return DEFAULT_RETRY_WAIT
    try:
        wait = float(header_value)
    except ValueError:
        return DEFAULT_RETRY_WAIT
    # NaN fails every comparison, including this one against itself, so it
    # has to be tested before the range check rather than inside it.
    if wait != wait or wait in (float("inf"), float("-inf")) or wait < 0:
        return DEFAULT_RETRY_WAIT
    return wait


def _bounded_wait(wait: float) -> float:
    """Clamp a wait to something time.sleep() will always accept.

    Belt and braces with _parse_retry_after: this is the value that actually
    reaches time.sleep(), and every sleep site in this module goes through
    it. min(wait, 60) alone does not protect against a negative or a NaN.
    """
    if wait != wait:                                    # NaN
        return DEFAULT_RETRY_WAIT
    return max(0.0, min(float(wait), MAX_RETRY_WAIT))


def _post_with_retry(url: str, data: bytes, settings: Settings, doc_id: str):
    """POST the PDF, retrying 429 and honouring Retry-After.

    Day-14 raised on 429 instead of retrying. That survives one document and
    does not survive ten: Day-15's thirty-two document run hit 429 five times
    and spent 94 seconds waiting. Retrying is not an optimisation here.
    """
    for attempt in range(6):
        status, headers, payload = _request(url, settings, "POST", data,
                                            "application/pdf")
        # Some api-versions reject raw binary and want base64 in a JSON body.
        # Both are documented; which one yours accepts is not obvious, so try
        # the other rather than making the learner debug a 415.
        if status == 415:
            body = json.dumps(
                {"base64Source": base64.b64encode(data).decode()}).encode()
            status, headers, payload = _request(url, settings, "POST", body,
                                                "application/json")
        if status != 429:
            return status, headers, payload
        retry_header = headers.get("Retry-After") or headers.get("retry-after")
        wait = _parse_retry_after(retry_header)
        time.sleep(_bounded_wait(wait))
    raise DocIntError(f"{doc_id}: still rate-limited after 6 attempts.")


def analyze(pdf: bytes, doc_id: str, settings: Settings) -> dict:
    """POST the PDF, poll the operation, return the raw payload."""
    settings.require()
    status, headers, payload = _post_with_retry(settings.analyze_url(), pdf,
                                                settings, doc_id)
    if status != 202:
        raise DocIntError(f"{doc_id}: {_explain(status, payload, settings)}")

    op = headers.get("Operation-Location") or headers.get("operation-location")
    if not op:
        raise DocIntError(
            f"{doc_id}: 202 Accepted with no Operation-Location header. "
            f"Nothing to poll.")

    deadline = time.time() + settings.timeout
    delay = 1.0
    while True:
        status, headers, payload = _request(op, settings)

        # THE POLL IS RATE-LIMITED TOO. Only the POST used to retry, and the
        # poll treated every non-200 as fatal. On the free (F0) tier — about
        # one request every twenty seconds — ten documents is ten POSTs plus
        # thirty to fifty polls, so a 429 on a poll is not an edge case, it
        # is the common case. Raising here also printed "this client already
        # retries and waits; seeing it here means it gave up", which was
        # simply untrue of a poll and sent the trainer to debug working code.
        # The analysis is already running and already billed: wait and ask
        # again.
        if status == 429:
            # FLOOR THE WAIT AT THE LOOP'S OWN BACKOFF. Retry-After: 0 is a
            # legal header and _bounded_wait passes it straight through, so
            # without this floor the branch sleeps for nothing, continues,
            # and never advances `delay` — a hot loop that re-GETs an
            # endpoint which has just said "rate limited", one round trip
            # apiece, for the whole timeout. Measured at 4.5 million
            # requests in three seconds against an injected transport. On a
            # free tier that deepens the throttle it is complaining about.
            wait = max(_bounded_wait(_parse_retry_after(
                headers.get("Retry-After") or headers.get("retry-after"))),
                delay)
            # The overall deadline still bounds this. A wait that would run
            # past it must be reported, not slept through — otherwise the
            # timeout the caller asked for silently stops applying.
            if time.time() + wait > deadline:
                raise DocIntError(
                    f"{doc_id}: rate-limited while polling, and waiting "
                    f"{wait:.0f}s to ask again would run past the "
                    f"{settings.timeout}s budget.\n"
                    f"  The analysis was accepted and billed; the client "
                    f"stopped waiting for it.\n"
                    f"  Raise the timeout, or move off the free (F0) tier.")
            time.sleep(wait)
            continue

        if status != 200:
            raise DocIntError(f"{doc_id}: poll {_explain(status, payload, settings)}")
        state = str(payload.get("status", "")).lower()
        if state == "succeeded":
            return payload
        if state == "failed":
            err = payload.get("error", payload)
            raise DocIntError(f"{doc_id}: analysis failed — {json.dumps(err)[:600]}")
        if time.time() > deadline:
            raise DocIntError(
                f"{doc_id}: still '{state}' after {settings.timeout}s.")
        time.sleep(delay)
        delay = min(delay * 1.5, 5.0)        # back off; do not hammer the poll


# ==========================================================================
# PREFLIGHT
#
# The cache is what makes this necessary. settings.require() runs inside
# analyze(), and a cache hit never reaches analyze() — so on a warm cache
# (a trainer calibrated before the session, which is the recommended order)
# ANY endpoint and ANY key produced a full "Live results loaded" board that
# had never touched a service. That silently fakes the exact thing the lab
# exists to demonstrate. This runs once, before the document loop, on cache
# hits as well as misses.
# ==========================================================================

def _preflight_verdict(status: int) -> str | None:
    """Classify a preflight status. None means proceed.

    FAIL ONLY ON EVIDENCE OF A REAL PROBLEM. 401 and 403 are proof the
    credentials are wrong. Nothing else is — least of all 404, which here
    may mean only that this resource does not expose the model-listing
    route. A preflight that invents failures is worse than no preflight:
    it blocks a working configuration on a guess. Anything that is not an
    auth rejection is "could not verify", and we proceed silently.
    """
    if status in (401, 403):
        return (f"Azure Document Intelligence rejected these credentials "
                f"(HTTP {status}).\n"
                f"  401/403 is the key or the region. A key from a different "
                f"Azure AI resource\n  returns exactly this — check the key "
                f"belongs to the resource this endpoint names.")
    return None


def preflight(settings: Settings, request_fn=None) -> None:
    """Fail before any document is sent — and before any cache is served.

    One cheap, unbilled, authenticated GET of the model list. It costs
    nothing, and it is the only thing standing between a warm cache and a
    board that reports live results without a live service.

    The key is never printed, logged or embedded in any message here.
    """
    settings.require()
    call = request_fn or _request
    base = settings.endpoint.rstrip("/")
    url = (f"{base}/{settings.resolved_route()}/documentModels"
           f"?api-version={settings.api_version}")
    # A connectivity failure raises DocIntError out of _request already,
    # with the endpoint and the reason — that IS evidence, and it should
    # stop us here rather than ten seconds into a billed run.
    status, _headers, _payload = call(url, settings)
    problem = _preflight_verdict(status)
    if problem:
        raise DocIntError(problem)


# ==========================================================================
# THE ADAPTER
#
# The only place in this codebase allowed to know Azure's response shape.
# Keep it that way and "could we swap in Textract" stays a one-file question.
# ==========================================================================
import render                                              # noqa: E402

# Printed label (cleaned, lowercased) -> our field name. Built from the
# labels render.py actually prints, plus the aliases a real form uses.
LABEL_MAP = {v.lower(): k for k, v in render.LABELS.items()}
LABEL_MAP.update({
    "claim reference": "claim_reference",
    "claim no.": "claim_reference",
    "policy number": "policy_number",
    "date of birth": "date_of_birth",
    "dob": "date_of_birth",
    "d.o.b": "date_of_birth",
    "name": "applicant_name",
    "insured name": "applicant_name",
    "sum insured": "sum_insured",
    "vehicle reg.": "vehicle_registration",
    "vehicle reg": "vehicle_registration",
    "incident date": "incident_date",
    "estimate total": "repair_estimate_total",
    "total": "repair_estimate_total",
    "parts": "parts_subtotal",
    "labour": "labour_subtotal",
    "driving licence number": "driving_licence_number",
})


def _clean_label(text: str) -> str:
    """The key as it appears on the page becomes the key our map is keyed on.

    STRIP THE COLON. DO NOT STRIP THE FULL STOP.

    That reads like a nitpick and it is not. Half the aliases above end in a
    full stop, because that is how people abbreviate on forms: "D.O.B.",
    "Sum Ins.", "Claim Ref.", "Policy No.". Tidy the trailing "." off those
    and they become labels the map has never heard of — and an unmapped label
    does not raise. The document still processes, the board still renders, and
    you have simply lost the applicant's date of birth.

    Then the live service showed us the other half of it. Document
    Intelligence reads a page as words, and the final full stop of an
    abbreviation is its own word. Rejoining them puts a space in front of it,
    so "D.O.B.:" printed on the form comes back as "D.O.B .:" — and stripping
    only the colon leaves "D.O.B .", which is just as unmapped as "D.O.B" was.
    Same silent miss, opposite cause. The mock cannot catch this: its labels
    are clean strings, and only the real service tokenises.

    So whitespace normalisation has to close a gap BEFORE punctuation as well
    as collapse the runs between words.
    """
    text = re.sub(r"\s+", " ", str(text)).strip()
    text = re.sub(r"\s+([.,;:])", r"\1", text)      # "D.O.B .:" -> "D.O.B.:"
    return text.rstrip(":").strip()


def _page_of(part: dict) -> int:
    regions = part.get("boundingRegions") or []
    if regions and isinstance(regions[0], dict):
        return int(regions[0].get("pageNumber", 1))
    return 1


def adapt(payload: dict, doc_id: str) -> tuple[list[dict], list[str]]:
    """analyzeResult -> the game's kv shape, plus the labels we could not map.

    Returns a LIST and preserves duplicate field names. Day-14 keyed its
    results by field name; here APP-0005's two conflicting repair estimates
    are the whole point of the document, and keying by name would silently
    merge them.
    """
    result = payload.get("analyzeResult", payload)
    kvs: list[dict] = []
    unmapped: list[str] = []

    for kp in result.get("keyValuePairs") or []:
        key = kp.get("key") or {}
        val = kp.get("value") or {}
        label = _clean_label(key.get("content", ""))
        if not label:
            continue
        field = LABEL_MAP.get(label.lower())
        if field is None:
            if label not in unmapped:
                unmapped.append(label)
            continue
        kvs.append({
            "field": field,
            "value": re.sub(r"\s+", " ", str(val.get("content", ""))).strip(),
            # A key-value pair always carries a confidence. A missing one is
            # a service change, not a zero — do not silently treat it as
            # garbage the thresholds will flag.
            "confidence": float(kp.get("confidence", 0.0)),
            "page": _page_of(key) or _page_of(val),
        })
    return kvs, unmapped


def pages_returned(payload: dict) -> int:
    result = payload.get("analyzeResult", payload)
    return len(result.get("pages") or [])
