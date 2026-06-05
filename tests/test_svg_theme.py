from __future__ import annotations

import unittest

from api.svg_theme import SVG_CANVAS_BG, normalize_svg_dark_background


class SvgThemeTests(unittest.TestCase):
    def test_injects_background_rect_when_missing(self):
        svg = "<svg viewBox='0 0 10 10'><path d='M0 0 L10 10' /></svg>"
        themed, meta = normalize_svg_dark_background(svg)
        self.assertTrue(meta["changed"])
        self.assertIn(f"<rect width='100%' height='100%' x='0' y='0' fill='{SVG_CANVAS_BG}'/>", themed)
        self.assertIn(f"background:{SVG_CANVAS_BG};", themed)

    def test_replaces_white_full_canvas_rect_only(self):
        svg = (
            "<svg viewBox='0 0 10 10'>"
            "<rect width='10' height='10' x='0' y='0' style='fill:#FFFFFF;stroke:none'></rect>"
            "<path style='fill:#FFD500;stroke:#00D9FF' d='M0 0 L10 10' />"
            "</svg>"
        )
        themed, meta = normalize_svg_dark_background(svg)
        self.assertTrue(meta["changed"])
        self.assertIn(f"fill:{SVG_CANVAS_BG}", themed)
        self.assertIn("fill:#FFD500", themed)
        self.assertIn("stroke:#00D9FF", themed)

    def test_replaces_transparent_full_canvas_rect(self):
        svg = (
            "<svg viewBox='0 0 10 10'>"
            "<rect width='100%' height='100%' x='0' y='0' fill='#00000000'></rect>"
            "<circle cx='5' cy='5' r='4' fill='#00C752' />"
            "</svg>"
        )
        themed, meta = normalize_svg_dark_background(svg)
        self.assertTrue(meta["changed"])
        self.assertIn(f"fill='{SVG_CANVAS_BG}'", themed)
        self.assertIn("fill='#00C752'", themed)


if __name__ == "__main__":
    unittest.main()
