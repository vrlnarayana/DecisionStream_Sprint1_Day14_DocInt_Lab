"""
azure_docint.py — the same ten documents, through the real service.

This is the ONE function the README promised you would have to change:

    mock_docint.analyze(doc_id)   ->   azure_docint.analyze(doc_id)

Everything downstream is untouched. `extract.py` does not import this file,
does not know it exists, and returns the same decision profile either way.
That is the test of whether your vendor boundary is in the right place.

WHAT IT ACTUALLY DOES
---------------------
    docs/APP-0003.pdf
      -> POST  {endpoint}/documentintelligence/documentModels/
               prebuilt-layout:analyze?api-version=2024-11-30&features=keyValuePairs
      -> 202 Accepted, with an Operation-Location header
      -> poll that URL until status == succeeded
      -> adapt analyzeResult.keyValuePairs into the lab's shape
      -> cache the raw response so the next run costs nothing

WHY REST AND NOT THE SDK
------------------------
So you can read the request. Everything here is standard library — no
`pip install azure-ai-documentintelligence`, no client class hiding the wire
format. When this 404s in front of a client you will want to know exactly what
was sent, and you will not get that from a stack trace inside an SDK.

Use the SDK in production. Read this once first.

THE TWO THINGS THAT WILL BITE YOU
---------------------------------
1. THE ROUTE MOVED.  api-version 2024-11-30 and later live under
   `/documentintelligence/`. Earlier versions live under `/formrecognizer/`.
   Mismatch the two and you get a 404 whose message talks about the model,
   which sends people hunting for a deployment problem that does not exist.

2. prebuilt-document IS GONE.  It was retired in the 2024-11-30 GA API. Its
   key-value pair extraction moved into `prebuilt-layout` behind an opt-in
   `features=keyValuePairs` flag. Omit the flag and layout returns HTTP 200
   with no keyValuePairs at all — a successful, empty, entirely misleading
   response. Nothing errors. You just get zero fields.

Both of those are the README's "verify the current SDK, model name and API
version before you build" made concrete. Checking is the habit.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import make_pdfs
import mock_docint
from config import ConfigError, Settings, get_settings


class AzureDocIntError(RuntimeError):
    """Anything that should stop the run: bad config, HTTP error, failed
    analysis. We do not paper over these — a half-processed batch that looks
    complete is worse than a batch that stopped."""


# Ground truth, the document list and the notes are properties of the FORM,
# not of whoever read it. They are identical for both providers, which is what
# makes the two runs comparable at all.
all_ids = mock_docint.all_ids
truth = mock_docint.truth
note = mock_docint.note
DOCUMENTS = mock_docint.DOCUMENTS


# ==========================================================================
# 1. HTTP
# ==========================================================================
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
        raise AzureDocIntError(
            f"Could not reach {url}\n  {e.reason}\n"
            f"  Check AZURE_DOCINT_ENDPOINT, and check the resource is not "
            f"firewalled to a VNet you are not on."
        ) from e


def _explain(status: int, payload: dict, settings: Settings) -> str:
    body = payload.get("_error_body", json.dumps(payload)[:800])
    hint = ""
    if status == 404:
        hint = (f"\n  404 is almost always the ROUTE, not the model.\n"
                f"  api-version {settings.api_version} resolved to "
                f"/{settings.resolved_route()}/.\n"
                f"  Set AZURE_DOCINT_ROUTE=formrecognizer (older) or "
                f"=documentintelligence (2024-11-30+) to force it.")
    elif status in (401, 403):
        hint = ("\n  401/403 is the key or the region. Confirm AZURE_DOCINT_KEY "
                "belongs to the resource\n  at AZURE_DOCINT_ENDPOINT — a key from "
                "a different Azure AI resource returns exactly this.")
    elif status == 429:
        hint = ("\n  429 is the free (F0) tier rate limit — 1 request every ~20s. "
                "Set DOCINT_POLL_GAP\n  higher, or move the resource to S0.")
    return f"HTTP {status} from Document Intelligence.{hint}\n  body: {body}"


def _analyze_raw(pdf: Path, settings: Settings) -> dict:
    """POST the PDF, poll the operation, return the raw analyzeResult payload."""
    url = settings.analyze_url()
    data = pdf.read_bytes()

    status, headers, payload = _request(url, settings, "POST", data, "application/pdf")

    # Some api-versions reject raw binary and want the bytes in a JSON body.
    # Both are documented; which one your version accepts is not obvious, so
    # try the other rather than making the learner debug a 415.
    if status == 415:
        body = json.dumps({"base64Source": base64.b64encode(data).decode()}).encode()
        status, headers, payload = _request(url, settings, "POST", body, "application/json")

    if status != 202:
        raise AzureDocIntError(f"{pdf.name}: {_explain(status, payload, settings)}")

    op = headers.get("Operation-Location") or headers.get("operation-location")
    if not op:
        raise AzureDocIntError(
            f"{pdf.name}: 202 Accepted with no Operation-Location header. "
            f"Nothing to poll."
        )

    deadline = time.time() + settings.timeout
    delay = 1.0
    while True:
        status, _, payload = _request(op, settings)
        if status != 200:
            raise AzureDocIntError(f"{pdf.name}: poll {_explain(status, payload, settings)}")
        state = str(payload.get("status", "")).lower()
        if state == "succeeded":
            return payload
        if state == "failed":
            err = payload.get("error", payload)
            raise AzureDocIntError(f"{pdf.name}: analysis failed — {json.dumps(err)[:600]}")
        if time.time() > deadline:
            raise AzureDocIntError(
                f"{pdf.name}: still '{state}' after {settings.timeout}s. "
                f"Raise AZURE_DOCINT_TIMEOUT."
            )
        time.sleep(delay)
        delay = min(delay * 1.5, 5.0)      # back off; do not hammer the poll URL


# ==========================================================================
# 2. THE ADAPTER
#
# This is where a vendor's response shape becomes the lab's response shape,
# and it is the only place in the codebase allowed to know about either.
# ==========================================================================
SELECTED = {":selected:", "selected", "[x]", "x", "yes", "true", "☒"}
UNSELECTED = {":unselected:", "unselected", "[ ]", "[]", "no", "false", "☐"}


def _clean_label(text: str) -> str:
    """Document Intelligence returns the key as it appears on the page, which
    usually includes the trailing colon and sometimes a line break. The label
    map in extract.py is keyed on the words alone, so the punctuation is
    stripped HERE — at the vendor boundary — and not by loosening the map.

    STRIP THE COLON. DO NOT STRIP THE FULL STOP.

    That looks like a nitpick and it is not. Half the aliases in LABEL_MAP end
    in a full stop, because that is how people abbreviate on forms: "D.O.B.",
    "Sum Ins.", "Claim Ref.", "Policy No.". Tidying the trailing "." off those
    turns them into labels the map has never heard of, and an unmapped label
    does not raise — it lands in `dropped` as "unmapped_label" and the field
    reports as MISSING. The document still processes. The report still renders.
    You have simply lost the applicant's date of birth.

    This exact bug was in this file and the self-test is what found it.

    THEN THE REAL SERVICE SHOWED US THE OTHER HALF OF IT.

    Document Intelligence reads a page as words, and the final full stop of an
    abbreviation is its own word. Rejoining them puts a space in front of it,
    so "D.O.B.:" printed on the form comes back as "D.O.B .:" — and stripping
    only the colon leaves "D.O.B .", which is just as unmapped as "D.O.B" was.
    Same silent MISSING, opposite cause. The mock could not have caught this:
    its labels are clean strings, and only the live service tokenises.

    So the whitespace normalisation has to close up a gap before punctuation
    as well as collapse the runs between words."""
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([.,;:])", r"\1", text)     # "D.O.B .:" -> "D.O.B.:"
    return text.rstrip(":").strip()


def _normalise_selection(value: str) -> str:
    """A selection mark reaches us as ':selected:' from the service and as
    '[X]' from a rendered form. extract.py understands one word for this, so
    the translation happens here rather than teaching the pipeline three."""
    v = value.strip().lower()
    if v in SELECTED:
        return "selected"
    if v in UNSELECTED:
        return "unselected"
    return value


def _page_of(part: dict) -> int:
    regions = part.get("boundingRegions") or []
    if regions and isinstance(regions[0], dict):
        return int(regions[0].get("pageNumber", 1))
    return 1


def adapt(payload: dict, doc_id: str) -> dict:
    """analyzeResult -> the lab's {id, pages, key_value_pairs} shape."""
    result = payload.get("analyzeResult", payload)
    pairs = []
    for kp in result.get("keyValuePairs") or []:
        key = kp.get("key") or {}
        val = kp.get("value") or {}
        label = _clean_label(key.get("content", ""))
        if not label:
            continue
        pairs.append({
            "label": label,
            "value": _normalise_selection(re.sub(r"\s+", " ", val.get("content", "")).strip()),
            # A key-value pair always carries a confidence. A missing one is a
            # service change, not a zero — do not silently treat it as garbage.
            "confidence": float(kp.get("confidence", 0.0)),
            "page": _page_of(key) or _page_of(val),
        })
    return {
        "id": doc_id,
        "pages": len(result.get("pages") or []) or 1,
        "key_value_pairs": pairs,
    }


