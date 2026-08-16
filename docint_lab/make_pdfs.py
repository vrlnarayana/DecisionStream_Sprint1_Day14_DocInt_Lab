"""
make_pdfs.py — render the ten synthetic documents as actual PDF files.

WHY THIS EXISTS
---------------
`mock_docint.py` holds the ten documents as the SHAPE Document Intelligence
returns. That is enough to learn the pipeline, and it is all the mock provider
needs.

The moment you point the lab at the real service, you need something to point
it AT. Azure will not accept a Python dictionary. So this module renders the
same ten documents — the same labels, the same values, the same page numbers —
as real PDFs on disk, and the azure provider posts those.

    mock_docint.DOCUMENTS  --render-->  docs/APP-XXXX.pdf  --POST-->  Azure DI

One source of truth, two providers. If you edit a document in mock_docint.py,
re-run `--make-pdfs` and the real path picks the change up too.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
These are DIGITAL PDFs — the text is embedded, not scanned. That matters, and
it is the honest limitation to state before you show anyone the azure run:

  * The OCR-confusion documents still carry their wrong characters, because we
    render the value Document Intelligence *saw* ("CLM-2026-45O3"), not the
    value the form *said*. The service will read that string back confidently.
    That is the right outcome: the lesson is that a confident read of a wrong
    character is indistinguishable from a confident read of a right one, and
    only the pattern check separates them.

  * APP-0010's whole purpose is LOW confidence, and digital text does not
    produce low confidence. It is rendered faint, small and skewed to push the
    scores down, but do not promise a number you have not measured. Run it,
    read `out/azure/confidence_delta.md`, and quote what actually came back.

  * Handwriting is not simulated. You cannot fake it with an embedded font,
    and pretending otherwise in front of a client is how you lose the room.

NO DEPENDENCIES
---------------
Standard library only — this writes the PDF bytes directly. A PDF is a handful
of objects, a cross-reference table of byte offsets, and a trailer. It is worth
reading once, because "the file format is just a format" is a useful thing to
have felt at least once in your career.
"""

from __future__ import annotations

from pathlib import Path

import mock_docint


PAGE_W, PAGE_H = 595, 842          # A4 in points
MARGIN = 64


# ==========================================================================
# 1. A MINIMAL PDF WRITER
# ==========================================================================
def _esc(s: str) -> str:
    """Escape a string for a PDF literal. Backslash first, or you escape
    your own escapes."""
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


class _Text:
    """One run of text to place on a page."""

    def __init__(self, x, y, s, size=11, bold=False, gray=0.0, skew=0.0):
        self.x, self.y, self.s = x, y, s
        self.size, self.bold, self.gray, self.skew = size, bold, gray, skew

    def ops(self) -> str:
        font = "/F2" if self.bold else "/F1"
        # Text matrix: [a b c d e f]. `c` is the horizontal skew — a tiny
        # value makes the line lean, the way a page fed crooked into a
        # scanner does.
        return (
            f"BT {self.gray:.2f} g {font} {self.size} Tf "
            f"1 0 {self.skew:.3f} 1 {self.x:.1f} {self.y:.1f} Tm "
            f"({_esc(self.s)}) Tj ET\n"
        )


def _build_pdf(pages: list[list[_Text]]) -> bytes:
    """pages[i] is the list of text runs on page i. Returns the PDF bytes."""
    objects: list[bytes] = []          # 1-indexed on write

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)            # this object's number

    # Reserve 1 = catalog, 2 = page tree; we know their numbers up front but
    # cannot write them until the kids exist.
    objects.append(b"")                # 1 catalog placeholder
    objects.append(b"")                # 2 pages placeholder

    f_regular = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                    b"/Encoding /WinAnsiEncoding >>")
    f_bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
                 b"/Encoding /WinAnsiEncoding >>")

    kids = []
    for runs in pages:
        stream = "".join(r.ops() for r in runs).encode("latin-1", "replace")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream)
                      + stream + b"\nendstream")
        page = add(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
            b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> "
            b"/Contents %d 0 R >>"
            % (PAGE_W, PAGE_H, f_regular, f_bold, content)
        )
        kids.append(page)

    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = (b"<< /Type /Pages /Kids [%s] /Count %d >>"
                  % (b" ".join(b"%d 0 R" % k for k in kids), len(kids)))

    # Serialise, recording the byte offset of every object for the xref table.
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"

    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref_at))
    return bytes(out)


