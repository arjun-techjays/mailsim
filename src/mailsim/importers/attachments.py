"""Deterministic document writers and text extraction for generated attachments.

* `pdf_bytes`: a small, valid PDF 1.4 (standard Type1 fonts, one uncompressed content stream
  per page, a correct cross-reference table) from a title and pages of styled lines. No
  timestamps or random ids beyond what the caller passes, so the same input gives the same
  bytes.
* `xlsx_bytes`: a minimal Office Open XML workbook (one sheet, inline strings and numbers),
  stored without compression and with fixed zip timestamps (byte-identical across platforms).
* `extract_text`: the text of a PDF (literal strings in content streams, plain or
  FlateDecode), an XLSX (cell text) or any text file, used by the PII scanner and the tests.

Pure standard library: the committed fixtures need nothing extra to load.
"""

from __future__ import annotations

import html
import io
import re
import textwrap
import zipfile
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from xml.sax.saxutils import escape

PAGE_W, PAGE_H = 612, 792  # US Letter, points
MARGIN = 72
# (font resource, size, leading, wrap width in characters)
STYLES: dict[str, tuple[str, int, int, int]] = {
    "title": ("F2", 22, 30, 44),
    "h1": ("F2", 15, 22, 60),
    "h2": ("F2", 12, 18, 74),
    "p": ("F1", 10, 14, 92),
    "small": ("F1", 8, 11, 115),
    "mono": ("F3", 9, 12, 76),
}
_ASCII = str.maketrans({"–": "-", "—": "-", "‘": "'", "’": "'",
                        "“": '"', "”": '"', "•": "-", " ": " "})  # fmt: skip


@dataclass(frozen=True)
class Line:
    style: str
    text: str


def _ascii(text: str) -> str:
    return text.translate(_ASCII).encode("ascii", "replace").decode("ascii")


def _pdf_string(text: str) -> str:
    out = _ascii(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"({out})"


def _layout(pages: Sequence[Sequence[Line]], footer: str) -> list[bytes]:
    """Content streams: wrapped lines, flowing onto extra pages when a page overflows."""
    streams: list[bytes] = []
    for page in pages:
        chunks: list[list[str]] = [[]]
        y = PAGE_H - MARGIN
        for line in page:
            font, size, lead, width = STYLES.get(line.style, STYLES["p"])
            wrapped = textwrap.wrap(line.text, width) if line.text.strip() else [""]
            if line.style == "mono":
                wrapped = [line.text[:width]]
            for part in wrapped:
                if y - lead < MARGIN + 24:
                    chunks.append([])
                    y = PAGE_H - MARGIN
                y -= lead
                chunks[-1].append(f"BT /{font} {size} Tf {MARGIN} {y} Td {_pdf_string(part)} Tj ET")
        for chunk in chunks:
            streams.append("\n".join(chunk).encode("ascii"))
    total = len(streams)
    out = []
    for no, body in enumerate(streams, start=1):
        foot = (
            f"BT /F1 8 Tf {MARGIN} 40 Td {_pdf_string(f'{footer}  |  Page {no} of {total}')} Tj ET"
        )
        out.append(body + b"\n" + foot.encode("ascii"))
    return out


def pdf_bytes(
    pages: Sequence[Sequence[Line]],
    *,
    title: str,
    author: str,
    created: str,
    footer: str = "",
    producer: str = "Document Services",
) -> bytes:
    """A valid multi-page PDF. `created` is a PDF date string body (``20261014093000``)."""
    streams = _layout(pages, footer or title)
    objects: list[bytes] = []
    n_pages = len(streams)
    first_page = 7
    kids = " ".join(f"{first_page + 2 * i} 0 R" for i in range(n_pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    for base in ("Helvetica", "Helvetica-Bold", "Courier"):
        objects.append(
            f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} /Encoding /WinAnsiEncoding >>".encode()
        )
    info = (f"<< /Title {_pdf_string(title)} /Author {_pdf_string(author)} "
            f"/Producer {_pdf_string(producer)} /CreationDate (D:{created}Z) >>")  # fmt: skip
    objects.append(info.encode())
    for i, stream in enumerate(streams):
        content_no = first_page + 2 * i + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                f"/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> "
                f"/Contents {content_no} 0 R >>"
            ).encode()  # fmt: skip
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for no, body in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f"{no} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = buf.tell()
    buf.write(f"xref\n0 {len(objects) + 1}\n".encode())
    buf.write(b"0000000000 65535 f \n")
    for off in offsets:
        buf.write(f"{off:010d} 00000 n \n".encode())
    buf.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    return buf.getvalue()


# ------------------------------------------------------------------------------ xlsx

_FIXED = (2026, 1, 1, 0, 0, 0)


def _col(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def xlsx_bytes(rows: Sequence[Sequence[str | float | int]], *, sheet: str = "Sheet1") -> bytes:
    """A minimal one-sheet workbook (strings inline, numbers as numbers)."""
    cells = []
    for r, row in enumerate(rows, start=1):
        parts = []
        for c, value in enumerate(row):
            ref = f"{_col(c)}{r}"
            if isinstance(value, int | float) and not isinstance(value, bool):
                parts.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                parts.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        cells.append(f'<row r="{r}">{"".join(parts)}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(cells)}</sheetData></worksheet>"
    )
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/'
            'sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.'
            'worksheet+xml"/></Types>'
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{escape(sheet[:31])}" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/worksheets/sheet1.xml": sheet_xml,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, data in files.items():
            info = zipfile.ZipInfo(name, date_time=_FIXED)
            info.external_attr = 0o644 << 16
            zf.writestr(info, data.encode("utf-8"))
    return buf.getvalue()


# --------------------------------------------------------------------------- extract
# Hardened, linear-time extraction for scanning untrusted attachments: size caps on the input,
# on every inflated stream and on every zip member, a compression-ratio check against zip
# bombs, and no backtracking regular expressions over whole files.

MAX_ATTACHMENT = 16 * 1024 * 1024  # bytes of one attachment / scanned file
MAX_INFLATE = 8 * 1024 * 1024  # bytes of one inflated PDF stream or zip member
MAX_TOTAL = 32 * 1024 * 1024  # inflated bytes per attachment
MAX_RATIO = 200  # zip member: uncompressed / compressed size
MAX_MEMBERS = 2000


class UnsafeAttachment(ValueError):
    """The attachment exceeds a size / ratio limit and was not parsed (a scanner finding)."""


class UnreadableAttachment(UnsafeAttachment):
    """The attachment is corrupt or uses an encoding the scanner cannot read (a finding: its
    text was not checked)."""


# image filters carry no text; any other filter the scanner cannot decode makes it unreadable
_IMAGE_FILTERS = (b"/DCTDecode", b"/JPXDecode", b"/CCITTFaxDecode", b"/JBIG2Decode")
_FILTER = re.compile(rb"/Filter\s*\[?\s*/(\w+)")


_LITERAL = re.compile(rb"\((?:\\.|[^\\()])*\)", re.S)
_HEX = re.compile(rb"(?<![<\w])<([0-9A-Fa-f\s]{2,})>(?!>)")
_UNESCAPE = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}


def _decode_pdf_string(raw: bytes) -> str:
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "replace")
    return raw.decode("latin-1")


