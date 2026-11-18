"""Standalone image uploads + Tesseract OCR (6 Nov 2026).

- .jpg / .jpeg / .png are accepted at upload and parsed as a one-page document whose whole
  picture goes to the vision model (pre-filter bypassed), with Tesseract text as text blocks.
- Docling page OCR is Tesseract, never the automatic choice (which picked RapidOCR).
Nothing here touches the database, storage or the model endpoints — all faked.
"""
import io
from unittest.mock import patch

from PIL import Image

from app.models.document import ExtractedImage


def _png(w=3000, h=1500, color=(240, 240, 240)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def test_image_types_are_accepted_at_upload():
    from app.api.routes import ingestion
    assert ingestion._EXT_TO_MIME[".jpg"] == "image/jpeg"
    assert ingestion._EXT_TO_MIME[".jpeg"] == "image/jpeg"
    assert ingestion._EXT_TO_MIME[".png"] == "image/png"
    assert {"image/jpeg", "image/png"} <= ingestion.ALLOWED_MIME_TYPES
    assert ingestion._ext_for("scan.jpeg", "image/jpeg") == ".jpg"


def test_image_is_a_one_page_document_read_by_the_vision_model(tmp_path):
    from app.services import document_parser
    p = tmp_path / "whiteboard.png"
    p.write_bytes(_png())
    with patch("app.services.ocr_service.ocr_image", return_value="Q3 targets\n\nShip by Friday"):
        doc = document_parser.parse_document(str(p), "doc-1")
    assert doc.page_count == 1 and doc.has_images and len(doc.images) == 1
    img = doc.images[0]
    assert img.whole_file is True and max(img.width, img.height) <= 2048      # scaled down
    assert [b.text for b in doc.text_blocks] == ["Q3 targets", "Ship by Friday"]
    assert doc.metadata.get("source_kind") == "image"


def test_jpeg_without_text_still_parses(tmp_path):
    from app.services import document_parser
    p = tmp_path / "photo.jpg"
    Image.new("RGB", (640, 480), (10, 120, 200)).save(p, format="JPEG")
    with patch("app.services.ocr_service.ocr_image", return_value=""):
        doc = document_parser.parse_document(str(p), "doc-2")
    assert doc.text_blocks == [] and len(doc.images) == 1 and doc.images[0].whole_file


def test_whole_file_picture_skips_the_prefilter():
    from app.services import ingestion_orchestrator as orch
    img = ExtractedImage(image_index=0, page_number=1, bbox=None, png_bytes=_png(200, 100), width=200, height=100,
                         whole_file=True)
    with patch("app.services.image_prefilter.ImagePrefilter.evaluate") as evaluate, \
         patch("app.services.ocr_service.ocr_image", return_value="text"), \
         patch.object(orch, "upload_file", lambda *a, **k: None):
        pending = orch._prepare_image_record(img, "doc-3", "bucket")
    evaluate.assert_not_called()
    assert pending is not None and pending.get("needs_vlm") is True


def test_whole_file_flag_survives_staging():
    from app.services import parse_staging_service as st
    img = ExtractedImage(image_index=0, page_number=1, bbox=None, png_bytes=b"x", whole_file=True)
    d = st._dataclass_to_dict_no_bytes(img)
    assert st._dict_to_extracted_image(d).whole_file is True


def test_docling_page_ocr_is_tesseract():
    from app.services import document_parser
    opts = document_parser._ocr_options()
    assert type(opts).__name__ == "TesseractCliOcrOptions" and opts.lang == ["eng"]


def test_ocr_without_tesseract_is_empty_not_an_error(monkeypatch):
    from app.services import ocr_service
    monkeypatch.setattr(ocr_service, "tesseract_cmd", lambda: None)
    assert ocr_service.ocr_image(_png(50, 50)) == ""


def test_table_cells_lose_ocr_noise_around_numbers_only():
    from app.services.document_parser import _clean_numeric_cell as c
    assert c("94\ufffd") == "94" and c("-2.8% ~") == "-2.8%" and c("6 :") == "6"
    assert c("\ufffd +1.1%") == "+1.1%" and c("-201 \ufffd") == "-201" and c("423.") == "423"
    for kept in ("Revenue", "One-off", "32% verif ieq", "ee5a2", "12,161", "(1,250)", "2026-04-14", ":", "TO1"):
        assert c(kept) == kept
