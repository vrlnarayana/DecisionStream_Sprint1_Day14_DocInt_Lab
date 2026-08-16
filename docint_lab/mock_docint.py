"""
mock_docint.py — ten synthetic case application PDFs, as Document Intelligence
would return them.

WHY MOCK
--------
The lab runs offline so the whole pod can complete it. But the shape here is
real: Azure AI Document Intelligence's prebuilt-document / layout models return
key-value pairs, tables and selection marks, each with a CONFIDENCE score and a
bounding region telling you which page it came from.

Everything the lab does downstream — mapping, thresholds, validation — works
identically against a real response. Swap `mock_docint.analyze()` for the real
SDK call and nothing else changes. That is the adapter pattern from Day 11,
applied to a different vendor service.

WHAT MAKES THESE DOCUMENTS HARD
-------------------------------
Real forms are messy in specific, repeatable ways. Each document here carries at
least one of:

  * a field label the form writer invented ("D.O.B.", "Reg No", "Sum Ins.")
  * handwriting the OCR read plausibly but wrongly (O/0, 1/I, 5/S, B/8)
  * a field that is simply absent
  * a field that appears twice, on different pages, with different values
  * a checkbox whose meaning inverts the sentence around it
  * a total that does not equal the sum of its parts
  * identity document data that should never have been extracted at all

None of these throw an error. Every one produces a confident, well-formed,
wrong record if you accept it without checking.
"""

from __future__ import annotations

import copy


def _kv(label, value, conf, page=1):
    return {"label": label, "value": value, "confidence": conf, "page": page}