def _unescape(raw: bytes) -> str:
    out = bytearray()
    i = 0
    while i < len(raw):
        ch = raw[i : i + 1]
        if ch == b"\\" and i + 1 < len(raw):
            nxt = raw[i + 1 : i + 2]
            if nxt in _UNESCAPE:
                out += _UNESCAPE[nxt]
                i += 2
                continue
            octal = re.match(rb"[0-7]{1,3}", raw[i + 1 : i + 4])
            if octal:
                out.append(int(octal.group(0), 8) & 0xFF)
                i += 1 + len(octal.group(0))
                continue
            out += nxt
            i += 2
            continue
        out += ch
        i += 1
    return _decode_pdf_string(bytes(out))


def _strings(region: bytes) -> list[str]:
    """Literal ``(...)`` and hex ``<...>`` strings of a PDF region."""
    out = [_unescape(s[1:-1]) for s in _LITERAL.findall(region)]
    for h in _HEX.findall(region):
        digits = re.sub(rb"\s", b"", h)
        if len(digits) % 2:
            digits += b"0"
        out.append(_decode_pdf_string(bytes.fromhex(digits.decode("ascii"))))
    return out


def _inflate(data: bytes, budget: list[int]) -> bytes:
    d = zlib.decompressobj()
    try:
        out = d.decompress(data, MAX_INFLATE)
    except zlib.error as exc:
        raise UnreadableAttachment("a compressed PDF stream is corrupt") from exc
    if d.unconsumed_tail:
        raise UnsafeAttachment("a compressed PDF stream inflates beyond the size limit")
    budget[0] -= len(out)
    if budget[0] < 0:
        raise UnsafeAttachment("compressed PDF streams inflate beyond the size limit")
    return out


# attributes that only carry package structure (cell references, ids, types, the XML declaration)
_STRUCTURAL = frozenset(
    ["version", "encoding", "standalone", "r", "t", "s", "sheetId", "Id", "r:id", "PartName",
     "Extension", "ContentType", "Type", "spans", "xml:space"]
)  # fmt: skip


_TAG = re.compile(r"<[^<>]*>")
_ATTR = re.compile(r"(?<![\w:.-])([\w:.-]+)=\"([^\"]*)\"")


