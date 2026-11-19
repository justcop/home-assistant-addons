"""Keep the illustrated sleeve labels portable and readable across interfaces."""
from pathlib import Path
from xml.etree import ElementTree as ET

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
STICKERS = ("loan", "loan-worn", "collection", "attention", "on-shelf", "on-loan")
SVG = "{http://www.w3.org/2000/svg}"


def test_paper_stickers_are_self_contained_svg():
    for name in STICKERS:
        path = STATIC / "stickers" / f"{name}.svg"
        root = ET.parse(path).getroot()
        assert root.tag == SVG + "svg"
        assert root.attrib.get("viewBox")
        assert root.findall(".//" + SVG + "text"), name
        assert root.findall(".//" + SVG + "path"), name
        assert root.findall(".//" + SVG + "filter"), name
        data = path.read_text()
        assert "<script" not in data and "http://" not in data.replace('xmlns="http://www.w3.org/2000/svg"', '')


def test_sticker_states_use_assets_in_both_interfaces():
    app = (STATIC / "app.js").read_text()
    vinyl = (STATIC / "vinyl.js").read_text()
    style = (STATIC / "style.css").read_text()
    worker = (STATIC / "sw.js").read_text()

    assert "loanStickerArt(album)" in app
    assert "loanStickerArt(album," in app
    assert "paperSticker('collection'" in app and "paperSticker('collection'" in vinyl
    assert "paperSticker('attention'" in app and "paperSticker('attention'" in vinyl
    assert "paperSticker('on-shelf'" in vinyl and "paperSticker('on-loan'" in vinyl
    assert "match-needed-label" in style and "pointer-events:auto" in style
    assert "stickers/'+name+'.svg'" in worker
    assert 'album-sleeve-sticker' not in vinyl
