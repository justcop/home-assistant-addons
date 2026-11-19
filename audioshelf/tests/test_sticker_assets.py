"""Check the two physical paper label designs and responsive PNG exports."""
from pathlib import Path
from PIL import Image

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
STICKERS = ("loan", "spotify_attention")
WIDTHS = (96, 160, 256, 384)

def test_responsive_photographic_sticker_assets():
    for kind in STICKERS:
        master = STATIC / "stickers" / f"{kind}-master.png"
        assert master.is_file(), f"Preserve full-resolution {kind} original"
        with Image.open(master) as img:
            assert img.mode == "RGBA" and img.width >= 1000
            master_ratio = img.width / img.height
        for width in WIDTHS:
            file = STATIC / "stickers" / f"{kind}-{width}.png"
            assert file.is_file(), file
            with Image.open(file) as img:
                assert img.format == "PNG" and img.mode == "RGBA"
                assert img.width == width
                assert abs(img.width / img.height - master_ratio) < .04

def test_status_illustrations_are_proportional_and_two_only():
    app = (STATIC / "app.js").read_text()
    vinyl = (STATIC / "vinyl.js").read_text()
    css = (STATIC / "style.css").read_text()
    worker = (STATIC / "sw.js").read_text()
    assert "loanStickerArt(album)" in app
    assert "loanStickerArt(album," in app
    assert "paperSticker('spotify_attention'" in app
    assert "paperSticker('spotify_attention'" in vinyl
    assert "paperSticker('collection'" not in app + vinyl
    assert "paperSticker('on-loan'" not in app + vinyl
    assert "STICKER_WIDTHS=[96,160,256,384]" in app
    assert "width:53%" in css and "width:34%" in css
    assert "match-needed-label" in css and "pointer-events:auto" in css
    assert "flatMap(name=>[96,160,256,384]" in worker
