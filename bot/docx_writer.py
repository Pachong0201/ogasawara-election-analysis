"""Minimal OOXML (.docx) writer for bot reports.

Converts the markdown-flavoured report text produced by the report writers
into a small Word document without external dependencies: ``**bold**`` lines
become headings, ``- `` lines become bullets, everything else becomes a
paragraph.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import List, Tuple

from xml.sax.saxutils import escape


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def _runs(text: str) -> List[Tuple[str, str]]:
    """Split a line into (kind, text) runs; ``**bold**`` markers become 'b'."""
    runs: List[Tuple[str, str]] = []
    for part in re.split(r"(\*\*[^*]+\*\*)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            runs.append(("b", part[2:-2]))
        else:
            runs.append(("", part))
    return runs


def _run_xml(text: str, bold: bool = False, size: int | None = None) -> str:
    props: List[str] = []
    if bold:
        props.append("<w:b/>")
    if size:
        props.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _para(
    text: str,
    *,
    bullet: bool = False,
    bold: bool = False,
    size: int | None = None,
    spacing: bool = True,
) -> str:
    ppr = "<w:pPr>"
    if spacing:
        ppr += '<w:spacing w:after="120"/>'
    if bullet:
        ppr += '<w:ind w:left="420" w:hanging="280"/>'
    ppr += "</w:pPr>"
    body: List[str] = [ppr]
    if bullet:
        body.append(_run_xml("• ", bold=False))
    for kind, value in _runs(text):
        body.append(_run_xml(value, bold=bold or kind == "b", size=size))
    return f'<w:p>{"".join(body)}</w:p>'


def build_docx(text: str, title: str = "选情分析报告") -> bytes:
    """Serialise report ``text`` (markdown-flavoured) into .docx bytes."""
    parts: List[str] = [_para(title, bold=True, size=32)]
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            parts.append('<w:p><w:pPr><w:spacing w:after="60"/></w:pPr></w:p>')
        elif stripped.startswith("- "):
            parts.append(_para(stripped[2:].strip(), bullet=True))
        elif stripped.startswith("**") and stripped.endswith("**") and len(stripped) > 4:
            parts.append(_para(stripped[2:-2], bold=True, size=26))
        else:
            parts.append(_para(stripped))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{"".join(parts)}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>'
        "</w:body></w:document>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _ROOT_RELS)
        zf.writestr("word/document.xml", document)
    return out.getvalue()