# ==========================================================================
# 3. THE PROVIDER INTERFACE — same three functions as mock_docint
# ==========================================================================
def _cache_path(settings: Settings, doc_id: str) -> Path:
    return settings.out_dir / "azure" / "di_raw" / f"{doc_id}.json"


# --------------------------------------------------------------------------
# PAGES THE SERVICE DECLINED TO READ
#
# A free-tier (F0) Document Intelligence resource analyses the FIRST TWO PAGES
# of a document and stops. It does not tell you. You get HTTP 200, no error, no
# `warnings` member, and a `pages` array that simply ends early — so every
# field printed on page three arrives as a MISSING field, indistinguishable
# from a label the model failed to read.
#
# That distinction is the whole point of the exercise. "The extractor could not
# read this field" and "nobody ever showed the extractor this page" are
# different failures with different fixes, and a lab that silently merges them
# teaches the wrong lesson. So we compare what we sent against what came back.
#
# Measured against the live service on 2026-08-16: the same text run returned
# cleanly as page 1 of a one-page PDF and disappeared as page 3 of a
# three-page PDF. Upgrade the resource to S0 to lift the limit.
# --------------------------------------------------------------------------
PAGE_TRUNCATIONS: list[str] = []


def _pdf_page_count(data: bytes) -> int | None:
    """How many pages the PDF we posted declares, read from the page tree.
    None when it cannot be determined — an unknown count must never be
    reported as a discrepancy."""
    m = re.search(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", data, re.S)
    if not m:
        return None
    try:
        return int(m.group(1)) or None
    except ValueError:
        return None


def _truncation_note(doc_id: str, declared: int | None, returned: int) -> str | None:
    """The sentence to show a learner when pages went missing, or None."""
    if not declared or returned >= declared:
        return None
    return (f"{doc_id}: sent {declared} pages, the service analysed {returned}. "
            f"Fields on page{'s' if declared - returned > 1 else ''} "
            f"{', '.join(str(p) for p in range(returned + 1, declared + 1))} "
            f"were never read — they are NOT extraction failures.")


def _check_pages(doc_id: str, pdf: Path, returned: int) -> None:
    if not pdf.exists():
        return
    note = _truncation_note(doc_id, _pdf_page_count(pdf.read_bytes()), returned)
    if note and note not in PAGE_TRUNCATIONS:
        PAGE_TRUNCATIONS.append(note)


def analyze(doc_id: str, settings: Settings | None = None) -> dict:
    """
    The real call. Same signature, same return shape, as mock_docint.analyze.

    Raw responses are cached under out/azure/di_raw/. The cache is what lets
    you re-run the three policies over real data without paying for the OCR
    three times — the extraction is deterministic, the OCR is what costs money.
    Delete the directory, or set DOCINT_USE_CACHE=false, to force a fresh read.
    """
    settings = settings or get_settings("azure")
    settings.require_azure()

    if doc_id not in mock_docint.all_ids():
        raise KeyError(f"No such document: {doc_id}")

    pdf = make_pdfs.pdf_path(settings.doc_dir, doc_id)

    cache = _cache_path(settings, doc_id)
    if settings.use_cache and cache.exists():
        adapted = adapt(json.loads(cache.read_text()), doc_id)
        # The cached run lost the same pages the billed one did. Re-checking
        # here keeps the warning on every run, not just the one that paid.
        _check_pages(doc_id, pdf, adapted["pages"])
        return adapted

    if not pdf.exists():
        raise AzureDocIntError(
            f"{pdf} does not exist.\n"
            f"  Render the forms first:  python3 run_lab.py --make-pdfs"
        )

    payload = _analyze_raw(pdf, settings)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(payload, indent=2))
    adapted = adapt(payload, doc_id)
    _check_pages(doc_id, pdf, adapted["pages"])
    return adapted


