"""Render the SVG logo into the favicon / app-icon PNG set.

    uv sync --extra icons
    uv run python scripts/make_icons.py

Outputs:
  assets/favicon-{16,32,48}.png, assets/icon-{128,180,192,256,512}.png
  assets/logo.png, assets/logo-banner.png
  web/public/favicon.png, web/public/apple-touch-icon.png
"""

from __future__ import annotations

from pathlib import Path

import cairosvg

BASE = Path(__file__).resolve().parent.parent
MARK = BASE / "assets" / "logo-mark.svg"
LOGO = BASE / "assets" / "logo.svg"
BANNER = BASE / "assets" / "logo-banner.svg"

SIZES = [16, 32, 48, 128, 180, 192, 256, 512]


def render(svg: Path, out: Path, size: int) -> None:
    cairosvg.svg2png(url=str(svg), write_to=str(out), output_width=size, output_height=size)
    print(f"wrote {out} ({size}x{size})")


def main() -> None:
    assets = BASE / "assets"
    public = BASE / "web" / "public"
    public.mkdir(parents=True, exist_ok=True)

    for size in SIZES:
        render(MARK, assets / f"icon-{size}.png", size)
        if size in (16, 32, 48):
            render(MARK, assets / f"favicon-{size}.png", size)

    render(MARK, public / "favicon.png", 32)
    render(MARK, public / "apple-touch-icon.png", 180)

    cairosvg.svg2png(url=str(LOGO), write_to=str(assets / "logo.png"), output_width=600, output_height=128)
    print(f"wrote {assets / 'logo.png'} (600x128)")
    cairosvg.svg2png(url=str(BANNER), write_to=str(assets / "logo-banner.png"), output_width=1200, output_height=400)
    print(f"wrote {assets / 'logo-banner.png'} (1200x400)")


if __name__ == "__main__":
    main()
