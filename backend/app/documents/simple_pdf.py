"""A dependency-free writer for simple text PDFs (used by the demo data generator).

Produces valid PDF 1.4 files with one or more pages of Helvetica text lines that pypdf (and any
other PDF text extractor) can read back line by line.
"""
from __future__ import annotations

LINES_PER_PAGE = 60
LINE_HEIGHT = 12
TOP_MARGIN = 800
LEFT_MARGIN = 40


def _escape(text: str) -> str:
    out = []
    for ch in text:
        if ch in "\\()":
            out.append("\\" + ch)
        elif ord(ch) < 32 or ord(ch) > 126:
            # WinAnsi-encode the rupee sign as "Rs." and drop other non-latin characters
            out.append("Rs." if ch == "₹" else ("-" if ch in "–—" else "?"))
        else:
            out.append(ch)
    return "".join(out)


def text_pdf(lines: list[str], title: str = "Document") -> bytes:
    pages = [lines[i:i + LINES_PER_PAGE] for i in range(0, max(len(lines), 1), LINES_PER_PAGE)] or [[]]
    objects: list[bytes] = []

    def add(obj: str | bytes) -> int:
        objects.append(obj.encode("latin-1") if isinstance(obj, str) else obj)
        return len(objects)

    font_id = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    page_ids: list[int] = []
    content_ids: list[int] = []
    pages_id_placeholder = len(objects) + 1 + 2 * len(pages)  # computed after pages/contents
    for page_lines in pages:
        stream_lines = ["BT", "/F1 9 Tf", f"{LEFT_MARGIN} {TOP_MARGIN} Td", f"{LINE_HEIGHT} TL"]
        for line in page_lines:
            stream_lines.append(f"({_escape(line)}) Tj T*")
        stream_lines.append("ET")
        stream = "\n".join(stream_lines).encode("latin-1")
        content_id = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        page_id = add(
            f"<< /Type /Page /Parent {pages_id_placeholder} 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        )
        content_ids.append(content_id)
        page_ids.append(page_id)
    pages_id = add(f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {len(page_ids)} >>")
    assert pages_id == pages_id_placeholder
    catalog_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")
    info_id = add(f"<< /Title ({_escape(title)}) /Producer (ASTRA Tax demo generator) >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R /Info {info_id} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
