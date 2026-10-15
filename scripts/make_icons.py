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
MARKS = {
    "light": BASE / "assets" / "logo-mark.svg",
    "dark": BASE / "assets" / "logo-mark-dark.svg",
    "synthwave": BASE / "assets" / "logo-mark-synthwave.svg",
}
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

    for theme, mark in MARKS.items():
        suffix = "" if theme == "light" else f"-{theme}"
        for size in SIZES:
            render(mark, assets / f"icon{suffix}-{size}.png", size)
            if size in (16, 32, 48):
                render(mark, assets / f"favicon{suffix}-{size}.png", size)
        render(mark, public / f"favicon{suffix}.png", 32)
        render(mark, public / f"apple-touch-icon{suffix}.png", 180)
        (public / f"favicon{suffix}.svg").write_text(mark.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"wrote {public / f'favicon{suffix}.svg'}")

    cairosvg.svg2png(url=str(LOGO), write_to=str(assets / "logo.png"), output_width=600, output_height=128)
    print(f"wrote {assets / 'logo.png'} (600x128)")
    cairosvg.svg2png(url=str(BANNER), write_to=str(assets / "logo-banner.png"), output_width=1200, output_height=400)
    print(f"wrote {assets / 'logo-banner.png'} (1200x400)")


if __name__ == "__main__":
    main()