# --------------------------------------------------------------------------
# The ten documents.
#
# `truth` is what the form actually said — used to score the pipeline.
# `kv` is what Document Intelligence returned.
# --------------------------------------------------------------------------
DOCUMENTS: list[dict] = [

# 1 — the easy one. Everything present, everything confident.
{
    "id": "APP-0001", "pages": 2, "note": "clean baseline",
    "kv": [
        _kv("Claim Reference", "CLM-2026-4471", 0.97),
        _kv("Policy Number", "MOT-4471902", 0.96),
        _kv("Applicant Full Name", "[PERSON_01]", 0.95),
        _kv("Date of Birth", "1987-04-12", 0.94),
        _kv("Vehicle Registration", "KP19 TRX", 0.96),
        _kv("Incident Date", "2026-03-14", 0.95),
        _kv("Sum Insured", "9000.00", 0.93),
        _kv("Excess", "350.00", 0.94),
        _kv("Repair Estimate Total", "12400.00", 0.95, page=2),
    ],
    "truth": {
        "claim_reference": "CLM-2026-4471", "policy_number": "MOT-4471902",
        "date_of_birth": "1987-04-12", "vehicle_registration": "KP19 TRX",
        "incident_date": "2026-03-14", "sum_insured": 9000.00,
        "excess": 350.00, "repair_estimate_total": 12400.00,
    },
},

# 2 — invented labels. The values are right; the LABELS are not what you expect.
{
    "id": "APP-0002", "pages": 2, "note": "non-standard field labels",
    "kv": [
        _kv("Claim Ref.", "CLM-2026-4482", 0.96),
        _kv("Policy No", "MOT-4482901", 0.95),
        _kv("Name of Applicant", "[PERSON_03]", 0.94),
        _kv("D.O.B.", "1991-09-30", 0.92),
        _kv("Reg No", "LT21 WNB", 0.95),
        _kv("Date of Loss", "2026-04-02", 0.93),
        _kv("Sum Ins.", "9100.00", 0.91),
        _kv("Policy Excess", "250.00", 0.93),
        _kv("Estimate Total", "6220.00", 0.94, page=2),
    ],
    "truth": {
        "claim_reference": "CLM-2026-4482", "policy_number": "MOT-4482901",
        "date_of_birth": "1991-09-30", "vehicle_registration": "LT21 WNB",
        "incident_date": "2026-04-02", "sum_insured": 9100.00,
        "excess": 250.00, "repair_estimate_total": 6220.00,
    },
},

# 3 — HANDWRITING. High confidence, wrong characters. The dangerous case.
{
    "id": "APP-0003", "pages": 2, "note": "handwritten form, OCR character confusion",
    "kv": [
        _kv("Claim Reference", "CLM-2026-45O3", 0.88),   # letter O for zero
        _kv("Policy Number", "MOT-45O3901", 0.86),       # letter O for zero
        _kv("Applicant Full Name", "[PERSON_04]", 0.71),
        _kv("Date of Birth", "1979-11-O5", 0.69),        # letter O again
        _kv("Vehicle Registration", "BD1B MHK", 0.83),   # B for 8
        _kv("Incident Date", "2026-03-09", 0.90),
        _kv("Sum Insured", "775O.OO", 0.74),             # letters for zeroes
        _kv("Excess", "3OO.OO", 0.72),
        _kv("Repair Estimate Total", "341O.OO", 0.79, page=2),
    ],
    "truth": {
        "claim_reference": "CLM-2026-4503", "policy_number": "MOT-4503901",
        "date_of_birth": "1979-11-05", "vehicle_registration": "BD18 MHK",
        "incident_date": "2026-03-09", "sum_insured": 7750.00,
        "excess": 300.00, "repair_estimate_total": 3410.00,
    },
},

# 4 — MISSING FIELDS. Two required fields simply are not on the form.
{
    "id": "APP-0004", "pages": 1, "note": "incomplete form, two fields absent",
    "kv": [
        _kv("Claim Reference", "CLM-2026-4517", 0.96),
        _kv("Policy Number", "MOT-4517901", 0.94),
        _kv("Applicant Full Name", "[PERSON_05]", 0.93),
        _kv("Vehicle Registration", "GF22 PLT", 0.95),
        _kv("Incident Date", "2026-03-28", 0.92),
        _kv("Repair Estimate Total", "15900.00", 0.94),
        # no Date of Birth, no Sum Insured
    ],
    "truth": {
        "claim_reference": "CLM-2026-4517", "policy_number": "MOT-4517901",
        "date_of_birth": None, "vehicle_registration": "GF22 PLT",
        "incident_date": "2026-03-28", "sum_insured": None,
        "excess": None, "repair_estimate_total": 15900.00,
    },
},

# 5 — CONFLICT ACROSS PAGES. Same field, two pages, two values.
{
    "id": "APP-0005", "pages": 3, "note": "field appears twice with different values",
    "kv": [
        _kv("Claim Reference", "CLM-2026-4526", 0.96),
        _kv("Policy Number", "MOT-4526901", 0.95),
        _kv("Applicant Full Name", "[PERSON_06]", 0.94),
        _kv("Date of Birth", "1984-02-17", 0.93),
        _kv("Vehicle Registration", "HN20 XCE", 0.95),
        _kv("Incident Date", "2026-04-03", 0.94),
        _kv("Sum Insured", "6400.00", 0.92),
        _kv("Excess", "250.00", 0.93),
        _kv("Repair Estimate Total", "2150.00", 0.94, page=2),
        _kv("Repair Estimate Total", "2450.00", 0.91, page=3),   # supplementary
    ],
    "truth": {
        "claim_reference": "CLM-2026-4526", "policy_number": "MOT-4526901",
        "date_of_birth": "1984-02-17", "vehicle_registration": "HN20 XCE",
        "incident_date": "2026-04-03", "sum_insured": 6400.00,
        "excess": 250.00, "repair_estimate_total": "CONFLICT",
    },
},

# 6 — SELECTION MARK whose meaning inverts the sentence.
{
    "id": "APP-0006", "pages": 2, "note": "checkbox semantics",
    "kv": [
        _kv("Claim Reference", "CLM-2026-4534", 0.96),
        _kv("Policy Number", "MOT-4534901", 0.95),
        _kv("Applicant Full Name", "[PERSON_07]", 0.94),
        _kv("Date of Birth", "1996-06-21", 0.92),
        _kv("Vehicle Registration", "JW19 RQA", 0.95),
        _kv("Incident Date", "2026-03-17", 0.93),
        _kv("Sum Insured", "10200.00", 0.92),
        _kv("Excess", "350.00", 0.93),
        _kv("Repair Estimate Total", "11050.00", 0.94, page=2),
        _kv("I do NOT wish to claim for personal injury", "selected", 0.88),
    ],
    "truth": {
        "claim_reference": "CLM-2026-4534", "policy_number": "MOT-4534901",
        "date_of_birth": "1996-06-21", "vehicle_registration": "JW19 RQA",
        "incident_date": "2026-03-17", "sum_insured": 10200.00,
        "excess": 350.00, "repair_estimate_total": 11050.00,
        "personal_injury_claimed": False,
    },
},

# 7 — IDENTITY DOCUMENT. Data you should not be extracting at all.
{
    "id": "APP-0007", "pages": 3, "note": "identity document attached — scope question",
    "kv": [
        _kv("Claim Reference", "CLM-2026-4548", 0.96),
        _kv("Policy Number", "MOT-4548901", 0.95),
        _kv("Applicant Full Name", "[PERSON_08]", 0.94),
        _kv("Date of Birth", "1988-12-03", 0.93),
        _kv("Vehicle Registration", "MS21 TVB", 0.95),
        _kv("Incident Date", "2026-03-25", 0.94),
        _kv("Sum Insured", "8900.00", 0.92),
        _kv("Excess", "300.00", 0.93),
        _kv("Repair Estimate Total", "7630.00", 0.94, page=2),
        # page 3 is a driving licence scan
        _kv("Driving Licence Number", "SMITH912035AB9CD", 0.91, page=3),
        _kv("Licence Issue Date", "2019-08-14", 0.90, page=3),
        _kv("Licence Address Line 1", "14 Fernbank Road", 0.87, page=3),
        _kv("Licence Postcode", "LS8 3QT", 0.92, page=3),
    ],
    "truth": {
        "claim_reference": "CLM-2026-4548", "policy_number": "MOT-4548901",
        "date_of_birth": "1988-12-03", "vehicle_registration": "MS21 TVB",
        "incident_date": "2026-03-25", "sum_insured": 8900.00,
        "excess": 300.00, "repair_estimate_total": 7630.00,
    },
},

# 8 — ARITHMETIC. Line items do not sum to the stated total.
{
    "id": "APP-0008", "pages": 2, "note": "line items do not reconcile to total",
    "kv": [
        _kv("Claim Reference", "CLM-2026-4552", 0.96),
        _kv("Policy Number", "MOT-4552901", 0.95),
        _kv("Applicant Full Name", "[PERSON_09]", 0.93),
        _kv("Date of Birth", "1972-05-08", 0.92),
        _kv("Vehicle Registration", "PC17 KDN", 0.94),
        _kv("Incident Date", "2026-03-12", 0.93),
        _kv("Sum Insured", "5600.00", 0.91),
        _kv("Excess", "250.00", 0.92),
        _kv("Parts Subtotal", "2900.00", 0.93, page=2),
        _kv("Labour Subtotal", "1480.00", 0.92, page=2),
        _kv("Repair Estimate Total", "4880.00", 0.95, page=2),   # 2900+1480 = 4380
    ],
    "truth": {
        "claim_reference": "CLM-2026-4552", "policy_number": "MOT-4552901",
        "date_of_birth": "1972-05-08", "vehicle_registration": "PC17 KDN",
        "incident_date": "2026-03-12", "sum_insured": 5600.00,
        "excess": 250.00, "repair_estimate_total": "ARITHMETIC_MISMATCH",
    },
},

# 9 — DATE FORMAT AMBIGUITY. 04/03/2026 — is that 4 March or 3 April?
{
    "id": "APP-0009", "pages": 2, "note": "ambiguous date format",
    "kv": [
        _kv("Claim Reference", "CLM-2026-4561", 0.96),
        _kv("Policy Number", "MOT-4561901", 0.95),
        _kv("Applicant Full Name", "[PERSON_10]", 0.94),
        _kv("Date of Birth", "12/07/1993", 0.90),
        _kv("Vehicle Registration", "QT19 LFS", 0.95),
        _kv("Incident Date", "04/03/2026", 0.91),
        _kv("Sum Insured", "9800.00", 0.92),
        _kv("Excess", "400.00", 0.93),
        _kv("Repair Estimate Total", "13200.00", 0.94, page=2),
    ],
    "truth": {
        "claim_reference": "CLM-2026-4561", "policy_number": "MOT-4561901",
        "date_of_birth": "AMBIGUOUS", "vehicle_registration": "QT19 LFS",
        "incident_date": "AMBIGUOUS", "sum_insured": 9800.00,
        "excess": 400.00, "repair_estimate_total": 13200.00,
    },
},

# 10 — LOW CONFIDENCE THROUGHOUT. Poor scan quality.
{
    "id": "APP-0010", "pages": 4, "note": "poor scan, low confidence across the board",
    "kv": [
        _kv("Claim Reference", "CLM-2026-4573", 0.64),
        _kv("Policy Number", "MOT-4573901", 0.61),
        _kv("Applicant Full Name", "[PERSON_11]", 0.58),
        _kv("Date of Birth", "1990-01-22", 0.55),
        _kv("Vehicle Registration", "ZB20 HRC", 0.67),
        _kv("Incident Date", "2026-04-06", 0.62),
        _kv("Sum Insured", "7100.00", 0.59),
        _kv("Excess", "350.00", 0.60),
        _kv("Repair Estimate Total", "5940.00", 0.63, page=3),
    ],
    "truth": {
        "claim_reference": "CLM-2026-4573", "policy_number": "MOT-4573901",
        "date_of_birth": "1990-01-22", "vehicle_registration": "ZB20 HRC",
        "incident_date": "2026-04-06", "sum_insured": 7100.00,
        "excess": 350.00, "repair_estimate_total": 5940.00,
    },
},
]


def analyze(doc_id: str) -> dict:
    """
    Stands in for:

        client = DocumentIntelligenceClient(endpoint, credential)
        poller = client.begin_analyze_document("prebuilt-document", body=pdf)
        result = poller.result()

    Returns the same SHAPE the service returns: key-value pairs, each with a
    confidence and a page. Swap this one function for the real SDK call and
    the rest of the lab is unchanged.
    """
    for d in DOCUMENTS:
        if d["id"] == doc_id:
            return copy.deepcopy({"id": d["id"], "pages": d["pages"],
                                  "key_value_pairs": d["kv"]})
    raise KeyError(f"No such document: {doc_id}")


def all_ids() -> list[str]:
    return [d["id"] for d in DOCUMENTS]


def truth(doc_id: str) -> dict:
    for d in DOCUMENTS:
        if d["id"] == doc_id:
            return copy.deepcopy(d["truth"])
    raise KeyError(doc_id)


def note(doc_id: str) -> str:
    for d in DOCUMENTS:
        if d["id"] == doc_id:
            return d["note"]
    return ""