def preflight(settings: Settings) -> None:
    """Fail before the first billed call, not halfway through the batch."""
    settings.require_azure()
    missing = [d for d in mock_docint.all_ids()
               if not make_pdfs.pdf_path(settings.doc_dir, d).exists()]
    if missing:
        raise AzureDocIntError(
            f"{len(missing)} of {len(mock_docint.all_ids())} PDFs are missing "
            f"from {settings.doc_dir}.\n"
            f"  Render them:  python3 run_lab.py --make-pdfs"
        )
    if len(mock_docint.all_ids()) > settings.max_documents:
        raise ConfigError(
            f"{len(mock_docint.all_ids())} documents exceeds DOCINT_MAX_DOCUMENTS="
            f"{settings.max_documents}. Raise it deliberately."
        )


# ==========================================================================
# 4. WHERE THE SERVICE AND THE MOCK DISAGREE
#
# This is the most useful artefact of the azure run, and the reason to do the
# run at all. The mock's confidences are numbers somebody chose to make a
# teaching point. These are numbers the service produced. Quote these.
# ==========================================================================
def confidence_delta(settings: Settings) -> str:
    rows = []
    for doc in mock_docint.DOCUMENTS:
        try:
            real = analyze(doc["id"], settings)
        except (AzureDocIntError, KeyError):
            continue
        got = {kv["label"].strip().lower(): kv for kv in real["key_value_pairs"]}
        for kv in doc["kv"]:
            label = kv["label"].strip().lower()
            r = got.get(label)
            rows.append((doc["id"], kv["label"], kv["confidence"],
                         r["confidence"] if r else None,
                         r["value"] if r else None, kv["value"]))

    found = [r for r in rows if r[3] is not None]
    md = [
        "# Mock confidence vs Azure confidence", "",
        "The mock's numbers were chosen to make a teaching point. These were",
        "produced by the service. When you present this lab, quote the right",
        "column and say where it came from.", "",
        f"Fields in the mock: **{len(rows)}**  ·  "
        f"returned by the service: **{len(found)}**  ·  "
        f"not returned: **{len(rows) - len(found)}**", "",
    ]
    if found:
        avg_mock = sum(r[2] for r in found) / len(found)
        avg_real = sum(r[3] for r in found) / len(found)
        md += [f"Mean confidence — mock **{avg_mock:.3f}**, service **{avg_real:.3f}**.", ""]
    md += ["| Doc | Label | Mock conf | Azure conf | Mock value | Azure value |",
           "|---|---|---|---|---|---|"]
    for did, label, mconf, rconf, rval, mval in rows:
        md.append(f"| {did} | {label} | {mconf:.2f} | "
                  f"{'—' if rconf is None else f'{rconf:.2f}'} | `{mval}` | "
                  f"{'*not returned*' if rval is None else f'`{rval}`'} |")

    md += ["", "## Read this before you draw a conclusion", "",
           "**A field the service did not return is not a service failure.** "
           "Key-value pair extraction finds the pairs it recognises as pairs. "
           "A label the model does not read as a label produces no pair at all "
           "— which lands in `missing_required`, not in the review queue. Those "
           "are different failures and they need different handling.", "",
           "**Check whether the page was read at all before you blame the "
           "extraction.** On a free-tier (F0) resource the service analyses the "
           "first two pages and stops — HTTP 200, no error, no warning, a "
           "`pages` array that just ends. Every field printed after page two "
           "then reports as *not returned*, which looks identical to a label "
           "the model failed to recognise and is nothing of the kind. The run "
           "prints a **PAGES THE SERVICE DID NOT READ** block when it detects "
           "this; if that block is present, the rows below for those documents "
           "are measuring the pricing tier, not the model.", "",
           "**These PDFs are digitally generated.** The text is embedded, not "
           "scanned, so the service reads it far more confidently than it would "
           "read the forms this lab is modelling. Confidence measured here is a "
           "ceiling, not an estimate. To get a real number, run the client's own "
           "scans through the same pipeline — that is the first thing to ask for.", "",
           "**The OCR-confusion values survive the round trip.** `CLM-2026-45O3` "
           "was rendered with the letter O and comes back with the letter O, at "
           "high confidence. That is the entire lesson holding up against the "
           "real service: nothing errored, nothing scored low, and only the "
           "pattern check knows the value is wrong.", ""]
    return "\n".join(md)
