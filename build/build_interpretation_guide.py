#!/usr/bin/env /usr/bin/python3
"""
Builds "Interpreting Your Results" — the learner-facing companion to the Day 14
Document Intelligence lab.

    /usr/bin/python3 build/build_interpretation_guide.py

python-docx lives on /usr/bin/python3 (3.9.6) on this machine, NOT on 3.12.
House style: navy #1D3557 header rows with white text, #EDF1F7 zebra banding,
Consolas for code, red #C8102E for the things that cost money.

Every number in the generated document is a measured figure from a real run:
the mock totals are deterministic, and the azure totals are from the live run
against doc-int-forge0fde-1 on 2026-08-16 (prebuilt-layout, api 2024-11-30, F0).
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

NAVY = RGBColor(0x1D, 0x35, 0x57)
RED = RGBColor(0xC8, 0x10, 0x2E)
TEAL = RGBColor(0x1B, 0x6C, 0x6F)
GREY = RGBColor(0x55, 0x5F, 0x6B)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

NAVY_HEX = "1D3557"
ZEBRA_HEX = "EDF1F7"
CODE_HEX = "F4F6F8"
AMBER_HEX = "FDF3E3"

OUT = Path(__file__).resolve().parent.parent / "Day14_DocInt_Interpreting_Your_Results.docx"


# --------------------------------------------------------------------------
# low-level helpers
# --------------------------------------------------------------------------
def shade(el, hex_fill):
    """Paint a cell or paragraph background."""
    pr = el.get_or_add_tcPr() if el.tag.endswith("tc") else el.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    pr.append(shd)


def cell_text(cell, text, bold=False, color=None, size=9.5):
    """Write into a cell WITHOUT going through cell.text.

    Setting `cell.text` leaves an empty run at index 0, so anything that later
    styles or asserts on runs[0] silently misses the real text. Write the run
    ourselves and there is exactly one run holding exactly the text.
    """
    p = cell.paragraphs[0]
    for r in list(p.runs):
        r._element.getparent().remove(r._element)
    run = p.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    run.font.name = "Calibri"
    if color is not None:
        run.font.color.rgb = color
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    return run


def h1(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(22)
    p.paragraph_format.space_after = Pt(8)
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(17)
    r.font.color.rgb = NAVY
    r.font.name = "Calibri"
    # navy rule under the heading
    pPr = p._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "12")
    bottom.set(qn("w:color"), NAVY_HEX)
    borders.append(bottom)
    pPr.append(borders)
    return p


def h2(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(14)
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(12.5)
    r.font.color.rgb = NAVY
    r.font.name = "Calibri"
    return p


def para(doc, text, italic=False, color=None, size=10.5, space_after=7):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    r = p.add_run(text)
    r.italic = italic
    r.font.size = Pt(size)
    r.font.name = "Calibri"
    if color is not None:
        r.font.color.rgb = color
    return p


def rich(doc, chunks, size=10.5, space_after=7):
    """chunks = [(text, {bold/italic/color}), ...] in one paragraph."""
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    for text, opts in chunks:
        r = p.add_run(text)
        r.bold = opts.get("bold", False)
        r.italic = opts.get("italic", False)
        r.font.size = Pt(opts.get("size", size))
        r.font.name = opts.get("font", "Calibri")
        if opts.get("color") is not None:
            r.font.color.rgb = opts["color"]
    return p


def bullet(doc, text, bold_prefix=None):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(3)
    if bold_prefix:
        r = p.add_run(bold_prefix)
        r.bold = True
        r.font.size = Pt(10.5)
        r.font.name = "Calibri"
    r = p.add_run(text)
    r.font.size = Pt(10.5)
    r.font.name = "Calibri"
    return p


def code(doc, text, fill=CODE_HEX):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(5)
    p.paragraph_format.space_after = Pt(9)
    p.paragraph_format.left_indent = Inches(0.18)
    r = p.add_run(text)
    r.font.name = "Consolas"
    r.font.size = Pt(9)
    shade(p._p, fill)
    return p


def callout(doc, title, text, fill=AMBER_HEX, accent=RED):
    t = doc.add_table(rows=1, cols=1)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    c = t.rows[0].cells[0]
    shade(c._tc, fill)
    p = c.paragraphs[0]
    for r in list(p.runs):
        r._element.getparent().remove(r._element)
    r = p.add_run(title + "  ")
    r.bold = True
    r.font.size = Pt(10.5)
    r.font.color.rgb = accent
    r.font.name = "Calibri"
    r = p.add_run(text)
    r.font.size = Pt(10.5)
    r.font.name = "Calibri"
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(6)
    doc.add_paragraph().paragraph_format.space_after = Pt(3)
    return t


def table(doc, headers, rows, widths=None, mono_cols=(), size=9.5):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, htext in enumerate(headers):
        c = t.rows[0].cells[i]
        shade(c._tc, NAVY_HEX)
        cell_text(c, htext, bold=True, color=WHITE, size=size)
    for n, row in enumerate(rows):
        cells = t.add_row().cells
        for i, val in enumerate(row):
            if n % 2 == 1:
                shade(cells[i]._tc, ZEBRA_HEX)
            run = cell_text(cells[i], str(val), size=size)
            if i in mono_cols:
                run.font.name = "Consolas"
                run.font.size = Pt(size - 0.5)
    if widths:
        for r in t.rows:
            for i, w in enumerate(widths):
                r.cells[i].width = Inches(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    return t


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------
def build():
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10.5)
    for s in doc.sections:
        s.left_margin = s.right_margin = Inches(0.85)
        s.top_margin = s.bottom_margin = Inches(0.8)

    # ---- title ----------------------------------------------------------
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run("Interpreting Your Results")
    r.bold = True
    r.font.size = Pt(26)
    r.font.color.rgb = NAVY
    r.font.name = "Calibri"

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run("Sprint 1 · Day 14 — Document Intelligence Field Extraction")
    r.font.size = Pt(13)
    r.font.color.rgb = RED
    r.font.name = "Calibri"

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(16)
    r = p.add_run("Birlasoft FORGE FDE Academy  ·  DecisionStream AI  ·  "
                  "Confidential — for learner use only")
    r.font.size = Pt(9)
    r.font.color.rgb = GREY
    r.italic = True
    r.font.name = "Calibri"

    para(doc,
         "You have run the lab and you have three tables of numbers. This guide is "
         "how to read them — what each column means, which ones matter, what the "
         "headline result is actually telling you, and the four conclusions people "
         "reliably draw from this data that are wrong.",
         size=11)
    rich(doc, [
        ("Read it with your own ", {}),
        ("out/mock/extraction_report.md", {"font": "Consolas", "size": 10}),
        (" open beside it. Every figure quoted here is a measured figure, not an "
         "illustration — if yours differ, that is a finding worth chasing, not a "
         "typo to ignore.", {}),
    ])

    # =====================================================================
    h1(doc, "1 · How to read a run")
    # =====================================================================
    h2(doc, "The banner tells you which world you are in")
    para(doc, "Every run opens by naming its provider. Check it before you read "
              "anything else — half of all confusion about this lab is someone "
              "comparing a mock table to an azure table without noticing.")
    code(doc,
         "provider: mock  (offline, free, deterministic)\n"
         "\n"
         "provider: azure  (prebuilt-layout @ api-version 2024-11-30, "
         "/documentintelligence/)\n"
         "          endpoint: https://doc-int-....cognitiveservices.azure.com/\n"
         "          cache:    on — delete out/azure/di_raw/ to force a fresh read")

    h2(doc, "The five columns")
    table(doc,
          ["Column", "Name", "What it counts", "How to treat it"],
          [
              ["acc", "Accepted", "Fields written into the decision profile with no "
               "human review", "Throughput. Higher is only better if WRONG stays at zero."],
              ["ok", "Correct", "Of the accepted fields, how many match ground truth",
               "The honest numerator. Never quote acc without ok."],
              ["WRONG", "Wrong accepted", "Accepted, and the value is wrong",
               "The only column that can hurt a customer. Judge every policy on this first."],
              ["flag", "Flagged", "Sent to the review queue for a human",
               "A cost, not a failure. Roughly 30 seconds of handler time each."],
              ["miss", "Missing", "A required field that was never extracted",
               "Visible and safe — the record is obviously incomplete."],
          ],
          widths=[0.65, 1.15, 2.5, 2.7], mono_cols=(0,))

    rich(doc, [
        ("Note that ", {}),
        ("acc", {"font": "Consolas", "size": 10}),
        (" is not ", {}),
        ("ok + WRONG", {"font": "Consolas", "size": 10}),
        (". A field can be accepted and have no ground-truth entry to check it "
         "against — those are counted as accepted but score in neither column. "
         "This is why the totals do not add up the way you expect, and it is "
         "deliberate: a metric that quietly invents a verdict for unverifiable "
         "fields is worse than one that admits the gap.", {}),
    ])

    h2(doc, "A worked line")
    code(doc,
         "doc         acc   ok  WRONG  flag  miss  note\n"
         "APP-0003      3    2      1     6     0  handwritten form"
         "  <-- policy_number=MOT-45O3901")
    para(doc, "Three fields were accepted. Two of them are right. One is wrong — and "
              "the arrow names it, so you never have to hunt. Six more went to the "
              "review queue. Nothing is missing, and nothing errored: this document "
              "produced a complete, well-formed, confidently wrong record.")
    rich(doc, [
        ("When a line surprises you, do not guess. Run ", {}),
        ("python3 run_lab.py --show APP-0003", {"font": "Consolas", "size": 10}),
        (" and you get every field, under every policy, with its raw value, its "
         "parsed value, its confidence and the validator that fired.", {}),
    ])

    # =====================================================================
    h1(doc, "2 · The three policies, and what each one is measuring")
    # =====================================================================
    table(doc,
          ["Policy", "What it does", "The question it answers"],
          [
              ["flat_0.85", "Accept anything above 0.85 confidence. Confidence and "
               "nothing else.", "What does the brief, implemented literally, let "
               "through?"],
              ["flat_0.85_checked", "Same threshold, plus pattern and cross-field "
               "validators that can override a high confidence.",
               "What does knowing the shape of the data buy you?"],
              ["tiered", "Per-field thresholds — identifiers at 0.97, names at 0.80 "
               "— plus the same validators.",
               "What does tuning the thresholds buy you on top of that?"],
          ],
          widths=[1.5, 3.0, 2.5], mono_cols=(0,))
    para(doc, "The three are deliberately cumulative. Each adds exactly one idea to "
              "the one before it, so the difference between two rows isolates the "
              "value of that one idea. That is the experiment; the numbers are just "
              "how it came out.", italic=True)

    # =====================================================================
    h1(doc, "3 · The headline result")
    # =====================================================================
    para(doc, "From the mock provider — deterministic, so these are the numbers you "
              "should have:")
    table(doc,
          ["Policy", "Correct", "WRONG accepted", "Flagged", "Missing"],
          [
              ["flat_0.85", "60", "5", "15", "3"],
              ["flat_0.85_checked", "56", "0", "26", "3"],
              ["tiered", "40", "0", "42", "3"],
          ],
          widths=[1.9, 1.2, 1.5, 1.2, 1.2], mono_cols=(0,))

    h2(doc, "What it says")
    bullet(doc, "removed all five wrong acceptances, at a cost of eleven extra flags.",
           bold_prefix="Adding the checks ")
    bullet(doc, "removed zero more — and added sixteen further flags.",
           bold_prefix="Adding tiered thresholds on top ")

    callout(doc, "The uncomfortable result.",
            "Tightening thresholds felt safer, produced a review queue nearly three "
            "times the size of the original, cost real handler time, and bought "
            "nothing. The validators did all of the work. Be ready to say that out "
            "loud to a client who assumes tighter is safer — it is the single most "
            "useful sentence in this lab.")

    para(doc, "The reason is worth stating precisely, because it generalises well "
              "beyond this lab. A confidence score is the model's certainty about "
              "what characters it saw. A pattern check knows something the score "
              "never can: what a valid claim reference looks like. No amount of "
              "threshold tuning can recover information the score does not contain.")

    h2(doc, "The cost of a flag, and why it is the wrong thing to minimise")
    para(doc, "A flagged field costs a handler around thirty seconds. A wrongly "
              "accepted identifier costs a rework cycle, a complaint, or a "
              "regulatory finding — and nobody knows it happened until somebody "
              "notices downstream, which may be months. The two are not comparable, "
              "and a policy that trades one wrong acceptance for twenty flags is "
              "almost always the right trade.")
    rich(doc, [
        ("Do the arithmetic for your own recommendation before you present it. "
         "At 4,000 documents a month, the ", {}),
        ("flat_0.85_checked", {"font": "Consolas", "size": 10}),
        (" flag rate is the number that determines whether your proposal is "
         "affordable — and it is the first thing an operations lead will ask for.", {}),
    ])

    # =====================================================================
    h1(doc, "4 · The five wrong acceptances, one at a time")
    # =====================================================================
    para(doc, "These are the five that flat_0.85 let through. None of them errored. "
              "Every one produced a well-formed record.")
    table(doc,
          ["Document / field", "Accepted", "Truth", "Why it slipped through",
           "What caught it"],
          [
              ["APP-0003\npolicy_number", "MOT-45O3901", "MOT-4503901",
               "Letter O for zero, at confidence 0.86 — above the threshold",
               "Pattern check: the identifier format forbids letters in that position"],
              ["APP-0005\nrepair_estimate_total", "2450.00", "CONFLICT",
               "The same field appears on two pages with two different values; both "
               "are individually confident",
               "Cross-field check: two values for one field is a conflict, not a "
               "choice"],
              ["APP-0008\nrepair_estimate_total", "4880.00", "ARITHMETIC_MISMATCH",
               "The stated total is perfectly legible — it just does not equal the "
               "line items",
               "Cross-field check: parts + labour must reconcile to the total"],
              ["APP-0009\ndate_of_birth", "12/07/1993", "AMBIGUOUS",
               "A real date, read correctly, in an unknowable format",
               "Format check: no day/month order can be established"],
              ["APP-0009\nincident_date", "04/03/2026", "AMBIGUOUS",
               "4 March or 3 April — both are valid dates",
               "Format check: same"],
          ],
          widths=[1.35, 1.05, 1.15, 1.9, 1.75], mono_cols=(0, 1, 2), size=8.5)

    para(doc, "Read the middle column again. Three of the five are not OCR errors at "
              "all — the service read the characters perfectly. They are failures of "
              "meaning: a value that conflicts with another value, a total that does "
              "not add up, a date whose format is unknowable. No confidence threshold "
              "can ever catch those, because there is nothing uncertain about the "
              "reading.")

    # =====================================================================
    h1(doc, "5 · Document by document")
    # =====================================================================
    para(doc, "Each of the ten documents isolates one failure mode that happens in "
              "production. Figures below are mock, flat_0.85.")
    table(doc,
          ["Doc", "acc / ok / WRONG / flag / miss", "What it tests",
           "The reading you want"],
          [
              ["APP-0001", "9 / 8 / 0 / 0 / 0", "Clean baseline",
               "Your control. If this one is not clean, something is wrong with your "
               "setup, not the data."],
              ["APP-0002", "9 / 8 / 0 / 0 / 0", "Invented field labels — D.O.B., "
               "Reg No, Sum Ins.",
               "Label mapping is a solved problem when you know the aliases. It is "
               "silent data loss when you do not."],
              ["APP-0003", "3 / 2 / 1 / 6 / 0", "Handwriting; O/0 confusion",
               "The headline case. High confidence, wrong characters, valid-looking "
               "output."],
              ["APP-0004", "6 / 5 / 0 / 0 / 3", "Two required fields simply absent",
               "Missing is the safe failure — visibly incomplete, nobody is misled."],
              ["APP-0005", "9 / 7 / 1 / 0 / 0", "Same field, two pages, two values",
               "The pipeline must surface both and flag. Picking one silently is an "
               "underwriting decision it has no authority to make."],
              ["APP-0006", "10 / 9 / 0 / 0 / 0", "A checkbox that inverts the sentence",
               "Selection marks carry meaning that depends on the sentence around "
               "them. Extracting the tick is not understanding it."],
              ["APP-0007", "9 / 8 / 0 / 0 / 0", "Driving licence attached",
               "Four licence fields are dropped before the profile is built. Check "
               "the dropped array in the JSON — that is the Sprint 0 Day 3 scope "
               "decision made executable."],
              ["APP-0008", "11 / 7 / 1 / 0 / 0", "Line items do not sum to the total",
               "Arithmetic is a validator, not an extraction problem."],
              ["APP-0009", "9 / 6 / 2 / 0 / 0", "04/03/2026 — 4 March or 3 April?",
               "Returning nothing is correct. A guess puts the claim four weeks out "
               "and the error is invisible."],
              ["APP-0010", "0 / 0 / 0 / 9 / 0", "Poor scan, low confidence throughout",
               "Everything flagged, nothing wrong. This is the system behaving "
               "exactly as designed."],
          ],
          widths=[0.72, 1.42, 1.75, 3.0], mono_cols=(0, 1), size=8.5)

    h2(doc, "Three design decisions worth arguing about")
    bullet(doc, "APP-0005 surfaces both values and flags. A system that quietly "
                "picks one is making an underwriting call it has no authority to "
                "make.", bold_prefix="Conflicts are never resolved automatically. ")
    bullet(doc, "Guessing gets a claim dated four weeks in the wrong direction, and "
                "nothing about the record looks wrong afterwards.",
           bold_prefix="Ambiguous dates return nothing. ")
    bullet(doc, "You need the verification verdict, not the passport. Look at "
                "FORBIDDEN_PREFIXES in extract.py — a scope gate that runs before "
                "anything else.",
           bold_prefix="Identity-document data is dropped before the profile. ")

    # =====================================================================
    h1(doc, "6 · What confidence is, and what it is not")
    # =====================================================================
    callout(doc, "The one sentence to remember.",
            "Confidence is the model's certainty about what it saw. It is not a "
            "statement about whether the value is correct.",
            fill="EAF2F5", accent=TEAL)
    para(doc, "Everything in this lab follows from that distinction. A model can be "
              "completely certain it read the character O — and be completely wrong "
              "that an O belongs there. It has no idea what a policy number is "
              "supposed to look like. You do.")
    para(doc, "So confidence is useful for one job: deciding whether the OCR is "
              "struggling. It is useless for deciding whether a value is valid. "
              "Those need different mechanisms, and conflating them is the mistake "
              "the brief's single 0.85 threshold makes.")

    # =====================================================================
    h1(doc, "7 · The other two outputs")
    # =====================================================================
    h2(doc, "field_results.csv — 270 rows")
    para(doc, "Every field, every document, every policy. Columns: policy, document, "
              "field, raw, value, confidence, page, verdict, notes. This is where you "
              "go to answer 'which fields would a tighter threshold have flagged?' "
              "without re-running anything — sort by confidence and read.")
    rich(doc, [
        ("One caveat: ", {}),
        ("verdict", {"font": "Consolas", "size": 10}),
        (" holds ", {}),
        ("accept", {"font": "Consolas", "size": 10}),
        (" or ", {}),
        ("flag", {"font": "Consolas", "size": 10}),
        (" — it does not record correctness, because the pipeline does not know the "
         "truth. Correctness is computed against ground truth only in the report. "
         "Do not go looking for a 'wrong' verdict in the CSV; there isn't one, and "
         "there shouldn't be.", {}),
    ])

    h2(doc, "profiles/APP-XXXX.json — what your Function would return")
    para(doc, "The deliverable shape. Five keys are worth knowing:")
    table(doc,
          ["Key", "What it holds", "Why it exists"],
          [
              ["decision_profile", "The accepted fields",
               "What downstream systems consume"],
              ["review_queue", "Flagged fields with confidence, page and the "
               "validator note", "What a handler works through"],
              ["missing_required", "Required fields never extracted",
               "Visibly incomplete beats silently wrong"],
              ["conflicts", "Fields with more than one candidate value",
               "Kept separate from flags — a different decision for a human"],
              ["dropped", "Fields removed before the profile was built, with a reason",
               "The audit trail for the identity-document scope gate"],
          ],
          widths=[1.5, 2.4, 2.6], mono_cols=(0,))
    rich(doc, [
        ("Open ", {}),
        ("out/mock/profiles/APP-0007.json", {"font": "Consolas", "size": 10}),
        (" and read the ", {}),
        ("dropped", {"font": "Consolas", "size": 10}),
        (" array. Four licence fields, each with reason ", {}),
        ("identity_evidence_out_of_scope", {"font": "Consolas", "size": 10}),
        (". When somebody asks where your data-minimisation decision is recorded, "
         "that array is the answer — not a paragraph in a design document.", {}),
    ])

    # =====================================================================
    h1(doc, "8 · If you ran it against Azure")
    # =====================================================================
    para(doc, "The azure path answers one question: how much of what you concluded "
              "survives contact with the real service? Figures below are from a live "
              "run on 2026-08-16 against a free-tier (F0) resource.")
    table(doc,
          ["Policy", "Mock (correct / WRONG / flag)", "Azure (correct / WRONG / flag)"],
          [
              ["flat_0.85", "60 / 5 / 15", "71 / 6 / 0"],
              ["flat_0.85_checked", "56 / 0 / 26", "67 / 1 / 11"],
              ["tiered", "40 / 0 / 42", "66 / 1 / 12"],
          ],
          widths=[1.9, 2.3, 2.3], mono_cols=(0, 1, 2))

    h2(doc, "Finding 1 — the threshold policy became completely inert")
    rich(doc, [
        ("Look at the flag column for ", {}),
        ("flat_0.85", {"font": "Consolas", "size": 10}),
        (" on azure: ", {}),
        ("zero", {"bold": True}),
        (". Every single value the service returned scored above 0.85, so a "
         "threshold-only policy flagged nothing at all — and let ", {}),
        ("more", {"italic": True}),
        (" wrong values through than the mock did, not fewer. Higher confidence made "
         "the outcome worse.", {}),
    ])
    para(doc, "APP-0003's vehicle registration is the clean example. The mock scored "
              "it 0.83 and flagged it. The service scored it 1.00 and accepted it — "
              "and it is wrong (BD1B MHK, where the truth is BD18 MHK). Confidence "
              "moved a wrong value out of the review queue and into the accepted set. "
              "If your recommendation to the client rests on a confidence threshold, "
              "this is the slide that kills it.")

    h2(doc, "Finding 2 — the free tier reads two pages and says nothing")
    para(doc, "An F0 resource analyses the first two pages of every document and "
              "stops. HTTP 200, no error, no warnings member, a pages array that "
              "simply ends. Three documents here are longer than two pages:")
    table(doc,
          ["Doc", "Pages sent", "Pages read", "What was lost"],
          [
              ["APP-0005", "3", "2", "The conflicting repair total on page 3"],
              ["APP-0007", "3", "2", "The entire driving-licence page"],
              ["APP-0010", "4", "2", "Part of the poor-scan document"],
          ],
          widths=[1.0, 1.1, 1.1, 3.3], mono_cols=(0, 1, 2))
    rich(doc, [
        ("The lab detects this and prints a ", {}),
        ("PAGES THE SERVICE DID NOT READ", {"bold": True}),
        (" block. If you see it, two consequences follow. First, those fields report "
         "as ", {}),
        ("missing", {"font": "Consolas", "size": 10}),
        (", which is the wrong word — nobody showed the model the page, so it is not "
         "an extraction failure. Second, the one wrong acceptance that survives the "
         "checked policies on azure is APP-0005's repair total, and that is ", {}),
        ("this artefact, not a validator failure", {"bold": True}),
        (": the conflicting value was on page 3, so the pipeline saw one value and "
         "correctly accepted it. Upgrade to S0 and the conflict check fires again.", {}),
    ])

    h2(doc, "Finding 3 — two bugs only the real service could have shown you")
    bullet(doc, "Document Intelligence reads a page as words, and the final full stop "
                "of an abbreviation is its own word — so D.O.B.: comes back as "
                "'D.O.B .:'. Strip only the colon and the label is unmapped, and the "
                "field reports missing with no error. It cost three fields on "
                "APP-0002.", bold_prefix="Punctuation spacing. ")
    bullet(doc, "prebuilt-document was retired in the 2024-11-30 GA API; key-value "
                "extraction moved to prebuilt-layout behind an opt-in "
                "features=keyValuePairs flag. Omit it and you get HTTP 200, no error, "
                "and zero fields.", bold_prefix="The model and the route moved. ")
    para(doc, "Neither could have been caught by the mock, because mock labels are "
              "clean strings and only the live service tokenises. That is the "
              "argument for spending the few pence early rather than deferring the "
              "integration until the logic is 'finished'.", italic=True)

    h2(doc, "What you are not entitled to quote")
    callout(doc, "Say this before anyone asks.",
            "These PDFs are digitally generated — the text is embedded, not scanned. "
            "The service reads them far more confidently than it would read the forms "
            "this lab models. Mean confidence measured 0.990 against the mock's 0.892. "
            "That is a ceiling, not an estimate.")
    bullet(doc, "It is rendered faint and skewed, but the text is still embedded, so "
                "it comes back clean. Any claim about scan quality needs the client's "
                "own scans.", bold_prefix="APP-0010 does not come back low-confidence. ")
    bullet(doc, "You cannot fake it with an embedded font, and pretending otherwise "
                "in front of a client is how you lose the room.",
           bold_prefix="Handwriting is not simulated. ")
    bullet(doc, "CLM-2026-45O3 is rendered with a letter O and comes back with a "
                "letter O, at high confidence. Nothing errored, nothing scored low, "
                "and only the pattern check knows it is wrong. That is the whole "
                "lesson, holding up against the real service.",
           bold_prefix="What does survive the round trip: ")

    # =====================================================================
    h1(doc, "9 · Four conclusions people draw that are wrong")
    # =====================================================================
    table(doc,
          ["The tempting reading", "Why it is wrong", "What to say instead"],
          [
              ["\"Accuracy is 80%, so the pipeline is 80% good.\"",
               "60 correct out of 75 accepted ignores the 15 flagged, the 3 missing, "
               "and — critically — treats the 5 wrong acceptances as ordinary errors. "
               "They are not ordinary; they are invisible.",
               "Quote wrong-accepted and flag rate separately. One is a risk, the "
               "other is a cost."],
              ["\"Raise the threshold and it gets safer.\"",
               "Measured here: tiered thresholds removed zero additional wrong "
               "acceptances and added sixteen flags. On azure the threshold policy "
               "flagged nothing at all.",
               "The validators did the work. Show the three-row comparison."],
              ["\"Azure did better — 71 correct against 60.\"",
               "It also accepted six wrong values instead of five and flagged none. "
               "More correct and more wrong at once, because everything cleared the "
               "threshold.",
               "Compare wrong-accepted first. Higher confidence is not higher "
               "accuracy."],
              ["\"Those missing fields mean the extraction failed.\"",
               "On F0, fields after page two were never read. That is a pricing "
               "tier, not a model capability.",
               "Check for the PAGES THE SERVICE DID NOT READ block before "
               "attributing any missing field."],
          ],
          widths=[1.9, 2.6, 2.1], size=8.5)

    # =====================================================================
    h1(doc, "10 · Turning this into your deliverable")
    # =====================================================================
    rich(doc, [
        ("The report ends with six questions. They are the deliverable — the table is "
         "just the evidence. Before you present, you should be able to answer every "
         "line below without opening ", {}),
        ("run_lab.py", {"font": "Consolas", "size": 10}),
        (".", {}),
    ])
    for text in [
        "You ran flat_0.85 first and can name what it let through.",
        "You can explain APP-0003 in one sentence.",
        "You can say whether the checks or the thresholds did the work, and why.",
        "You have a per-field threshold proposal with a reason for each tier.",
        "You know your flag rate, and what it costs at 4,000 documents a month.",
        "You can say where the identity-document decision is recorded.",
        "If you ran azure: you have read confidence_delta.md, you can say which of "
        "your mock conclusions survived, and which numbers you are not entitled to "
        "quote from it.",
    ]:
        bullet(doc, text, bold_prefix="☐  ")

    para(doc, "")
    callout(doc, "The sentence your client needs to hear.",
            "Confidence tells you how sure the model is about what it saw. It cannot "
            "tell you whether the value is right. Everything that made this pipeline "
            "safe came from knowing what the data is supposed to look like — and that "
            "knowledge is yours, not the model's.",
            fill="EAF2F5", accent=TEAL)

    # ---- glossary --------------------------------------------------------
    h1(doc, "Glossary")
    table(doc,
          ["Term", "Meaning here"],
          [
              ["Wrong accepted", "A field written into the decision profile whose "
               "value is wrong. The only outcome that can reach a customer unnoticed."],
              ["Flag", "A field routed to a human. Costs roughly 30 seconds; costs "
               "nothing downstream."],
              ["Validator / check", "A rule that knows the shape of the data — an "
               "identifier pattern, an arithmetic reconciliation, a date-format test."],
              ["Tiered threshold", "Different confidence bars for different fields; "
               "identifiers at 0.97, names at 0.80."],
              ["Ground truth", "What the value actually is. Available here because "
               "the documents are synthetic; never available in production, which is "
               "why validators matter more than metrics."],
              ["prebuilt-layout", "The Document Intelligence model that does key-value "
               "extraction, behind features=keyValuePairs, from api-version "
               "2024-11-30."],
              ["F0 / S0", "Free and Standard pricing tiers. F0 analyses the first two "
               "pages of a document only, and does not say so."],
          ],
          widths=[1.6, 4.9])

    doc.save(str(OUT))
    return OUT


if __name__ == "__main__":
    path = build()
    print("wrote %s (%.1f KB)" % (path, path.stat().st_size / 1024))
