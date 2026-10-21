"""Extract text from a submitted pitch deck (.pptx or .pdf).

.pptx is parsed with the standard library only (it is a zip of XML). PDF needs `pypdf`.
Uploads are untrusted: size, entry count and decompressed size are all capped.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import html
import io
import re
import zipfile

MAX_BYTES = 20 * 1024 * 1024
MAX_UNCOMPRESSED = 150 * 1024 * 1024
MAX_SLIDES = 120
MAX_CHARS = 60_000


class DeckError(Exception):
    pass


def decode_upload(filename: str, data_b64: str) -> tuple[str, bytes]:
    name = re.sub(r"[^\w.\- ]", "_", (filename or "deck").split("/")[-1].split("\\")[-1])[:120] or "deck"
    try:
        raw = base64.b64decode(data_b64.split(",", 1)[-1], validate=False)
    except (binascii.Error, ValueError):
        raise DeckError("The file could not be read. Try uploading it again.")
    if not raw:
        raise DeckError("The file is empty.")
    if len(raw) > MAX_BYTES:
        raise DeckError(f"The file is {len(raw) // 1024 // 1024} MB; the limit is {MAX_BYTES // 1024 // 1024} MB.")
    return name, raw


def _para_text(xml: str) -> str:
    lines = []
    for p in re.findall(r"<a:p[ >].*?</a:p>", xml, flags=re.S):
        t = "".join(re.findall(r"<a:t[^>]*>(.*?)</a:t>", p, flags=re.S))
        t = html.unescape(t).strip()
        if t:
            lines.append(t)
    return "\n".join(lines)


def parse_pptx(raw: bytes) -> list[dict]:
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise DeckError("This does not look like a valid .pptx file.")
    infos = z.infolist()
    if len(infos) > 5000 or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
        raise DeckError("This .pptx is too large to process safely.")
    num = lambda n: int(re.search(r"(\d+)\.xml$", n).group(1))
    slide_names = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)), key=num)
    if not slide_names:
        raise DeckError("No slides were found in this file.")
    notes = {num(n): n for n in z.namelist() if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", n)}
    slides = []
    for i, n in enumerate(slide_names[:MAX_SLIDES], 1):
        if z.getinfo(n).file_size > 8 * 1024 * 1024:
            continue
        text = _para_text(z.read(n).decode("utf-8", "replace"))
        note = ""
        if num(n) in notes and z.getinfo(notes[num(n)]).file_size < 2 * 1024 * 1024:
            note = _para_text(z.read(notes[num(n)]).decode("utf-8", "replace"))
        slides.append({"n": i, "text": text, **({"notes": note} if note else {})})
    return slides


def parse_pdf(raw: bytes) -> list[dict]:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise DeckError("PDF support needs the 'pypdf' package (pip install pypdf). A .pptx works without it.")
    try:
        r = PdfReader(io.BytesIO(raw))
        pages = r.pages[:MAX_SLIDES]
        return [{"n": i, "text": (p.extract_text() or "").strip()} for i, p in enumerate(pages, 1)]
    except Exception:  # noqa: BLE001 - pypdf raises many types on malformed files
        raise DeckError("This PDF could not be read. It may be damaged or password-protected.")


def parse(filename: str, raw: bytes) -> dict:
    if raw[:4] == b"%PDF":
        slides = parse_pdf(raw)
    elif raw[:2] == b"PK":
        slides = parse_pptx(raw)
    elif raw[:4] == b"\xd0\xcf\x11\xe0":
        raise DeckError("Old .ppt files are not supported. In PowerPoint use File > Save As > .pptx or PDF.")
    else:
        raise DeckError("Upload a .pptx or PDF file.")
    total, kept = 0, []
    for s in slides:
        t = s["text"]
        if total + len(t) > MAX_CHARS:
            t = t[: max(0, MAX_CHARS - total)]
        total += len(t)
        kept.append({**s, "text": t})
    if not any(s["text"].strip() for s in kept):
        raise DeckError("No text could be read from the slides. If they are images, add the key points as text or export a PDF with selectable text.")
    return {"filename": filename, "size": len(raw), "sha1": hashlib.sha1(raw).hexdigest(),
            "kind": "pdf" if raw[:4] == b"%PDF" else "pptx", "slides": kept, "chars": total}


def as_text(deck: dict | None, limit: int = 25_000) -> str:
    if not deck:
        return ""
    out = []
    for s in deck.get("slides", []):
        out.append(f"--- Slide {s['n']} ---\n{s['text']}" + (f"\n[speaker notes] {s['notes']}" if s.get("notes") else ""))
    return "\n".join(out)[:limit]
