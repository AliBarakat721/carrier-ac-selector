"""OCR front-end (no Qt): PDF text layer first, Tesseract second."""
from __future__ import annotations

import re

from PIL import Image, ImageOps

from core import parse_hap_text

try:
    import pytesseract
except ImportError:          # pragma: no cover
    pytesseract = None

try:
    import pymupdf as fitz   # modern name
    PDF_AVAILABLE = True
except ImportError:
    try:
        import fitz          # older pymupdf
        PDF_AVAILABLE = True
    except ImportError:      # pragma: no cover
        fitz = None
        PDF_AVAILABLE = False

# Tried in order; the first result that parses cleanly wins.
PSM_MODES = (4, 6, 11)

# ── RTF helpers (compiled once for speed) ─────────────
_CONTROL  = re.compile(r'\\([a-zA-Z]+)(-?\d+)?[ ]?')
_HEX_ESC  = re.compile(r"\\'([0-9a-fA-F]{2})")
_SKIP_GROUPS = frozenset({
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object",
    "themedata", "colorschememapping", "latentstyles", "datastore",
    "listtable", "listoverridetable", "rsidtbl", "generator", "filetbl",
    "header", "footer", "footnote", "annotation", "field",
})


class OCRUnavailable(RuntimeError):
    pass


def preprocess(image: Image.Image) -> Image.Image:
    """Grayscale + 3x upscale ONLY.

    Heavy contrast/sharpen/unsharp-mask turned HAP's dotted leader lines into
    blobs and made Tesseract misread the 'Total coil load' line.
    """
    g = ImageOps.grayscale(image.convert("RGB"))
    w, h = g.size
    return g.resize((w * 3, h * 3), Image.Resampling.LANCZOS)


def _score(text: str) -> float:
    r = parse_hap_text(text)
    return (r.btu > 0) + (r.cfm > 0) - 0.5 * len(r.warnings)


def ocr_image(image: Image.Image, progress=None) -> str:
    """Run Tesseract with several page-segmentation modes, keep the best."""
    if pytesseract is None:
        raise OCRUnavailable("pytesseract is not installed.")
    prepared = preprocess(image)
    best_text, best_score = "", float("-inf")
    for i, psm in enumerate(PSM_MODES):
        try:
            text = pytesseract.image_to_string(prepared, config=f"--psm {psm} --oem 3")
        except pytesseract.TesseractNotFoundError as exc:
            raise OCRUnavailable(
                "Tesseract OCR engine was not found.\n"
                "Install it (Windows: github.com/UB-Mannheim/tesseract, "
                "Linux: sudo dnf/apt install tesseract) and make sure it is on PATH."
            ) from exc
        s = _score(text)
        if s > best_score:
            best_text, best_score = text, s
        if progress:
            progress(30 + int(55 * (i + 1) / len(PSM_MODES)))
        if parse_hap_text(text).ok:
            break
    return best_text


def rtf_to_text(raw: str) -> str:
    """Fast RTF -> plain text. Handles multi-MB HAP reports (11 MB+).

    Key optimizations vs the naive version:
      * compiled regex + pattern.match(raw, i) -> no string slicing
      * bulk-copy plain text via str.find (C-level)
      * fast-skip of huge ignored groups (\\pict, \\fonttbl, ...)
    """
    out = []
    n = len(raw)
    i = 0
    skipping_depth = 0

    while i < n:
        # ── Fast-skip mode: inside a group we don't care about ──
        if skipping_depth > 0:
            c = raw[i]
            if c == "{":
                skipping_depth += 1
            elif c == "}":
                skipping_depth -= 1
            i += 1
            continue

        # ── Bulk-copy plain text until next { } \ ──
        nxt = n
        for ch in ("{", "}", "\\"):
            k = raw.find(ch, i)
            if k != -1 and k < nxt:
                nxt = k
        if nxt > i:
            out.append(raw[i:nxt])
            i = nxt
            if i >= n:
                break

        c = raw[i]

        # ── Group open: peek for a skip-control-word right after ──
        if c == "{":
            m = _CONTROL.match(raw, i + 1)
            if m and m.group(1).lower() in _SKIP_GROUPS:
                skipping_depth = 1
                i = m.end()
            else:
                i += 1
            continue

        if c == "}":
            i += 1
            continue

        # ── c == "\\" (backslash) ──
        # Special: \\*  = "ignore this whole group"
        if i + 1 < n and raw[i + 1] == "*":
            skipping_depth = 1
            i += 2
            continue

        m = _CONTROL.match(raw, i)
        if m:
            word = m.group(1).lower()
            if word in _SKIP_GROUPS:
                skipping_depth = 1
            elif word in ("par", "line", "row"):
                out.append("\n")
            elif word in ("tab", "cell"):
                out.append("\t")
            elif word == "emdash":
                out.append("\u2014")
            i = m.end()
            continue

        m = _HEX_ESC.match(raw, i)
        if m:
            try:
                ch = bytes([int(m.group(1), 16)]).decode("cp1252", "replace")
                out.append(" " if ch == "\xa0" else ch)
            except Exception:
                pass
            i = m.end()
            continue

        # Escaped \{  \}  \\ -> emit the literal character
        if i + 1 < n and raw[i + 1] in ("{", "}", "\\"):
            out.append(raw[i + 1])
        i += 2

    return "".join(out)

def extract_text(path: str, progress=None) -> str:
    """Text for an image or PDF. PDFs use their text layer when it parses."""
    progress = progress or (lambda _v: None)
    progress(5)
    lower = path.lower()
    if lower.endswith(".rtf"):
        with open(path, "rb") as f:
            raw = f.read()
        try:
            s = raw.decode("cp1252")
        except UnicodeDecodeError:
            s = raw.decode("latin-1", "replace")
        progress(95)
        return rtf_to_text(s)
    if lower.endswith(".pdf"):
        if not PDF_AVAILABLE:
            raise OCRUnavailable(
                "PDF input requires 'pymupdf'.\n  python -m pip install pymupdf")
        doc = fitz.open(path)
        try:
            n = len(doc)
            if n == 0:
                raise RuntimeError("PDF has no pages.")
            # 1) native text layer: exact, no OCR errors
            layer = "\n".join(f"--- page {i+1} of {n} (text layer) ---\n"
                              + doc.load_page(i).get_text() for i in range(n))
            from core import parse_all_systems
            single = parse_hap_text(layer)
            systems = parse_all_systems(layer)
            # One completeness rule for both paths: every field present and
            # no parser warnings. With system headers, EVERY system must pass.
            if systems:
                layer_complete = all(s.btu > 0 and s.cfm > 0 and not s.warnings
                                     for s in systems)
            else:
                layer_complete = single.btu > 0 and single.cfm > 0 and not single.warnings
            if layer_complete:
                progress(95)
                return layer
            # A partial native text layer is not proof that all required fields
            # were extracted. OCR pages and retain both sources for reconciliation.
            chunks = []
            for i in range(n):
                pix = doc.load_page(i).get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                try:
                    txt = ocr_image(img)
                except OCRUnavailable:
                    # Keep any useful native text if the optional OCR engine is absent.
                    if single.btu > 0 or systems:
                        progress(95)
                        return layer
                    raise
                chunks.append(f"--- page {i+1} of {n} (OCR) ---\n{txt}")
                progress(10 + int(80 * (i + 1) / n))
            return layer + "\n" + "\n".join(chunks)
        finally:
            doc.close()
    progress(10)
    return ocr_image(Image.open(path), progress)
