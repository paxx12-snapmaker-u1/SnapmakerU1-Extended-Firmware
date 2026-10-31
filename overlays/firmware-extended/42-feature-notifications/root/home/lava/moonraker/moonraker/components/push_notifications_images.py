# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @noon92

# Spool pictures for the Spoolman notifications of push_notifications.
#
# One row per toolhead: its spool, drawn in the filament's colour (or
# colours: wedges and stripes for a multi-colour filament) and filled
# to how much is left (10% steps of a full spool; under 10% shows a few
# wraps), with arrows to the spools the printer refills from, in the order
# it would use them. The toolhead's own spool has the left column to
# itself; a chain longer than two backups wraps to a second line, joined
# by an elbow arrow (down, left, down).
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

WIDTH = 900
ROW = 330          # one line of spools
LINE_GAP = 44      # between the lines of a wrapped chain (the elbow arrow)
NOTE_H = 30        # the "order may differ" note under a chain
COLUMN = 300       # width of each spool's column
COLUMNS = (150, 450, 750)   # centres: toolhead, then its refill spools
NOTE_X = 600       # centre of the no-refill note (the two right columns)
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


def _shades(colour: RGB) -> Tuple[RGB, RGB, RGB]:
    """(face, side, groove) shades of a winding colour."""
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
    return face, side, groove


