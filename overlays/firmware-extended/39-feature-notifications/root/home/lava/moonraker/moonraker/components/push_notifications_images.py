# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @noon92

# Spool pictures for the Spoolman notifications of push_notifications.
#
# One row per toolhead: its spool, drawn in the filament's colour and filled
# to how much is left (10% steps of a full spool; under 10% shows a few
# wraps), with an optional arrow to the spool the printer refills from.
# Colours: red = not enough, orange = may be insufficient / running low,
# green = enough. Uses Pillow only (no numpy).
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

RGB = Tuple[int, int, int]
RED: RGB = (176, 38, 38)
ORANGE: RGB = (196, 104, 0)
GREEN: RGB = (36, 116, 58)
INK: RGB = (32, 34, 38)
GREY: RGB = (95, 98, 105)
STATUS_COLOURS = {"red": RED, "orange": ORANGE, "green": GREEN, "ink": INK}

WIDTH = 760
ROW = 330
COLUMN = 300       # width of each spool's column
LEFT = 190         # centre of the toolhead's column
RIGHT = 570        # centre of the refill's column (or the note)
ARROW = 380        # centre of the arrow between them
SPOOL = 170        # spool height in px
SQUASH = 0.5       # side view at ~60 degrees
DEPTH = 0.32       # spool width between flanges, fraction of its height
R_HUB = 0.34       # hub radius, fraction of the flange radius
R_FULL = 0.90      # radius of a full spool's windings
SS = 2             # supersampling for smooth edges
UNKNOWN_FILL = 0.5 # Spoolman says empty but it's loaded: drawn half full

FONT_DIRS = ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu",
             "/usr/share/fonts/TTF", "/usr/share/fonts")
_fonts: Dict[Tuple[bool, int], Any] = {}
# The printer has no font files: Pillow's built-in font (one weight) is
# used, and bold is drawn with an outline
_fake_bold = False


def font(bold: bool, size: int) -> Any:
    key = (bold, size)
    if key in _fonts:
        return _fonts[key]
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    f = None
    for d in FONT_DIRS:
        try:
            f = ImageFont.truetype(os.path.join(d, name), size)
            break
        except OSError:
            continue
    if f is None:
        global _fake_bold
        _fake_bold = True
        try:
            f = ImageFont.load_default(size=size)   # Pillow >= 10.1
        except TypeError:
            f = ImageFont.load_default()
    _fonts[key] = f
    return f


def parse_hex(value: Any) -> Optional[RGB]:
    s = str(value or "").strip().lstrip("#")
    if len(s) >= 6:
        try:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        except ValueError:
            return None
    return None


def level(remaining: float, full: Optional[float]) -> float:
    """Drawn fill: 10% steps of a full spool; under 10% (not empty) shows
    a few wraps. Unknown full weight: drawn full."""
    if remaining <= 0:
        return 0.
    if not full or full <= 0:
        return 1.
    f = remaining / full
    return 0.06 if f < 0.1 else min(1., round(f * 10) / 10)


def _scale(c: Sequence[float], k: float) -> RGB:
    return tuple(max(0, min(255, int(v * k))) for v in c[:3])  # type: ignore


def _ellipse(d: ImageDraw.ImageDraw, cx: float, cy: float, rx: float,
             ry: float, **kw: Any) -> None:
    d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], **kw)


