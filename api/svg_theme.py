from __future__ import annotations

import re
from typing import Dict, Tuple

from flask import Response


SVG_CANVAS_BG = "#020607"

_SVG_OPEN_RE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)
_SVG_STYLE_RE = re.compile(r"(<svg\b[^>]*\bstyle=['\"])([^'\"]*)(['\"])", re.IGNORECASE)
_SVG_HAS_STYLE_RE = re.compile(r"<svg\b[^>]*\bstyle=['\"]", re.IGNORECASE)
_FULL_CANVAS_RECT_RE = re.compile(
    r"<rect\b(?=[^>]*\bwidth=['\"](?:100%|[0-9.]+(?:px)?)['\"])(?=[^>]*\bheight=['\"](?:100%|[0-9.]+(?:px)?)['\"])[^>]*>",
    re.IGNORECASE,
)
_WHITE_FILL_RE = re.compile(r"(fill\s*:\s*|fill=['\"])\s*(#fff(?:fff)?|white|rgb\(255\s*,\s*255\s*,\s*255\)|rgba\(255\s*,\s*255\s*,\s*255\s*,\s*1(?:\.0+)?\))", re.IGNORECASE)
_TRANSPARENT_FILL_RE = re.compile(r"(fill\s*:\s*|fill=['\"])\s*(none|transparent|#00000000|#ffffff00|rgba\(0\s*,\s*0\s*,\s*0\s*,\s*0(?:\.0+)?\)|rgba\(255\s*,\s*255\s*,\s*255\s*,\s*0(?:\.0+)?\))", re.IGNORECASE)


def _set_svg_background_style(svg_text: str, background: str) -> Tuple[str, bool]:
    changed = False

    def replace_style(match: re.Match[str]) -> str:
        nonlocal changed
        style_value = match.group(2)
        new_style = re.sub(
            r"background(?:-color)?\s*:\s*[^;]+;?",
            f"background:{background};",
            style_value,
            flags=re.IGNORECASE,
        )
        if new_style == style_value:
            new_style = f"{style_value.rstrip(';')};background:{background};" if style_value.strip() else f"background:{background};"
        changed = changed or (new_style != style_value)
        return f"{match.group(1)}{new_style}{match.group(3)}"

    themed, count = _SVG_STYLE_RE.subn(replace_style, svg_text, count=1)
    if count:
        return themed, changed

    if not _SVG_OPEN_RE.search(svg_text):
        return svg_text, False

    themed = _SVG_OPEN_RE.sub(lambda m: m.group(0)[:-1] + f" style='background:{background};'>", svg_text, count=1)
    return themed, True


def _darken_background_rect(svg_text: str, background: str) -> Tuple[str, bool]:
    changed = False
    matched_canvas_rect = False

    def replace_rect(match: re.Match[str]) -> str:
        nonlocal changed, matched_canvas_rect
        rect = match.group(0)
        matched_canvas_rect = True

        if _WHITE_FILL_RE.search(rect):
            changed = True
            return _WHITE_FILL_RE.sub(lambda m: f"{m.group(1)}{background}", rect, count=1)

        if _TRANSPARENT_FILL_RE.search(rect):
            changed = True
            return _TRANSPARENT_FILL_RE.sub(lambda m: f"{m.group(1)}{background}", rect, count=1)

        if "fill=" not in rect.lower() and "fill:" not in rect.lower():
            changed = True
            return rect[:-1] + f" fill='{background}'>"

        return rect

    themed = _FULL_CANVAS_RECT_RE.sub(replace_rect, svg_text, count=1)
    if matched_canvas_rect:
        return themed, changed

    injected = _SVG_OPEN_RE.sub(
        lambda m: m.group(0) + f"<rect width='100%' height='100%' x='0' y='0' fill='{background}'/>",
        svg_text,
        count=1,
    )
    return injected, injected != svg_text


def normalize_svg_dark_background(svg_text: str, background: str = SVG_CANVAS_BG) -> Tuple[str, Dict[str, bool | str]]:
    themed = str(svg_text or "")
    if "<svg" not in themed.lower():
        return themed, {"changed": False, "background": background, "has_svg": False}

    themed, style_changed = _set_svg_background_style(themed, background)
    themed, rect_changed = _darken_background_rect(themed, background)
    changed = style_changed or rect_changed
    return themed, {
        "changed": changed,
        "background": background,
        "has_svg": True,
        "style_changed": style_changed,
        "rect_changed": rect_changed,
    }


def themed_svg_response(svg_text: str, *, background: str = SVG_CANVAS_BG, headers: Dict[str, str] | None = None) -> Response:
    themed, meta = normalize_svg_dark_background(svg_text, background=background)
    response_headers = dict(headers or {})
    response_headers["X-Warhead-SVG-Darkened"] = "1" if meta.get("changed") else "0"
    return Response(themed, mimetype="image/svg+xml", headers=response_headers)
