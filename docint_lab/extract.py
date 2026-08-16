"""
extract.py — map Document Intelligence output to the DecisionStream schema,
apply confidence policy, and validate.

THE PIPELINE
------------
    DI response
      -> normalise labels     (the form writer's words -> your field names)
      -> normalise values     (dates, money, registrations)
      -> resolve conflicts    (same field, two pages, two answers)
      -> apply confidence     (accept / flag / reject, PER FIELD)
      -> validate             (cross-field checks the model cannot do)
      -> decision profile     (+ a review queue for anything not accepted)

THE POINT OF TODAY
------------------
The brief says: ">0.85 accept, otherwise flag". Implement that first — it is
in `POLICY_FLAT`. Then look at what it does to APP-0003.

APP-0003 is a handwritten form. Its claim reference came back as
"CLM-2026-45O3" — a letter O where a zero should be — with confidence 0.88.
A flat 0.85 threshold ACCEPTS it. The record is now attached to a claim that
does not exist, and nothing errored.

    Confidence is the model's certainty about what it SAW.
    It is not a statement about whether the value is CORRECT.

That gap is the whole lesson. Two things close it:

  1. Per-field thresholds. A claim reference is an identifier — a single wrong
     character makes it useless, so it needs a much higher bar than a free-text
     description where a small error is survivable.

  2. Validation the model cannot do. Does the reference match the expected
     pattern? Do the parts sum to the total? Is the date plausible? These catch
     confident errors that no threshold ever will.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime


# ==========================================================================
# 1. THE TARGET SCHEMA
# ==========================================================================
SCHEMA: dict[str, dict] = {
    "claim_reference":      {"type": "identifier", "required": True,
                             "pattern": r"^CLM-\d{4}-\d{4}$"},
    "policy_number":        {"type": "identifier", "required": True,
                             "pattern": r"^MOT-\d{7}$"},
    "date_of_birth":        {"type": "date",       "required": True},
    "vehicle_registration": {"type": "identifier", "required": True,
                             "pattern": r"^[A-Z]{2}\d{2}\s?[A-Z]{3}$"},
    "incident_date":        {"type": "date",       "required": True},
    "sum_insured":          {"type": "money",      "required": True},
    "excess":               {"type": "money",      "required": True},
    "repair_estimate_total": {"type": "money",     "required": True},
    "parts_subtotal":       {"type": "money",      "required": False},
    "labour_subtotal":      {"type": "money",      "required": False},
    "applicant_name":       {"type": "text",       "required": True},
    "personal_injury_claimed": {"type": "boolean", "required": False},
}

# ==========================================================================
# 2. LABEL MAPPING — the form writer's words to your field names.
#
# This table is not glamorous and it is most of the work. Every new client
# form adds rows to it. Build it as data, never as if/elif in code, because
# the people who know the aliases are not the people who write Python.
# ==========================================================================
LABEL_MAP: dict[str, str] = {}
for canonical, aliases in {
    "applicant_name": ["applicant full name", "name of applicant", "applicant name",
                       "full name"],
    "claim_reference": ["claim reference", "claim ref.", "claim ref", "claim no",
                        "reference"],
    "policy_number": ["policy number", "policy no", "policy no.", "policy ref"],
    "date_of_birth": ["date of birth", "d.o.b.", "dob", "birth date"],
    "vehicle_registration": ["vehicle registration", "reg no", "reg no.",
                             "registration", "vrn", "vehicle reg"],
    "incident_date": ["incident date", "date of loss", "date of incident",
                      "loss date"],
    "sum_insured": ["sum insured", "sum ins.", "sum ins", "insured value"],
    "excess": ["excess", "policy excess", "standard excess"],
    "repair_estimate_total": ["repair estimate total", "estimate total",
                              "total estimate", "repair total"],
    "parts_subtotal": ["parts subtotal", "parts total"],
    "labour_subtotal": ["labour subtotal", "labour total", "labor subtotal"],
}.items():
    for a in aliases:
        LABEL_MAP[a] = canonical

# Selection marks whose meaning INVERTS the sentence they sit next to.
NEGATED_CHECKBOXES = {
    "i do not wish to claim for personal injury": "personal_injury_claimed",
}

# Fields that must NEVER be written to the decision profile.
# Extracting identity evidence is a scope decision made on Sprint 0 Day 3:
# we need the verification VERDICT, not the passport.
FORBIDDEN_PREFIXES = ("driving licence", "licence ", "passport", "national insurance")


# ==========================================================================
# 3. CONFIDENCE POLICY
# ==========================================================================
@dataclass
class Policy:
    name: str
    default_accept: float
    default_reject: float
    per_field: dict[str, tuple[float, float]] = dc_field(default_factory=dict)
    # When False, the pipeline trusts confidence ALONE — which is exactly what
    # ">0.85 accept, otherwise flag" says. Run it that way first and see what
    # gets through. Then turn the checks on.
    use_checks: bool = True

    def band(self, fname: str) -> tuple[float, float]:
        return self.per_field.get(fname, (self.default_accept, self.default_reject))

    def verdict(self, fname: str, conf: float) -> str:
        acc, rej = self.band(fname)
        if conf >= acc:
            return "accept"
        if conf < rej:
            return "reject"
        return "flag"


# STEP 1 — exactly what the brief says. Confidence and nothing else.
POLICY_FLAT = Policy("flat_0.85", 0.85, 0.50, use_checks=False)

# STEP 2 — same threshold, but now pattern and cross-field checks can override
# a high confidence. This is the single most valuable line of code today.
POLICY_FLAT_CHECKED = Policy("flat_0.85_checked", 0.85, 0.50)

# What you should end up proposing. Identifiers carry a much higher bar
# because one wrong character makes them worthless; a name can be corrected
# by a human who is looking at the same form.
POLICY_TIERED = Policy(
    "tiered", 0.85, 0.50,
    per_field={
        "claim_reference":      (0.97, 0.70),
        "policy_number":        (0.97, 0.70),
        "vehicle_registration": (0.95, 0.65),
        "date_of_birth":        (0.90, 0.55),
        "incident_date":        (0.90, 0.55),
        "sum_insured":          (0.90, 0.55),
        "excess":               (0.88, 0.50),
        "repair_estimate_total": (0.92, 0.60),
        # A name is NOT an identifier. A handler looking at the same form can
        # correct a misread name in seconds. It does not need a 0.97 bar.
        "applicant_name":       (0.80, 0.45),
    },
)

POLICIES = {p.name: p for p in (POLICY_FLAT, POLICY_FLAT_CHECKED, POLICY_TIERED)}


# ==========================================================================
# 4. VALUE NORMALISATION
# ==========================================================================
MONEY_RE = re.compile(r"[^0-9.,]")


def normalise_money(raw: str) -> tuple[float | None, list[str]]:
    notes = []
    s = raw.strip()
    # OCR reads zero as the letter O and one as the letter I surprisingly often.
    if re.search(r"[OoIl]", s):
        notes.append("ocr_letter_in_number")
        s = s.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1")
    s = MONEY_RE.sub("", s).replace(",", "")
    try:
        return float(s), notes
    except ValueError:
        notes.append("unparseable_money")
        return None, notes


ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
SLASH_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def normalise_date(raw: str) -> tuple[str | None, list[str]]:
    notes = []
    s = raw.strip()
    if re.search(r"[OoIl]", s):
        notes.append("ocr_letter_in_number")
        s = s.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1")
    m = ISO_RE.match(s)
    if m:
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return s, notes
        except ValueError:
            notes.append("impossible_date")
            return None, notes
    m = SLASH_RE.match(s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # If both parts are 12 or under, we genuinely cannot tell which is
        # the day and which is the month. Guessing is how you get a claim
        # dated four weeks in the wrong direction.
        if a <= 12 and b <= 12 and a != b:
            notes.append("ambiguous_date_format")
            return None, notes
        d, mth = (a, b) if a > 12 else (b, a)
        try:
            return date(y, mth, d).isoformat(), notes
        except ValueError:
            notes.append("impossible_date")
            return None, notes
    notes.append("unrecognised_date_format")
    return None, notes


def normalise_identifier(raw: str, fname: str) -> tuple[str, list[str]]:
    notes = []
    s = raw.strip().upper()
    spec = SCHEMA.get(fname, {})
    pat = spec.get("pattern")
    if pat and not re.match(pat, s):
        # Try the standard OCR confusions before giving up. If a repair makes
        # it match the expected pattern, that is strong evidence of what it
        # should have been — but it is still a REPAIR and must be flagged.
        repaired = s.replace("O", "0").replace("I", "1").replace("B", "8").replace("S", "5")
        if re.match(pat, repaired):
            notes.append("ocr_repaired_to_pattern")
            return repaired, notes
        notes.append("pattern_mismatch")
    return s, notes


# ==========================================================================
# 5. THE PIPELINE
# ==========================================================================
@dataclass
class Field:
    name: str
    raw: str
    value: object
    confidence: float
    page: int
    verdict: str
    notes: list[str] = dc_field(default_factory=list)


def extract(di_response: dict, policy: Policy) -> dict:
    fields: dict[str, Field] = {}
    conflicts: list[dict] = []
    dropped: list[dict] = []

    for kv in di_response["key_value_pairs"]:
        label = kv["label"].strip().lower()

        # Scope gate FIRST. Identity evidence never reaches the profile.
        if any(label.startswith(p) for p in FORBIDDEN_PREFIXES):
            dropped.append({"label": kv["label"], "page": kv["page"],
                            "reason": "identity_evidence_out_of_scope"})
            continue

        if label in NEGATED_CHECKBOXES:
            fname = NEGATED_CHECKBOXES[label]
            selected = str(kv["value"]).lower() == "selected"
            fields[fname] = Field(fname, kv["value"], (not selected),
                                  kv["confidence"], kv["page"],
                                  policy.verdict(fname, kv["confidence"]),
                                  ["negated_checkbox"])
            continue

        fname = LABEL_MAP.get(label)
        if fname is None:
            dropped.append({"label": kv["label"], "page": kv["page"],
                            "reason": "unmapped_label"})
            continue

        spec = SCHEMA[fname]
        notes: list[str] = []
        if spec["type"] == "money":
            val, n = normalise_money(str(kv["value"]))
        elif spec["type"] == "date":
            val, n = normalise_date(str(kv["value"]))
        elif spec["type"] == "text":
            val, n = str(kv["value"]).strip(), []
        else:
            val, n = normalise_identifier(str(kv["value"]), fname)
        notes += n

        verdict = policy.verdict(fname, kv["confidence"])
        # A parse failure or a pattern mismatch overrides a high confidence.
        # This is the single most valuable check in the pipeline — and it is
        # the one the brief does not ask for.
        if policy.use_checks:
            if val is None or "pattern_mismatch" in notes or "ambiguous_date_format" in notes:
                verdict = "flag"
            if "ocr_repaired_to_pattern" in notes and verdict == "accept":
                verdict = "flag"
        elif val is None:
            # Even confidence-only cannot store something it could not parse.
            val = str(kv["value"])

        if fname in fields:
            prev = fields[fname]
            if prev.value != val and policy.use_checks:
                conflicts.append({
                    "field": fname,
                    "a": {"value": prev.value, "page": prev.page, "confidence": prev.confidence},
                    "b": {"value": val, "page": kv["page"], "confidence": kv["confidence"]},
                })
                # Never silently pick one. That is a human decision.
                prev.verdict = "flag"
                prev.notes.append("conflicting_values_across_pages")
                continue
        fields[fname] = Field(fname, str(kv["value"]), val, kv["confidence"],
                              kv["page"], verdict, notes)

    missing = [f for f, s in SCHEMA.items()
               if s["required"] and f not in fields]

    validations = validate(fields) if policy.use_checks else []
    for v in validations:
        for f in v["fields"]:
            if f in fields and fields[f].verdict == "accept":
                fields[f].verdict = "flag"
                fields[f].notes.append(v["code"])

    accepted = {f.name: f.value for f in fields.values() if f.verdict == "accept"}
    review = [{"field": f.name, "raw": f.raw, "value": f.value,
               "confidence": f.confidence, "page": f.page,
               "verdict": f.verdict, "notes": f.notes}
              for f in fields.values() if f.verdict != "accept"]

    return {
        "document_id": di_response["id"],
        "pages": di_response["pages"],
        "policy": policy.name,
        "decision_profile": accepted,
        "review_queue": review,
        "missing_required": missing,
        "conflicts": conflicts,
        "dropped": dropped,
        "validations": validations,
        "needs_human": bool(review or missing or conflicts or validations),
    }


# ==========================================================================
# 6. VALIDATION — the checks a confidence score can never make
# ==========================================================================
def validate(fields: dict[str, Field]) -> list[dict]:
    out = []

    def val(n):
        f = fields.get(n)
        return f.value if f else None

    parts, labour, total = val("parts_subtotal"), val("labour_subtotal"), val("repair_estimate_total")
    if parts is not None and labour is not None and total is not None:
        if abs((parts + labour) - total) > 0.01:
            out.append({"code": "arithmetic_mismatch",
                        "detail": f"parts {parts} + labour {labour} = {parts + labour}, "
                                  f"stated total {total}",
                        "fields": ["repair_estimate_total", "parts_subtotal", "labour_subtotal"]})

    dob, inc = val("date_of_birth"), val("incident_date")
    if dob and inc:
        try:
            d1, d2 = date.fromisoformat(dob), date.fromisoformat(inc)
            age = (d2 - d1).days / 365.25
            if age < 17:
                out.append({"code": "implausible_age",
                            "detail": f"applicant would be {age:.0f} at the incident date",
                            "fields": ["date_of_birth", "incident_date"]})
            if d2 > date(2027, 1, 1):
                out.append({"code": "future_incident_date", "detail": inc,
                            "fields": ["incident_date"]})
        except ValueError:
            pass

    est, sins = val("repair_estimate_total"), val("sum_insured")
    if est is not None and sins is not None and est > sins:
        out.append({"code": "estimate_exceeds_sum_insured",
                    "detail": f"estimate {est} against sum insured {sins}",
                    "fields": ["repair_estimate_total"]})

    exc = val("excess")
    if exc is not None and est is not None and exc > est:
        out.append({"code": "excess_exceeds_estimate",
                    "detail": f"excess {exc} against estimate {est}",
                    "fields": ["excess"]})

    return out
