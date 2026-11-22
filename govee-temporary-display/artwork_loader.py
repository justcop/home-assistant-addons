"""Read and cache native-resolution Govee artwork from regular PNG files.

The images are stored in the GitHub repository under artwork/ and copied
into the installed app. This reader is separate from the temporary UDP
transport and the gameplay session scheduler.
"""
from functools import lru_cache
from pathlib import Path

from PIL import Image

WIDTH = 52
HEIGHT = 32
_ARTWORK_DIR = Path(__file__).resolve().parent / "artwork"


@lru_cache(maxsize=32)
def load_rgb_image(filename: str) -> bytes:
    """Load an approved registry filename as exactly 4992 RGB bytes."""
    if (not isinstance(filename, str) or not filename.endswith(".png")
            or Path(filename).name != filename):
        raise ValueError("artwork filename must be a PNG basename")
    path = _ARTWORK_DIR / filename
    with Image.open(path) as image:
        if image.size != (WIDTH, HEIGHT):
            raise ValueError(
                f"{filename} is {image.width}x{image.height}; expected {WIDTH}x{HEIGHT}"
            )
        raw = image.convert("RGB").tobytes()
    if len(raw) != WIDTH * HEIGHT * 3:
        raise ValueError("artwork image is not a valid 52x32 RGB frame")
    return raw
