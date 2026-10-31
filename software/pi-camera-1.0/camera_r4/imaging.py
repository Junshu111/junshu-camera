"""Pillow-only mild skin-colour smoothing; no reshape, learned model or HDR."""
from PIL import Image, ImageChops, ImageFilter


def beautify(image):
    image = image.convert('RGB')
    # Broad conventional skin-chroma gate. This is not face detection and can
    # also include skin-coloured objects; texture blend is deliberately mild.
    _, cb, cr = image.convert('YCbCr').split()
    cb_mask = cb.point([255 if 77 <= value <= 127 else 0 for value in range(256)])
    cr_mask = cr.point([255 if 133 <= value <= 173 else 0 for value in range(256)])
    mask = ImageChops.multiply(cb_mask, cr_mask)
    # Do not spread the mask onto non-skin neighbours.
    mask = mask.point(lambda value: round(value * .3))
    radius = max(1.2, min(4., min(image.size) * .006))
    return Image.composite(image.filter(ImageFilter.GaussianBlur(radius)), image, mask)


def luminance_histogram(image, bins=64):
    if type(bins) is not int or not 1 <= bins <= 256:
        raise ValueError('Histogram bins must be between 1 and 256')
    histogram = image.convert('L').histogram()
    result = [0] * bins
    for value, count in enumerate(histogram):
        result[min(bins-1, value*bins//256)] += count
    return result