def _xml_text(xml: str) -> list[str]:
    """Text nodes and attribute values of an XML part (namespace URIs and MIME types skipped)."""
    out = [html.unescape(t.strip()) for t in re.findall(r">([^<>]+)<", xml) if t.strip()]
    pairs = [p for tag in _TAG.findall(xml) for p in _ATTR.findall(tag)]  # attributes in tags only
    for name, value in pairs:
        if name.startswith("xmlns") or name in _STRUCTURAL or not value.strip():
            continue
        if re.match(r"(?:https?://(?:schemas\.|purl\.org|www\.w3\.org|ns\.adobe\.com)|urn:)|"
                    r"(?:application|image|text|audio|video)/", value):  # fmt: skip
            continue
        out.append(html.unescape(value))
    return out


def pdf_text(data: bytes) -> str:
    """Every string of a PDF: text in content streams (plain or FlateDecode), XMP / XML
    metadata streams, and every string outside streams (the Info dictionary, annotations,
    outlines). One scan over the file, no backtracking."""
    if len(data) > MAX_ATTACHMENT:
        raise UnsafeAttachment("PDF larger than the size limit")
    lines: list[str] = []
    outside: list[bytes] = []
    budget = [MAX_TOTAL]
    pos = 0
    while True:
        s = data.find(b"stream", pos)
        while s >= 3 and data[s - 3 : s] == b"end":
            s = data.find(b"stream", s + 6)
        if s < 0:
            outside.append(data[pos:])
            break
        start = s + 6
        if data[start : start + 2] == b"\r\n":
            start += 2
        elif data[start : start + 1] in (b"\n", b"\r"):
            start += 1
        end = data.find(b"endstream", start)
        if end < 0:
            outside.append(data[pos:])
            break
        head_from = data.rfind(b"obj", max(pos, s - 4096), s)
        header = data[head_from if head_from >= 0 else max(pos, s - 4096) : s]
        outside.append(data[pos:s])
        body = data[start:end].rstrip(b"\r\n")
        filt = _FILTER.search(header)
        if filt and filt.group(1) == b"FlateDecode":
            body = _inflate(body, budget)
        elif filt and not any(f in header for f in _IMAGE_FILTERS):
            raise UnreadableAttachment(f"PDF stream filter {filt.group(1).decode()} not decoded")
        elif filt:  # an image: no text to read
            pos = end + 9
            continue
        if b"/Metadata" in header or body.lstrip()[:9] in (
            b"<?xpacket",
            b"<x:xmpmet",
            b"<?xml ver",
        ):
            lines.extend(_xml_text(body.decode("utf-8", "replace")))
        else:
            lines.extend(_strings(body))
        pos = end + 9
    for region in outside:
        lines.extend(_strings(region))
    return "\n".join(line for line in lines if line.strip())


def zip_text(data: bytes) -> str:
    """Text of every XML part of an OOXML package (sheets, shared strings, docx body,
    comments, drawings, docProps) after size and compression-ratio checks."""
    if len(data) > MAX_ATTACHMENT:
        raise UnsafeAttachment("archive larger than the size limit")
    out: list[str] = []
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise UnsafeAttachment("archive has too many members")
        for info in sorted(infos, key=lambda i: i.filename):
            if info.is_dir():
                continue
            if info.file_size > MAX_INFLATE or (
                info.compress_size and info.file_size / info.compress_size > MAX_RATIO
            ):
                raise UnsafeAttachment(f"archive member {info.filename!r} exceeds the limits")
            total += info.file_size
            if total > MAX_TOTAL:
                raise UnsafeAttachment("archive inflates beyond the size limit")
            with zf.open(info) as fh:
                raw = fh.read(MAX_INFLATE + 1)
            if len(raw) > MAX_INFLATE:
                raise UnsafeAttachment(f"archive member {info.filename!r} exceeds the limits")
            name = info.filename.lower()
            if name.endswith((".xml", ".rels", ".vml")):
                out.extend(_xml_text(raw.decode("utf-8", "replace")))
            elif name.endswith((".txt", ".csv")):
                out.append(raw.decode("utf-8", "replace"))
    return "\n".join(out)


def xlsx_text(data: bytes) -> str:
    """Text of an XLSX / DOCX (kept for callers; see `zip_text`)."""
    return zip_text(data)


def extract_text(data: bytes, filename: str = "", content_type: str = "") -> str:
    """Text of an attachment for scanning: PDF, any zip package (XLSX, DOCX, PPTX), otherwise
    the decoded bytes. Raises `UnsafeAttachment` for input beyond the size / ratio limits."""
    if len(data) > MAX_ATTACHMENT:
        raise UnsafeAttachment("attachment larger than the size limit")
    lower = filename.lower()
    if data.startswith(b"%PDF") or lower.endswith(".pdf") or content_type == "application/pdf":
        return pdf_text(data)
    if data.startswith(b"PK\x03\x04") or lower.endswith((".xlsx", ".docx", ".pptx", ".zip")):
        try:
            return zip_text(data)
        except zipfile.BadZipFile as exc:
            raise UnreadableAttachment("corrupt archive") from exc
    return data.decode("utf-8", "replace")
