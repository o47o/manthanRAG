"""
Raw OCR engine wrapper: runs Tesseract on a single image and returns the detected text
UNALTERED. Used for pictures inside documents (as supporting text for the Gemma 4 vision
model) and for standalone image uploads.

6 Nov 2026: Tesseract replaces EasyOCR here (EasyOCR was never installed, so this returned
'' every time) and RapidOCR in the Docling page pipeline (Chinese-origin, user rule).
Tesseract is a separate program, called through its command line — no Python binding.
"""
import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)

_WINDOWS_DEFAULT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
_cmd_cache = None
_warned = False


def tesseract_cmd() -> str | None:
    """Path of the tesseract program, or None when it isn't installed."""
    global _cmd_cache
    if _cmd_cache is not None:
        return _cmd_cache or None
    from app.config import settings
    cand = [settings.TESSERACT_CMD] if settings.TESSERACT_CMD else []
    cand += [shutil.which("tesseract") or "", _WINDOWS_DEFAULT]
    _cmd_cache = next((c for c in cand if c and (os.path.isfile(c) or shutil.which(c))), "")
    return _cmd_cache or None


def ocr_image(png_bytes: bytes) -> str:
    """Run Tesseract on one image (PNG/JPEG bytes). Returns the text, or '' on any failure
    (non-fatal — the vision model still reads the picture)."""
    global _warned
    cmd = tesseract_cmd()
    if not cmd:
        if not _warned:
            logger.error("Tesseract OCR is not installed — picture text comes from the vision model only. "
                         "Install it (Windows: winget install UB-Mannheim.TesseractOCR; Docker: tesseract-ocr).")
            _warned = True
        return ""
    from app.config import settings
    try:
        r = subprocess.run([cmd, "stdin", "stdout", "-l", settings.TESSERACT_LANG or "eng", "--psm", "3"],
                           input=png_bytes, capture_output=True, timeout=120)
        if r.returncode != 0:
            logger.warning("tesseract failed (%s): %s", r.returncode, r.stderr[:300].decode("utf-8", "replace"))
            return ""
        return r.stdout.decode("utf-8", "replace").strip()
    except Exception as exc:
        logger.warning("ocr_image failed (%s: %s)", type(exc).__name__, exc)
        return ""