def spool(fill: float, colour: RGB) -> Image.Image:
    """An angled spool, front flange on the left, smoked clear plastic."""
    h = SPOOL * SS
    r = h / 2.
    rx = r * SQUASH
    depth = int(h * DEPTH)
    w = int(2 * rx) + depth + 4 * SS
    img = Image.new("RGBA", (w, h + 4 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cy = h / 2. + 2 * SS
    fx = rx + 2 * SS                 # front face centre
    lum = sum(colour) / 3.
    face = colour
    side = _scale(colour, 0.78)
    groove = _scale(colour, 0.9)
    if lum < 45:                     # lift black so the windings show
        face = tuple(min(255, v + 18) for v in colour)  # type: ignore
        side = tuple(min(255, v + 10) for v in colour)  # type: ignore
        groove = tuple(min(255, v + 34) for v in colour)  # type: ignore
    elif lum > 225:                  # white: keep shading visible
        side = _scale(colour, 0.84)
        groove = _scale(colour, 0.93)
    r_fill = math.sqrt(R_HUB ** 2 + fill * (R_FULL ** 2 - R_HUB ** 2)) \
        if fill > 0 else 0.
    # back flange
    _ellipse(d, fx + depth, cy, rx, r, fill=(85, 89, 96, 235))
    # barrel: hub, then windings, stacked from the back
    for i in range(depth, 0, -SS):
        _ellipse(d, fx + i, cy, rx * R_HUB, r * R_HUB, fill=(96, 100, 108))
        if r_fill:
            _ellipse(d, fx + i, cy, rx * r_fill, r * r_fill, fill=side)
    if r_fill:
        _ellipse(d, fx, cy, rx * r_fill, r * r_fill, fill=face)
        # winding grooves on the front face
        k = r_fill
        step = 0.035
        while k > R_HUB + step:
            _ellipse(d, fx, cy, rx * k, r * k, outline=groove, width=SS)
            k -= step
    # hub and centre hole
    _ellipse(d, fx, cy, rx * R_HUB, r * R_HUB, fill=(132, 136, 145))
    _ellipse(d, fx - rx * 0.04, cy - r * 0.04, rx * R_HUB * 0.86,
             r * R_HUB * 0.86, fill=(146, 150, 159))
    _ellipse(d, fx, cy, rx * 0.12, r * 0.12, fill=(52, 54, 60))
    # front flange: smoked clear plastic, opaque rim, soft glare
    fl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(fl)
    _ellipse(fd, fx, cy, rx, r, fill=(150, 158, 170, 72))
    glare = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glare)
    _ellipse(gd, fx - rx * 0.45, cy - r * 0.42, rx * 0.22, r * 0.3,
             fill=(255, 255, 255, 120))
    glare = glare.filter(ImageFilter.GaussianBlur(10 * SS))
    fl = Image.alpha_composite(fl, glare)
    fd = ImageDraw.Draw(fl)
    _ellipse(fd, fx, cy, rx, r, outline=(80, 84, 94, 245), width=4 * SS)
    img = Image.alpha_composite(img, fl)
    return img.resize((img.width // SS, img.height // SS), Image.LANCZOS)


def _text(d: ImageDraw.ImageDraw, xy: Tuple[float, float], text: str,
          bold: bool, size: int, fill: RGB, center: bool = False) -> None:
    f = font(bold, size)
    x, y = xy
    if bold and _fake_bold:
        # Outlined letters need a little more space between them
        gap = max(1., size * 0.07)
        widths = [d.textlength(c, font=f) for c in text]
        if center:
            x -= (sum(widths) + gap * (len(text) - 1)) / 2
        for c, w in zip(text, widths):
            d.text((x, y), c, font=f, fill=fill, stroke_width=1,
                   stroke_fill=fill)
            x += w + gap
        return
    if center:
        x -= d.textlength(text, font=f) / 2
    d.text((x, y), text, font=f, fill=fill)


def _fit(d: ImageDraw.ImageDraw, text: str, f: Any, width: float) -> str:
    if d.textlength(text, font=f) <= width:
        return text
    while text and d.textlength(text + "…", font=f) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _put(bg: Image.Image, cx: float, y: int, sp: Image.Image) -> Image.Image:
    x = int(cx - sp.width / 2)
    sh = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.ellipse([x + 8, y + SPOOL - 14, x + sp.width + 8, y + SPOOL + 16],
               fill=(0, 0, 0, 80))
    bg = Image.alpha_composite(bg, sh.filter(ImageFilter.GaussianBlur(9)))
    bg.alpha_composite(sp, (x, y))
    return bg


def _background(height: int) -> Image.Image:
    bg = Image.new("RGBA", (WIDTH, height))
    d = ImageDraw.Draw(bg)
    for y in range(height):
        g = int(238 - 18 * y / max(1, height - 1))
        d.line([(0, y), (WIDTH, y)], fill=(g, g + 1, g + 5, 255))
    return bg


def render(rows: List[Dict[str, Any]], path: str) -> str:
    """Draw the rows and save a JPEG at `path`. Each row:
      toolhead, word, status, needs (str or ""), remaining, full, colour,
      name, and optionally refill = {toolhead, remaining, full, colour,
      name, status} or note = [lines].
    Both spools are drawn as the same stack: toolhead above, amount left,
    what it needs (left spool only) and the filament's name below. `word`
    is not drawn: the colour and the amounts say it.
    """
    height = ROW * len(rows) + 12
    bg = _background(height)

    def stack(d: ImageDraw.ImageDraw, cx: float, y0: int, sp: Dict[str, Any],
              col: RGB, needs: str = "") -> None:
        _text(d, (cx, y0 + 2), sp["toolhead"], True, 24, col, center=True)
        y = y0 + SPOOL + 66
        _text(d, (cx, y), "Amount left unknown" if sp.get("unknown")
              else grams_text(sp["remaining"], sp.get("full")),
              True, 20, col, center=True)
        y += 27
        if needs:
            _text(d, (cx, y), needs.rstrip("."), False, 20, INK, center=True)
            y += 27
        _text(d, (cx, y), _fit(d, sp.get("name") or "", font(False, 16),
                               COLUMN - 20), False, 16, GREY, center=True)

    for i, row in enumerate(rows):
        y0 = 6 + i * ROW
        col = STATUS_COLOURS.get(row["status"], INK)
        refill = row.get("refill")
        for cx, sp in ((LEFT, row), (RIGHT, refill)):
            if sp:
                bg = _put(bg, cx, y0 + 40, spool(
                    UNKNOWN_FILL if sp.get("unknown")
                    else level(sp["remaining"], sp.get("full")),
                    sp.get("colour") or (200, 200, 200)))
        d = ImageDraw.Draw(bg)
        if i:
            d.line([40, y0 - 4, WIDTH - 40, y0 - 4], fill=(190, 193, 200),
                   width=2)
        stack(d, LEFT, y0, row, col, row.get("needs") or "")
        if refill:
            ay = y0 + 40 + SPOOL // 2
            x0, x1 = ARROW - 50, ARROW + 50
            d.line([x0, ay, x1 - 22, ay], fill=(45, 48, 54), width=8)
            d.polygon([(x1, ay), (x1 - 28, ay - 21), (x1 - 28, ay + 21)],
                      fill=(45, 48, 54))
            _text(d, (ARROW, ay - 52), "refill", False, 18, GREY,
                  center=True)
            stack(d, RIGHT, y0, refill,
                  STATUS_COLOURS.get(refill.get("status"), INK))
        else:
            lines = row.get("note") or []
            y = y0 + 40 + SPOOL // 2 - 15 * len(lines)
            for k, line in enumerate(lines):
                _text(d, (RIGHT, y + k * 30), line, False, 20, GREY,
                      center=True)
    bg.convert("RGB").save(path, quality=88)
    return path


def grams_text(remaining: float, full: Optional[float]) -> str:
    if full and full > 0:
        return f"{remaining:.0f} of {full:.0f} g left"
    return f"{remaining:.0f} g left"