# ==========================================================================
# 2. THE FORM LAYOUT
#
# "Label: Value" on one line is what Document Intelligence's key-value pair
# extraction is built to recognise. Every alias in extract.LABEL_MAP travels
# through to the real service unchanged, because it is the form writer's
# label that gets rendered — not our canonical field name.
# ==========================================================================
DECLARATION = [
    "I declare that the information given on this form is true and complete to",
    "the best of my knowledge. I understand that giving false information may",
    "invalidate my policy and may be reported to the relevant authorities.",
    "",
    "Signature: ______________________     Date: ______________________",
]


# A form does not print the word "selected" next to a tickbox — it prints a
# tickbox. The mock stores DI's OWN representation of a ticked box, so render
# it back into the thing the service would be looking at. Whatever the service
# then calls it, `azure_docint._normalise_selection` translates back.
def _on_page(value) -> str:
    v = str(value).strip().lower()
    if v in ("selected", ":selected:"):
        return "[X]"
    if v in ("unselected", ":unselected:"):
        return "[ ]"
    return str(value)


def _render(doc: dict) -> bytes:
    faint = doc["id"] == "APP-0010"        # the poor-scan document
    size = 9 if faint else 11
    gray = 0.55 if faint else 0.0
    skew = 0.035 if faint else 0.0

    n_pages = max(doc["pages"], max((kv.get("page", 1) for kv in doc["kv"]), default=1))
    pages: list[list[_Text]] = [[] for _ in range(n_pages)]

    for i, runs in enumerate(pages, start=1):
        y = PAGE_H - MARGIN
        runs.append(_Text(MARGIN, y, "CASE APPLICATION FORM", 16, bold=True, gray=gray, skew=skew))
        y -= 18
        runs.append(_Text(MARGIN, y, "DecisionStream AI  -  Motor Claims Intake",
                          10, gray=min(gray + 0.3, 0.7), skew=skew))
        y -= 14
        runs.append(_Text(MARGIN, y, f"Document {doc['id']}          Page {i} of {n_pages}",
                          10, gray=min(gray + 0.3, 0.7), skew=skew))
        runs.append(_Text(MARGIN, y - 12, "_" * 74, 10, gray=min(gray + 0.4, 0.75), skew=skew))

    # Place every key-value pair on the page the mock says it came from.
    cursors = {i: PAGE_H - MARGIN - 78 for i in range(1, n_pages + 1)}
    for kv in doc["kv"]:
        p = kv.get("page", 1)
        y = cursors[p]
        pages[p - 1].append(
            _Text(MARGIN, y, f"{kv['label']}: {_on_page(kv['value'])}",
                  size, gray=gray, skew=skew)
        )
        cursors[p] = y - (22 if not faint else 20)

    # The declaration goes on the last page, well clear of the fields, so the
    # service has some ordinary prose to read as well as the form pairs.
    last = pages[-1]
    y = min(cursors[n_pages] - 30, 240)
    last.append(_Text(MARGIN, y, "DECLARATION", 11, bold=True, gray=gray, skew=skew))
    for line in DECLARATION:
        y -= 16
        last.append(_Text(MARGIN, y, line, 9, gray=min(gray + 0.1, 0.6), skew=skew))

    return _build_pdf(pages)


# ==========================================================================
# 3. ENTRY POINT
# ==========================================================================
def pdf_path(doc_dir: Path, doc_id: str) -> Path:
    return doc_dir / f"{doc_id}.pdf"


def make_all(doc_dir: Path, quiet: bool = False) -> list[Path]:
    doc_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for doc in mock_docint.DOCUMENTS:
        path = pdf_path(doc_dir, doc["id"])
        path.write_bytes(_render(doc))
        written.append(path)
        if not quiet:
            print(f"  wrote {path.name:<14} {doc['pages']} page(s)  "
                  f"{len(doc['kv'])} field(s)  {doc['note']}")
    return written