def spool(fill: float, colour: Any) -> Image.Image:
    """An angled spool, front flange on the left, smoked clear plastic.
    `colour` is one RGB, or a list of them for a multi-colour filament:
    the face is split into wedges and the barrel into stripes, one per
    colour, in the order given (coextruded or sequential alike)."""
    colours: List[RGB] = [tuple(c) for c in colour] \
        if colour and isinstance(colour[0], (tuple, list)) else [tuple(colour)]
    colours = colours[:8] or [(200, 200, 200)]
    h = SPOOL * SS
    r = h / 2.
    rx = r * SQUASH
    depth = int(h * DEPTH)
    w = int(2 * rx) + depth + 4 * SS
    img = Image.new("RGBA", (w, h + 4 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cy = h / 2. + 2 * SS
    fx = rx + 2 * SS                 # front face centre
    r_fill = math.sqrt(R_HUB ** 2 + fill * (R_FULL ** 2 - R_HUB ** 2)) \
        if fill > 0 else 0.
    n = len(colours)
    shades = [_shades(c) for c in colours]
    # back flange
    _ellipse(d, fx + depth, cy, rx, r, fill=(85, 89, 96, 235))
    # barrel: hub, then windings, stacked from the back; a multi-colour
    # filament's barrel is striped along its depth
    for i in range(depth, 0, -SS):
        _ellipse(d, fx + i, cy, rx * R_HUB, r * R_HUB, fill=(96, 100, 108))
        if r_fill:
            k = min(n - 1, int((depth - i) / depth * n))
            _ellipse(d, fx + i, cy, rx * r_fill, r * r_fill,
                     fill=shades[k][1])
    if r_fill:
        box = [fx - rx * r_fill, cy - r * r_fill, fx + rx * r_fill,
               cy + r * r_fill]
        if n == 1:
            d.ellipse(box, fill=shades[0][0])
        else:
            for k in range(n):
                d.pieslice(box, -90 + 360. * k / n, -90 + 360. * (k + 1) / n,
                           fill=shades[k][0])
        # winding grooves on the front face, in each wedge's own shade
        kk = r_fill
        step = 0.035
        while kk > R_HUB + step:
            gbox = [fx - rx * kk, cy - r * kk, fx + rx * kk, cy + r * kk]
            if n == 1:
                d.ellipse(gbox, outline=shades[0][2], width=SS)
            else:
                for k in range(n):
                    d.arc(gbox, -90 + 360. * k / n, -90 + 360. * (k + 1) / n,
                          fill=shades[k][2], width=SS)
            kk -= step
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


def _layout(row: Dict[str, Any]) -> List[Tuple[int, int]]:
    """(line, column) of each refill spool: the first line holds up to
    two after the toolhead's own; later lines up to two, never in the
    toolhead's (left) column."""
    return [(0, 1 + k) if k < 2 else (1 + (k - 2) // 2, 1 + (k - 2) % 2)
            for k in range(len(row.get("refills") or []))]


def _row_height(row: Dict[str, Any]) -> int:
    places = _layout(row)
    lines = 1 + max((ln for ln, _c in places), default=0)
    note = NOTE_H if len(places) >= 2 else 0
    return ROW * lines + LINE_GAP * (lines - 1) + note


def _warning(d: ImageDraw.ImageDraw, x: float, y: float, size: int) -> None:
    """A warning sign (as the emoji): yellow triangle, dark "!"."""
    top = y + 1
    d.polygon([(x + size / 2, top), (x + size, top + size - 1),
               (x, top + size - 1)], fill=(242, 184, 0),
              outline=(150, 110, 0))
    cx = x + size / 2
    d.line([cx, top + size * 0.32, cx, top + size * 0.66], fill=INK, width=2)
    d.ellipse([cx - 1.2, top + size * 0.75, cx + 1.2, top + size * 0.88],
              fill=INK)


def _arrow_right(d: ImageDraw.ImageDraw, x0: float, x1: float,
                 y: float) -> None:
    d.line([x0, y, x1 - 22, y], fill=(45, 48, 54), width=8)
    d.polygon([(x1, y), (x1 - 28, y - 21), (x1 - 28, y + 21)],
              fill=(45, 48, 54))


def _arrow_elbow(d: ImageDraw.ImageDraw, x0: float, y0: float, x1: float,
                 y1: float) -> None:
    """Down from (x0, y0), left to x1, down to (x1, y1)."""
    mid = y0 + (y1 - y0) * 0.45
    d.line([(x0, y0), (x0, mid), (x1, mid), (x1, y1 - 18)],
           fill=(45, 48, 54), width=6, joint="curve")
    d.polygon([(x1, y1), (x1 - 15, y1 - 22), (x1 + 15, y1 - 22)],
              fill=(45, 48, 54))


def render(rows: List[Dict[str, Any]], path: str) -> str:
    """Draw the rows and save a JPEG at `path`. Each row:
      toolhead, word, status, needs (str or ""), remaining (None: not
      known, drawn full with no amount line), full, colour,
      name, and optionally refills = [{toolhead, remaining, full, colour,
      name, status, match ("Exact match" / "Inexact match")}, ...] (in the order the printer uses them) or
      note = [lines].
    Every spool is drawn as the same stack: toolhead above, amount left,
    what it needs (the toolhead's own spool only) and the filament's name
    below. `word` is not drawn: the colour and the amounts say it.
    """
    heights = [_row_height(r) for r in rows]
    height = sum(heights) + 12
    bg = _background(height)

    def stack(d: ImageDraw.ImageDraw, cx: float, y0: int, sp: Dict[str, Any],
              col: RGB, needs: str = "") -> None:
        _text(d, (cx, y0 + 2), sp["toolhead"], True, 24, col, center=True)
        y = y0 + SPOOL + 66
        if sp.get("remaining") is not None:
            # No Spoolman: no amount, the spool is drawn full
            _text(d, (cx, y), "Amount left unknown" if sp.get("unknown")
                  else grams_text(sp["remaining"], sp.get("full")),
                  True, 20, col, center=True)
            y += 27
        if needs:
            _text(d, (cx, y), needs.rstrip("."), False, 20, INK, center=True)
            y += 27
        _text(d, (cx, y), _fit(d, sp.get("name") or "", font(False, 16),
                               COLUMN - 20), False, 16, GREY, center=True)
        if sp.get("match"):
            # A refill spool: the same colour as the toolhead's, or not
            # (with a warning sign: drawn, the printer has no emoji font)
            m = sp["match"]
            if m.startswith("Inexact"):
                w = d.textlength(m, font=font(False, 16))
                size = 16
                gap = 6
                x = cx - (w + size + gap) / 2
                _warning(d, x, y + 22, size)
                _text(d, (x + size + gap, y + 22), m, False, 16, GREY)
            else:
                _text(d, (cx, y + 22), m, False, 16, GREY, center=True)

    def draw(sp: Dict[str, Any], cx: float, y: int) -> None:
        nonlocal bg
        bg = _put(bg, cx, y + 40, spool(
            UNKNOWN_FILL if sp.get("unknown")
            else 1. if sp.get("remaining") is None
            else level(sp["remaining"], sp.get("full")),
            sp.get("colour") or (200, 200, 200)))

    y0 = 6
    for i, row in enumerate(rows):
        col = STATUS_COLOURS.get(row["status"], INK)
        refills = row.get("refills") or []
        places = _layout(row)
        line_y = lambda ln: y0 + ln * (ROW + LINE_GAP)
        draw(row, COLUMNS[0], y0)
        for sp, (ln, c) in zip(refills, places):
            draw(sp, COLUMNS[c], line_y(ln))
        d = ImageDraw.Draw(bg)
        if i:
            d.line([40, y0 - 4, WIDTH - 40, y0 - 4], fill=(190, 193, 200),
                   width=2)
        stack(d, COLUMNS[0], y0, row, col, row.get("needs") or "")
        prev = (0, 0)
        for k, (sp, (ln, c)) in enumerate(zip(refills, places)):
            if ln == prev[0]:
                ay = line_y(ln) + 40 + SPOOL // 2
                x0 = COLUMNS[prev[1]] + COLUMN / 2 - 100
                x1 = COLUMNS[c] - COLUMN / 2 + 100
                _arrow_right(d, x0, x1, ay)
                _text(d, ((x0 + x1) / 2, ay - 52), "refill", False, 18,
                      GREY, center=True)
            else:
                # From under the last spool of the line above, down to the
                # first of this one
                ey0, ey1 = line_y(prev[0]) + ROW - 18, line_y(ln) - 2
                _arrow_elbow(d, COLUMNS[prev[1]], ey0, COLUMNS[c], ey1)
                _text(d, ((COLUMNS[prev[1]] + COLUMNS[c]) / 2,
                          ey0 + (ey1 - ey0) * 0.45 - 25), "refill", False,
                      18, GREY, center=True)
            stack(d, COLUMNS[c], line_y(ln), sp,
                  STATUS_COLOURS.get(sp.get("status"), INK))
            prev = (ln, c)
        if not refills:
            lines = row.get("note") or []
            y = y0 + 40 + SPOOL // 2 - 15 * len(lines)
            for k, line in enumerate(lines):
                _text(d, (NOTE_X, y + k * 30), line, False, 20, GREY,
                      center=True)
        elif len(refills) >= 2:
            _text(d, (WIDTH / 2, y0 + heights[i] - NOTE_H + 2),
                  "Backup spool order may differ from what's shown",
                  False, 16, GREY, center=True)
        y0 += heights[i]
    bg.convert("RGB").save(path, quality=88)
    return path


def grams_text(remaining: float, full: Optional[float]) -> str:
    if full and full > 0:
        return f"{remaining:.0f} of {full:.0f} g left"
    return f"{remaining:.0f} g left"
