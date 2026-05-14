from modules.google_patents_scraper import (
    _GOOGLE_PATENTS_IMAGE_JS,
    _extract_google_patents_image_label,
    _google_patents_image_kind,
)


def test_extract_google_patents_image_label_supports_japanese_figures():
    rec = {"context": "【図１】本発明の実施形態を示す断面図"}

    assert _extract_google_patents_image_label(rec) == "図1"
    assert _google_patents_image_kind("図1") == "figure"


def test_extract_google_patents_image_label_supports_chemical_and_english_labels():
    assert _extract_google_patents_image_label({"label": "【化２】"}) == "化2"
    assert _google_patents_image_kind("化2") == "chemical"
    assert _extract_google_patents_image_label({"alt": "FIG. 3 is a schematic view"}) == "Fig. 3"


def test_google_patents_image_js_collects_drawings_and_patentimages():
    assert "section[itemprop=\"drawings\"] img" in _GOOGLE_PATENTS_IMAGE_JS
    assert "patentimages.storage.googleapis.com" in _GOOGLE_PATENTS_IMAGE_JS
    assert "meta[itemprop=\"full\"]" in _GOOGLE_PATENTS_IMAGE_JS
