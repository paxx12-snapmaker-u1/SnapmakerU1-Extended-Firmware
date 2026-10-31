# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @noon92

# HTML emails for push_notifications (and the Firmware Config test): a card
# with a colour bar for the kind of notification, the title, the text, the
# image inline, and the links as buttons. Inline styles and tables only, so
# it looks the same in Gmail, Outlook and phone mail apps. Apprise adds a
# plain-text version for mail apps that don't show HTML.
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import datetime
import html
from typing import List, Optional, Sequence, Tuple

# Bar colour per Apprise notification type (as in the Discord embeds)
COLOURS = {"info": "#3D7BD9", "success": "#24743A", "warning": "#C46800",
           "failure": "#B02626"}
FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,"
        "Arial,sans-serif")
IMAGE_MARK = ("<!--image-->", "<!--/image-->")
NO_IMAGE = ('<p style="margin:0;color:#8a8f98;font-size:13px">'
            '(image could not be attached)</p>')


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _paragraphs(body: str) -> str:
    """Blank lines separate paragraphs; the first line of each is bold
    (the file name, or the toolhead in filament notifications)."""
    out = []
    for block in (b for b in body.strip().split("\n\n") if b.strip()):
        lines = [_esc(x) for x in block.strip().split("\n")]
        if len(lines) > 1:
            lines[0] = f'<b style="color:#202226">{lines[0]}</b>'
        out.append('<p style="margin:0 0 12px 0">' + "<br>".join(lines)
                   + "</p>")
    return "".join(out)


def _button(title: str, url: str, primary: bool) -> str:
    bg, fg = ("#202226", "#ffffff") if primary else ("#eceef1", "#202226")
    return (f'<a href="{_esc(url)}" style="display:inline-block;'
            f'background:{bg};color:{fg};text-decoration:none;'
            f'font-weight:600;font-size:14px;padding:9px 16px;'
            f'border-radius:8px;margin:0 8px 8px 0">{_esc(title)}</a>')


def render(title: str, body: str, links: Sequence[Tuple[str, str]],
           kind: str = "info", image: Optional[str] = None,
           when: Optional[datetime.datetime] = None,
           sender: str = "") -> str:
    """The email's HTML. `image` is the attachment's file name (shown
    inline through Apprise's cid: support), `when` the time shown at the
    top and bottom (it also keeps Gmail from hiding repeated emails in a
    thread)."""
    when = when or datetime.datetime.now()
    colour = COLOURS.get(str(kind), COLOURS["info"])
    img = ""
    if image:
        img = (IMAGE_MARK[0] +
               f'<tr><td style="padding:4px 24px 8px 24px">'
               f'<img src="cid:{_esc(image)}" alt="" width="552" '
               f'style="display:block;width:100%;max-width:552px;height:auto;'
               f'border-radius:8px;border:1px solid #e3e5e8"></td></tr>'
               + IMAGE_MARK[1])
    buttons = " ".join(_button(t, u, n == 0) for n, (t, u) in enumerate(links))
    if buttons:
        buttons = (f'<tr><td style="padding:12px 24px 8px 24px">{buttons}'
                   '</td></tr>')
    footer = f"Sent {when:%Y-%m-%d %H:%M:%S}"
    if sender:
        footer += f" by {_esc(sender)}"
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width">'
        '<meta name="color-scheme" content="light"></head>'
        f'<body style="margin:0;padding:0;background:#f2f3f5">'
        f'<table role="presentation" width="100%" cellpadding="0" '
        f'cellspacing="0" style="background:#f2f3f5"><tr><td align="center" '
        f'style="padding:24px 12px">'
        f'<table role="presentation" width="100%" cellpadding="0" '
        f'cellspacing="0" style="max-width:600px;background:#ffffff;'
        f'border:1px solid #e3e5e8;border-radius:12px;font-family:{FONT};'
        f'border-collapse:separate;overflow:hidden">'
        f'<tr><td style="height:6px;line-height:6px;font-size:0;'
        f'background:{colour};border-radius:12px 12px 0 0">&nbsp;</td></tr>'
        f'<tr><td style="padding:18px 24px 0 24px;font-size:12px;'
        f'color:#8a8f98;letter-spacing:.02em">'
        f'{when:%a %d %b %Y · %H:%M}</td></tr>'
        f'<tr><td style="padding:4px 24px 12px 24px;font-size:21px;'
        f'line-height:1.3;font-weight:700;color:#202226">{_esc(title)}'
        f'</td></tr>'
        f'<tr><td style="padding:0 24px 4px 24px;font-size:15px;'
        f'line-height:1.5;color:#3a3d42">{_paragraphs(body)}</td></tr>'
        f'{img}{buttons}'
        f'<tr><td style="padding:14px 24px 18px 24px;font-size:12px;'
        f'color:#8a8f98;border-top:1px solid #eceef1">{footer}</td></tr>'
        '</table></td></tr></table></body></html>')


def without_image(page: str) -> str:
    """The page with the image replaced by a note (the last retry is sent
    without the image)."""
    start, end = IMAGE_MARK
    a, b = page.find(start), page.find(end)
    if a < 0 or b < 0:
        return page
    note = f'<tr><td style="padding:4px 24px 8px 24px">{NO_IMAGE}</td></tr>'
    return page[:a] + note + page[b + len(end):]


def is_page(text: str) -> bool:
    return text.startswith("<!doctype html>")


__all__: List[str] = ["render", "without_image", "is_page", "COLOURS"]
