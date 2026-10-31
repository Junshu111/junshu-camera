"""One continuous 3:5 context, shared by the preview and full-resolution JPEG.

Boxes are floating point coordinates in the oriented source. Subpixel resampling
retains the maximal context without an extra alignment/safety crop.
"""
from dataclasses import dataclass
import math

from PIL import Image, ImageOps

from .imaging import beautify


_RECTS = {'9:16': (15, 0, 450, 800), '1:1': (0, 160, 480, 480),
          '3:4': (0, 80, 480, 640), '16:9': (0, 265, 480, 270)}


@dataclass(frozen=True)
class FramedImage:
    image: Image.Image
    box: tuple
    source_size: tuple


def capture_rect(ratio, fullscreen=True):
    """R4 logical rectangle; OFF affects display masks, never photo geometry."""
    try:
        return _RECTS[ratio]
    except KeyError:
        raise ValueError('Unsupported photo ratio') from None


def context_box(size, settings):
    """Maximal centered 3:5 context / zoom, in already-oriented pixel space."""
    width, height = size
    if min(width, height) < 2:
        raise ValueError('Camera frame is too small')
    zoom = settings.zoom
    if not math.isfinite(zoom) or zoom < 1:
        raise ValueError('Zoom must be at least one')
    scale = min(width / 3, height / 5) / zoom
    cw, ch = 3 * scale, 5 * scale
    left, top = (width - cw) / 2, (height - ch) / 2
    return left, top, left + cw, top + ch


def _oriented(image, settings):
    image = image.convert('RGB')
    if settings.rotation:
        image = image.rotate(-settings.rotation, expand=True)
    if settings.mirror:
        image = ImageOps.mirror(image)
    return beautify(image) if settings.beauty else image


def compose_frame(image, settings):
    oriented = _oriented(image, settings)
    left, top, right, bottom = context_box(oriented.size, settings)
    x, y, w, h = capture_rect(settings.ratio, settings.fullscreen)
    sx, sy = (right - left) / 480, (bottom - top) / 800
    box = (left + x * sx, top + y * sy, left + (x+w) * sx, top + (y+h) * sy)
    rw, rh = map(int, settings.ratio.split(':'))
    # Exact rational JPEG dimensions, with no upscaling and no further FOV loss.
    units = math.floor(min((box[2]-box[0])/rw, (box[3]-box[1])/rh) + 1e-8)
    if units < 1:
        raise ValueError('Camera frame too small for requested photo ratio')
    result = oriented.resize((rw*units, rh*units), Image.Resampling.LANCZOS, box=box)
    return FramedImage(result, box, oriented.size)


def preview_context(image, settings, size=(480, 800)):
    oriented = _oriented(image, settings)
    return oriented.resize(size, Image.Resampling.BILINEAR,
                           box=context_box(oriented.size, settings))
