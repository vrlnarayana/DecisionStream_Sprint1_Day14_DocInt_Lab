"""
game.py — the Threshold Game.

THE POINT OF THE GAME
---------------------
You are given ten case application forms as Document Intelligence returned
them, and one job: decide which extracted values to ACCEPT automatically and
which to FLAG for a human.

You have two kinds of control:

    THRESHOLDS   a confidence bar per field. Above it, accept. Below, flag.
    CHECKS       validators that can override a high confidence when the
                 value does not look like what it claims to be.

The scoring is deliberately lopsided:

    +10   a correct value accepted automatically      (the work you saved)
    -100  a WRONG value accepted automatically        (the case you broke)
     -2   a value sent to a human                     (thirty seconds)
    -20   a required field missing and not flagged    (a silent gap)

That -100 is not arbitrary. A flag costs a handler half a minute. A wrongly
accepted policy number attaches a claim to a policy that does not exist, and
nobody finds out until somebody downstream notices.

WHAT YOU WILL DISCOVER
----------------------
Threshold tuning alone cannot win this game. There is a ceiling, and it is
well below the score you get by turning the checks on. That is the whole
lesson and the leaderboard is what proves it, rather than a slide asserting
it.

Confidence is the model's certainty about what it SAW.
It is not a statement about whether the value is CORRECT.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime


# ==========================================================================
# SCORING
# ==========================================================================
POINTS = {
    "correct_accept": 10,
    "wrong_accept": -150,
    "flag": -2,
    "missing_unflagged": -20,
}

FIELDS = [
    "claim_reference", "policy_number", "applicant_name", "date_of_birth",
    "vehicle_registration", "incident_date", "sum_insured", "excess",
    "repair_estimate_total",
]

FIELD_KIND = {
    "claim_reference": "identifier",
    "policy_number": "identifier",
    "vehicle_registration": "identifier",
    "applicant_name": "text",
    "date_of_birth": "date",
    "incident_date": "date",
    "sum_insured": "money",
    "excess": "money",
    "repair_estimate_total": "money",
}

PATTERNS = {
    "claim_reference": r"^CLM-\d{4}-\d{4}$",
    "policy_number": r"^MOT-\d{7}$",
    "vehicle_registration": r"^[A-Z]{2}\d{2}\s?[A-Z]{3}$",
}

REQUIRED = ["claim_reference", "policy_number", "date_of_birth",
            "vehicle_registration", "incident_date", "sum_insured",
            "excess", "repair_estimate_total"]

CHECKS = {
    "pattern":      "Identifiers must match their expected shape",
    "ocr_repair":   "Flag a value that only matches after repairing OCR confusions",
    "arithmetic":   "Line items must sum to the stated total",
    "date_sanity":  "Reject impossible and ambiguous dates",
    "cross_field":  "Estimate vs sum insured, excess vs estimate, age at incident",
    "conflict":     "Never silently pick between two values for the same field",
    "scope_gate":   "Identity document data never reaches the profile",
}

DEFAULT_THRESHOLDS = {f: 0.85 for f in FIELDS}
DEFAULT_CHECKS = {k: False for k in CHECKS}


# ==========================================================================
# THE TEN FORMS
# ==========================================================================
def _kv(label, value, conf, page=1):
    return {"field": label, "value": value, "confidence": conf, "page": page}


DOCUMENTS: list[dict] = [
{
  "id": "APP-0001", "note": "clean baseline",
  "hint": "Everything present, everything confident, everything correct.",
  "kv": [
    _kv("claim_reference", "CLM-2026-4471", 0.97),
    _kv("policy_number", "MOT-4471902", 0.96),
    _kv("applicant_name", "[PERSON_01]", 0.95),
    _kv("date_of_birth", "1987-04-12", 0.94),
    _kv("vehicle_registration", "KP19 TRX", 0.96),
    _kv("incident_date", "2026-03-14", 0.95),
    _kv("sum_insured", "9000.00", 0.93),
    _kv("excess", "350.00", 0.94),
    _kv("repair_estimate_total", "12400.00", 0.95, 2),
  ],
  "truth": {"claim_reference": "CLM-2026-4471", "policy_number": "MOT-4471902",
            "applicant_name": "[PERSON_01]", "date_of_birth": "1987-04-12",
            "vehicle_registration": "KP19 TRX", "incident_date": "2026-03-14",
            "sum_insured": 9000.0, "excess": 350.0,
            "repair_estimate_total": 12400.0},
},
{
  "id": "APP-0002", "note": "lower confidence, all correct",
  "hint": "A slightly worse scan. Every value is right. Set your bar too high "
          "and you pay a flag for each one.",
  "kv": [
    _kv("claim_reference", "CLM-2026-4482", 0.91),
    _kv("policy_number", "MOT-4482901", 0.90),
    _kv("applicant_name", "[PERSON_03]", 0.87),
    _kv("date_of_birth", "1991-09-30", 0.88),
    _kv("vehicle_registration", "LT21 WNB", 0.90),
    _kv("incident_date", "2026-04-02", 0.89),
    _kv("sum_insured", "9100.00", 0.88),
    _kv("excess", "250.00", 0.89),
    _kv("repair_estimate_total", "6220.00", 0.90, 2),
  ],
  "truth": {"claim_reference": "CLM-2026-4482", "policy_number": "MOT-4482901",
            "applicant_name": "[PERSON_03]", "date_of_birth": "1991-09-30",
            "vehicle_registration": "LT21 WNB", "incident_date": "2026-04-02",
            "sum_insured": 9100.0, "excess": 250.0,
            "repair_estimate_total": 6220.0},
},
{
  "id": "APP-0003", "note": "handwritten — letter O for zero",
  "hint": "THE ONE. High confidence, wrong characters. No threshold below "
          "0.89 catches the policy number, and above 0.89 you flag most of "
          "the clean documents too.",
  "kv": [
    _kv("claim_reference", "CLM-2026-45O3", 0.88),
    _kv("policy_number", "MOT-45O3901", 0.86),
    _kv("applicant_name", "[PERSON_04]", 0.71),
    _kv("date_of_birth", "1979-11-O5", 0.69),
    _kv("vehicle_registration", "BD1B MHK", 0.83),
    _kv("incident_date", "2026-03-09", 0.90),
    _kv("sum_insured", "775O.OO", 0.74),
    _kv("excess", "3OO.OO", 0.72),
    _kv("repair_estimate_total", "341O.OO", 0.79, 2),
  ],
  "truth": {"claim_reference": "CLM-2026-4503", "policy_number": "MOT-4503901",
            "applicant_name": "[PERSON_04]", "date_of_birth": "1979-11-05",
            "vehicle_registration": "BD18 MHK", "incident_date": "2026-03-09",
            "sum_insured": 7750.0, "excess": 300.0,
            "repair_estimate_total": 3410.0},
},
{
  "id": "APP-0004", "note": "two required fields absent",
  "hint": "Missing is not the same as wrong. An absent required field that "
          "nobody flags is a silent gap.",
  "kv": [
    _kv("claim_reference", "CLM-2026-4517", 0.96),
    _kv("policy_number", "MOT-4517901", 0.94),
    _kv("applicant_name", "[PERSON_05]", 0.93),
    _kv("vehicle_registration", "GF22 PLT", 0.95),
    _kv("incident_date", "2026-03-28", 0.92),
    _kv("sum_insured", "16000.00", 0.93),
    _kv("repair_estimate_total", "15900.00", 0.94),
  ],
  "truth": {"claim_reference": "CLM-2026-4517", "policy_number": "MOT-4517901",
            "applicant_name": "[PERSON_05]", "vehicle_registration": "GF22 PLT",
            "incident_date": "2026-03-28", "sum_insured": 16000.0,
            "repair_estimate_total": 15900.0},
},
{
  "id": "APP-0005", "note": "same field twice, different values",
  "hint": "An original estimate and a supplementary one. Choosing between "
          "them is a handler decision, not yours.",
  "kv": [
    _kv("claim_reference", "CLM-2026-4526", 0.96),
    _kv("policy_number", "MOT-4526901", 0.95),
    _kv("applicant_name", "[PERSON_06]", 0.94),
    _kv("date_of_birth", "1984-02-17", 0.93),
    _kv("vehicle_registration", "HN20 XCE", 0.95),
    _kv("incident_date", "2026-04-03", 0.94),
    _kv("sum_insured", "6400.00", 0.92),
    _kv("excess", "250.00", 0.93),
    _kv("repair_estimate_total", "2150.00", 0.94, 2),
    _kv("repair_estimate_total", "2450.00", 0.91, 3),
  ],
  "truth": {"claim_reference": "CLM-2026-4526", "policy_number": "MOT-4526901",
            "applicant_name": "[PERSON_06]", "date_of_birth": "1984-02-17",
            "vehicle_registration": "HN20 XCE", "incident_date": "2026-04-03",
            "sum_insured": 6400.0, "excess": 250.0,
            "repair_estimate_total": "CONFLICT"},
},
{
  "id": "APP-0006", "note": "line items do not sum to the total",
  "hint": "Parts 2,900 plus labour 1,480 is 4,380. The form says 4,880. "
          "Confidence is high on all three.",
  "kv": [
    _kv("claim_reference", "CLM-2026-4552", 0.96),
    _kv("policy_number", "MOT-4552901", 0.95),
    _kv("applicant_name", "[PERSON_09]", 0.93),
    _kv("date_of_birth", "1972-05-08", 0.92),
    _kv("vehicle_registration", "PC17 KDN", 0.94),
    _kv("incident_date", "2026-03-12", 0.93),
    _kv("sum_insured", "5600.00", 0.91),
    _kv("excess", "250.00", 0.92),
    _kv("parts_subtotal", "2900.00", 0.93, 2),
    _kv("labour_subtotal", "1480.00", 0.92, 2),
    _kv("repair_estimate_total", "4880.00", 0.95, 2),
  ],
  "truth": {"claim_reference": "CLM-2026-4552", "policy_number": "MOT-4552901",
            "applicant_name": "[PERSON_09]", "date_of_birth": "1972-05-08",
            "vehicle_registration": "PC17 KDN", "incident_date": "2026-03-12",
            "sum_insured": 5600.0, "excess": 250.0,
            "repair_estimate_total": "ARITHMETIC_MISMATCH"},
},
{
  "id": "APP-0007", "note": "ambiguous date format",
  "hint": "04/03/2026 — is that 4 March or 3 April? Guessing gets a claim "
          "dated four weeks in the wrong direction.",
  "kv": [
    _kv("claim_reference", "CLM-2026-4561", 0.96),
    _kv("policy_number", "MOT-4561901", 0.95),
    _kv("applicant_name", "[PERSON_10]", 0.94),
    _kv("date_of_birth", "12/07/1993", 0.90),
    _kv("vehicle_registration", "QT19 LFS", 0.95),
    _kv("incident_date", "04/03/2026", 0.91),
    _kv("sum_insured", "9800.00", 0.92),
    _kv("excess", "400.00", 0.93),
    _kv("repair_estimate_total", "13200.00", 0.94, 2),
  ],
  "truth": {"claim_reference": "CLM-2026-4561", "policy_number": "MOT-4561901",
            "applicant_name": "[PERSON_10]", "date_of_birth": "AMBIGUOUS",
            "vehicle_registration": "QT19 LFS", "incident_date": "AMBIGUOUS",
            "sum_insured": 9800.0, "excess": 400.0,
            "repair_estimate_total": 13200.0},
},
{
  "id": "APP-0008", "note": "identity document attached",
  "hint": "Page 3 is a driving licence. Document Intelligence read it "
          "confidently. Should any of it be in your decision profile?",
  "kv": [
    _kv("claim_reference", "CLM-2026-4548", 0.96),
    _kv("policy_number", "MOT-4548901", 0.95),
    _kv("applicant_name", "[PERSON_08]", 0.94),
    _kv("date_of_birth", "1988-12-03", 0.93),
    _kv("vehicle_registration", "MS21 TVB", 0.95),
    _kv("incident_date", "2026-03-25", 0.94),
    _kv("sum_insured", "8900.00", 0.92),
    _kv("excess", "300.00", 0.93),
    _kv("repair_estimate_total", "7630.00", 0.94, 2),
    _kv("driving_licence_number", "SMITH912035AB9CD", 0.91, 3),
    _kv("licence_postcode", "LS8 3QT", 0.92, 3),
  ],
  "truth": {"claim_reference": "CLM-2026-4548", "policy_number": "MOT-4548901",
            "applicant_name": "[PERSON_08]", "date_of_birth": "1988-12-03",
            "vehicle_registration": "MS21 TVB", "incident_date": "2026-03-25",
            "sum_insured": 8900.0, "excess": 300.0,
            "repair_estimate_total": 7630.0},
},
{
  "id": "APP-0009", "note": "estimate far above the sum insured",
  "hint": "An estimate of 22,000 against a sum insured of 6,100. Not "
          "impossible. It should never pass without somebody looking.",
  "kv": [
    _kv("claim_reference", "CLM-2026-4573", 0.95),
    _kv("policy_number", "MOT-4573901", 0.94),
    _kv("applicant_name", "[PERSON_11]", 0.93),
    _kv("date_of_birth", "1990-01-22", 0.92),
    _kv("vehicle_registration", "ZB20 HRC", 0.94),
    _kv("incident_date", "2026-04-06", 0.93),
    _kv("sum_insured", "6100.00", 0.92),
    _kv("excess", "350.00", 0.93),
    _kv("repair_estimate_total", "22000.00", 0.94, 2),
  ],
  "truth": {"claim_reference": "CLM-2026-4573", "policy_number": "MOT-4573901",
            "applicant_name": "[PERSON_11]", "date_of_birth": "1990-01-22",
            "vehicle_registration": "ZB20 HRC", "incident_date": "2026-04-06",
            "sum_insured": 6100.0, "excess": 350.0,
            "repair_estimate_total": "IMPLAUSIBLE"},
},
{
  "id": "APP-0010", "note": "poor scan, low confidence throughout",
  "hint": "The safest document in the set. It says it is unsure, so a "
          "sensible threshold flags everything and nothing gets through wrong.",
  "kv": [
    _kv("claim_reference", "CLM-2026-4590", 0.64),
    _kv("policy_number", "MOT-4590901", 0.61),
    _kv("applicant_name", "[PERSON_12]", 0.58),
    _kv("date_of_birth", "1995-06-14", 0.55),
    _kv("vehicle_registration", "RV70 KDS", 0.67),
    _kv("incident_date", "2026-04-11", 0.62),
    _kv("sum_insured", "7300.00", 0.59),
    _kv("excess", "300.00", 0.60),
    _kv("repair_estimate_total", "5940.00", 0.63, 3),
  ],
  "truth": {"claim_reference": "CLM-2026-4590", "policy_number": "MOT-4590901",
            "applicant_name": "[PERSON_12]", "date_of_birth": "1995-06-14",
            "vehicle_registration": "RV70 KDS", "incident_date": "2026-04-11",
            "sum_insured": 7300.0, "excess": 300.0,
            "repair_estimate_total": 5940.0},
},
]

FORBIDDEN_PREFIXES = ("driving_licence", "licence_", "passport", "national_insurance")


# ==========================================================================
# NORMALISATION
# ==========================================================================
OCR_MAP = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1",
                         "B": "8", "S": "5"})


def norm_money(raw: str) -> tuple[float | None, list[str]]:
    notes = []
    s = str(raw).strip()
    if re.search(r"[OoIl]", s):
        notes.append("ocr_letter_in_number")
        s = s.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1")
    s = re.sub(r"[^0-9.]", "", s)
    try:
        return float(s), notes
    except ValueError:
        return None, notes + ["unparseable"]


def norm_date(raw: str, strict: bool = True) -> tuple[str | None, list[str]]:
    """
    strict=True   an ambiguous date returns nothing, and gets flagged.
    strict=False  an ambiguous date is GUESSED, day-first, and flows through
                  looking perfectly ordinary. That is what happens when you
                  do not have the date sanity check turned on.
    """
    notes = []
    s = str(raw).strip()
    if re.search(r"[OoIl]", s):
        notes.append("ocr_letter_in_number")
        s = s.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return s, notes
        except ValueError:
            return None, notes + ["impossible_date"]
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        if a <= 12 and b <= 12 and a != b:
            if strict:
                return None, notes + ["ambiguous_date"]
            # guess day-first and say nothing about it
            try:
                return date(y, b, a).isoformat(), notes + ["ambiguous_date_guessed"]
            except ValueError:
                return None, notes + ["impossible_date"]
        d, mth = (a, b) if a > 12 else (b, a)
        try:
            return date(y, mth, d).isoformat(), notes
        except ValueError:
            return None, notes + ["impossible_date"]
    return None, notes + ["unrecognised_date"]


def norm_identifier(raw: str, fname: str) -> tuple[str, list[str]]:
    notes = []
    s = str(raw).strip().upper()
    pat = PATTERNS.get(fname)
    if pat and not re.match(pat, s):
        repaired = s.translate(OCR_MAP)
        if re.match(pat, repaired):
            return repaired, ["ocr_repaired_to_pattern"]
        notes.append("pattern_mismatch")
    return s, notes


# ==========================================================================
# THE PIPELINE — this is what your settings control
# ==========================================================================
@dataclass
class FieldResult:
    name: str
    raw: str
    value: object
    confidence: float
    page: int
    verdict: str                 # accept | flag
    notes: list[str] = dc_field(default_factory=list)
    correct: bool | None = None
    points: int = 0


def process(doc: dict, thresholds: dict, checks: dict) -> dict:
    fields: dict[str, FieldResult] = {}
    dropped: list[dict] = []
    extras: dict[str, float] = {}

    for kv in doc["kv"]:
        fname = kv["field"]

        # scope gate
        if any(fname.startswith(p) for p in FORBIDDEN_PREFIXES):
            if checks.get("scope_gate"):
                dropped.append({"field": fname, "page": kv["page"],
                                "reason": "identity_evidence_out_of_scope"})
                continue
            # gate off: it flows through as an ordinary field
            fields[fname] = FieldResult(fname, str(kv["value"]), str(kv["value"]),
                                        kv["confidence"], kv["page"], "accept",
                                        ["identity_data_in_profile"])
            continue

        if fname in ("parts_subtotal", "labour_subtotal"):
            v, _ = norm_money(kv["value"])
            if v is not None:
                extras[fname] = v
            continue

        if fname not in FIELDS:
            continue

        kind = FIELD_KIND[fname]
        if kind == "money":
            val, notes = norm_money(kv["value"])
        elif kind == "date":
            val, notes = norm_date(kv["value"], strict=bool(checks.get("date_sanity")))
        elif kind == "identifier":
            val, notes = norm_identifier(kv["value"], fname)
        else:
            val, notes = str(kv["value"]).strip(), []

        verdict = "accept" if kv["confidence"] >= thresholds.get(fname, 0.85) else "flag"

        # ---- CHECKS can override a high confidence ----
        if checks.get("pattern") and "pattern_mismatch" in notes:
            verdict = "flag"
        if checks.get("ocr_repair") and "ocr_repaired_to_pattern" in notes:
            verdict = "flag"
        if checks.get("date_sanity") and any(
                n in notes for n in ("ambiguous_date", "impossible_date",
                                     "unrecognised_date")):
            verdict = "flag"
        if val is None and verdict == "accept":
            # cannot accept something we could not parse
            verdict = "flag"
            notes.append("unparseable_value")

        if fname in fields:
            prev = fields[fname]
            if prev.value != val:
                if checks.get("conflict"):
                    prev.verdict = "flag"
                    prev.notes.append("conflicting_values")
                    prev.value = "CONFLICT"
                    continue
                # conflict check off: last one silently wins
                notes.append("silently_overwrote_earlier_value")
        fields[fname] = FieldResult(fname, str(kv["value"]), val,
                                    kv["confidence"], kv["page"], verdict, notes)

    # ---- cross-field checks ----
    def v(n):
        return fields[n].value if n in fields else None

    if checks.get("arithmetic") and "parts_subtotal" in extras and \
            "labour_subtotal" in extras and isinstance(v("repair_estimate_total"), float):
        if abs(extras["parts_subtotal"] + extras["labour_subtotal"]
               - v("repair_estimate_total")) > 0.01:
            f = fields["repair_estimate_total"]
            f.verdict = "flag"
            f.value = "CONFLICT"
            f.notes.append("arithmetic_mismatch")

    if checks.get("cross_field"):
        est, sins, exc = v("repair_estimate_total"), v("sum_insured"), v("excess")
        if isinstance(est, float) and isinstance(sins, float) and est > sins * 1.5:
            f = fields["repair_estimate_total"]
            f.verdict = "flag"
            f.value = "CONFLICT"
            f.notes.append("estimate_far_exceeds_sum_insured")
        if isinstance(exc, float) and isinstance(est, float) and exc > est:
            fields["excess"].verdict = "flag"
            fields["excess"].notes.append("excess_exceeds_estimate")
        dob, inc = v("date_of_birth"), v("incident_date")
        if isinstance(dob, str) and isinstance(inc, str):
            try:
                age = (date.fromisoformat(inc) - date.fromisoformat(dob)).days / 365.25
                if age < 17:
                    fields["date_of_birth"].verdict = "flag"
                    fields["date_of_birth"].notes.append("implausible_age")
            except ValueError:
                pass

    missing = [f for f in REQUIRED if f not in fields]
    return {"doc_id": doc["id"], "fields": fields, "missing": missing,
            "dropped": dropped}


# ==========================================================================
# SCORING
# ==========================================================================
def score_doc(res: dict, doc: dict) -> dict:
    truth = doc["truth"]
    total = 0
    correct_accepts = wrong_accepts = flags = 0
    detail = []

    for name, f in res["fields"].items():
        if "identity_data_in_profile" in f.notes:
            # identity data that reached the profile is always a wrong accept
            f.correct = False
            f.points = POINTS["wrong_accept"]
            wrong_accepts += 1
            total += f.points
            detail.append({"field": name, "verdict": "accept", "ok": False,
                           "points": f.points, "why": "identity data in the profile"})
            continue

        want = truth.get(name)
        if f.verdict == "flag":
            f.points = POINTS["flag"]
            flags += 1
            total += f.points
            detail.append({"field": name, "verdict": "flag", "ok": None,
                           "points": f.points, "why": ", ".join(f.notes) or "below threshold"})
            continue

        # accepted — is it right?
        if want in ("CONFLICT", "ARITHMETIC_MISMATCH", "AMBIGUOUS", "IMPLAUSIBLE"):
            ok = False
            why = f"should not have been accepted ({want})"
        elif want is None:
            ok = False
            why = "field is not in the form at all"
        elif isinstance(want, float):
            ok = isinstance(f.value, float) and abs(f.value - want) < 0.01
            why = "correct" if ok else f"accepted {f.value}, should be {want}"
        else:
            ok = str(f.value) == str(want)
            why = "correct" if ok else f"accepted {f.value}, should be {want}"

        f.correct = ok
        f.points = POINTS["correct_accept"] if ok else POINTS["wrong_accept"]
        if ok:
            correct_accepts += 1
        else:
            wrong_accepts += 1
        total += f.points
        detail.append({"field": name, "verdict": "accept", "ok": ok,
                       "points": f.points, "why": why})

    unflagged_missing = len(res["missing"])
    total += unflagged_missing * POINTS["missing_unflagged"]

    return {"doc_id": doc["id"], "score": total,
            "correct_accepts": correct_accepts, "wrong_accepts": wrong_accepts,
            "flags": flags, "missing": unflagged_missing, "detail": detail}


def play(thresholds: dict, checks: dict,
         documents: list[dict] | None = None) -> dict:
    docs = DOCUMENTS if documents is None else documents
    rows, tot = [], {"score": 0, "correct_accepts": 0, "wrong_accepts": 0,
                     "flags": 0, "missing": 0}
    per_doc = {}
    for doc in docs:
        res = process(doc, thresholds, checks)
        sc = score_doc(res, doc)
        rows.append(sc)
        per_doc[doc["id"]] = {"result": res, "score": sc}
        for k in tot:
            tot[k] += sc[k]
    return {"total": tot, "per_doc": per_doc, "rows": rows}


# ==========================================================================
# REFERENCE STRATEGIES — used to prove tuning alone cannot win
# ==========================================================================
def strategy(name: str) -> tuple[dict, dict]:
    t = dict(DEFAULT_THRESHOLDS)
    c = dict(DEFAULT_CHECKS)
    if name == "brief":                       # exactly what the curriculum says
        pass
    elif name == "accept_everything":
        t = {f: 0.0 for f in FIELDS}
    elif name == "flag_everything":
        t = {f: 1.01 for f in FIELDS}
    elif name == "tuned_hard":                # aggressive tuning, no checks
        t = {f: (0.97 if FIELD_KIND[f] == "identifier" else 0.93) for f in FIELDS}
    elif name == "tuned_extreme":
        t = {f: 0.99 for f in FIELDS}
    elif name == "checks_only":               # brief thresholds + all checks
        c = {k: True for k in CHECKS}
    elif name == "checks_and_tuning":
        t = {f: (0.95 if FIELD_KIND[f] == "identifier" else 0.88) for f in FIELDS}
        c = {k: True for k in CHECKS}
    return t, c


def load(path: str) -> tuple[dict, dict]:
    with open(path) as f:
        cfg = json.load(f)
    t = dict(DEFAULT_THRESHOLDS)
    t.update(cfg.get("thresholds", {}))
    c = dict(DEFAULT_CHECKS)
    c.update(cfg.get("checks", {}))
    return t, c
