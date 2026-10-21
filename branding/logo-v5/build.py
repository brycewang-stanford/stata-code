"""Generate the stata-code v5 logo kit (StatsPAI-family palette).

Usage: python3 branding/logo-v5/build.py  (writes next to this script; needs rsvg-convert + Inter,
variable font, path overridable via INTER_TTF)
Wordmark is outlined from Inter (variable, wght 900) so SVGs are font-independent.
"""
import os
import subprocess
import sys
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
OUT.mkdir(parents=True, exist_ok=True)

# ---- palette (sampled from the StatsPAI banner) ----
INK = "#1A1050"
CREAM = "#FAF8F4"
ACCENT = ["#CC30C5", "#7E3AEC", "#486BE3", "#1C94DB"]  # wordmark gradient
NEON = ["#FF4FD8", "#9D6CFF", "#2EDBFF"]  # whisker glow, top → bottom

# ---- wordmark outlines ----
_font = instantiateVariableFont(
    TTFont(os.environ.get("INTER_TTF", os.path.expanduser("~/Library/Fonts/Inter.ttf"))), {"wght": 900, "opsz": 32}
)
_gs = _font.getGlyphSet()
_cmap = _font.getBestCmap()
_upm = _font["head"].unitsPerEm
_hmtx = _font["hmtx"]


def text_path(s, size, tracking=-0.025):
    """Return (svg path d, advance width) for `s` at `size` px, baseline at y=0."""
    scale = size / _upm
    x = 0.0
    pen = SVGPathPen(_gs)
    for ch in s:
        g = _cmap[ord(ch)]
        tp = TransformPen(pen, (scale, 0, 0, -scale, x, 0))
        _gs[g].draw(tp)
        x += _hmtx[g][0] * scale + tracking * size
    return pen.getCommands(), x - tracking * size


# ---- mark (128 × 128 tile) ----
ROWS = [  # (y, x1, x2, point estimate x, neon colour)
    (38, 40, 96, 76, NEON[0]),
    (64, 30, 72, 42, NEON[1]),
    (90, 48, 94, 80, NEON[2]),
]
AXIS_X = 60


def mark_defs(p, glow=True):
    d = f"""
    <linearGradient id="{p}tile" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#8E2AA7"/>
      <stop offset="0.45" stop-color="#1A0F4A"/>
      <stop offset="0.7" stop-color="#140E44"/>
      <stop offset="1" stop-color="#1D5C8C"/>
    </linearGradient>
    <radialGradient id="{p}glowTL" cx="0.12" cy="0.1" r="0.6">
      <stop offset="0" stop-color="#C33BD0" stop-opacity="0.75"/>
      <stop offset="1" stop-color="#C33BD0" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="{p}glowBR" cx="0.95" cy="0.95" r="0.55">
      <stop offset="0" stop-color="#36AFDA" stop-opacity="0.7"/>
      <stop offset="1" stop-color="#36AFDA" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="{p}rim" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#C86BFF"/>
      <stop offset="0.5" stop-color="#5B3BB0" stop-opacity="0.6"/>
      <stop offset="1" stop-color="#3FC3F0"/>
    </linearGradient>"""
    if glow:
        d += f"""
    <filter id="{p}blur" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="3.2"/>
    </filter>"""
    return d


def mark_body(p, glow=True, axis=True, mesh=True):
    parts = [
        f'<rect x="4" y="4" width="120" height="120" rx="28" fill="url(#{p}tile)"/>',
        f'<rect x="4" y="4" width="120" height="120" rx="28" fill="url(#{p}glowTL)"/>',
        f'<rect x="4" y="4" width="120" height="120" rx="28" fill="url(#{p}glowBR)"/>',
        f'<rect x="4.75" y="4.75" width="118.5" height="118.5" rx="27.25" fill="none" '
        f'stroke="url(#{p}rim)" stroke-width="1.5"/>',
    ]
    if axis:
        parts.append(
            f'<line x1="{AXIS_X}" y1="22" x2="{AXIS_X}" y2="106" stroke="#FFFFFF" '
            'stroke-opacity="0.38" stroke-width="2.5" stroke-linecap="round" stroke-dasharray="3 6"/>'
        )
    if mesh:
        # faint estimate path, echoing StatsPAI's network mesh
        pts = " ".join(f"{px},{y}" for y, _, _, px, _ in ROWS)
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="#FFFFFF" stroke-opacity="0.22" '
            'stroke-width="1.5" stroke-linejoin="round"/>'
        )
    for y, x1, x2, px, c in ROWS:
        if glow:
            parts.append(
                f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="{c}" stroke-width="9" '
                f'stroke-linecap="round" opacity="0.75" filter="url(#{p}blur)"/>'
            )
        parts.append(
            f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="{c}" stroke-width="5" stroke-linecap="round"/>'
        )
    for y, _, _, px, c in ROWS:
        if glow:
            parts.append(f'<circle cx="{px}" cy="{y}" r="12" fill="{c}" filter="url(#{p}blur)"/>')
        parts.append(f'<circle cx="{px}" cy="{y}" r="8" fill="#FFFFFF"/>')
    return "\n    ".join(parts)


def svg(viewbox, label, defs, body):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" role="img" aria-label="{label}">\n'
        f"  <title>{label}</title>\n  <defs>{defs}\n  </defs>\n  {body}\n</svg>\n"
    )


def wordmark(size, base_color, x0, baseline, gid):
    """'stata' in base colour + '-code' in the StatsPAI accent gradient."""
    d1, w1 = text_path("stata", size)
    d2, w2 = text_path("-code", size)
    gap = -0.025 * size
    grad = (
        f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" '
        f'x1="0" y1="0" x2="{w2:.1f}" y2="0">'
        + "".join(
            f'<stop offset="{i / (len(ACCENT) - 1):.3f}" stop-color="{c}"/>'
            for i, c in enumerate(ACCENT)
        )
        + "</linearGradient>"
    )
    body = (
        f'<path transform="translate({x0} {baseline})" fill="{base_color}" d="{d1}"/>\n  '
        f'<path transform="translate({x0 + w1 + gap:.2f} {baseline})" fill="url(#{gid})" d="{d2}"/>'
    )
    return grad, body, w1 + gap + w2


def write(name, content, *renders):
    path = OUT / name
    path.write_text(content)
    for w in renders:
        stem = path.stem
        subprocess.run(
            ["rsvg-convert", "-w", str(w), "-o", str(OUT / f"{stem}@{w}.png"), str(path)],
            check=True,
        )


# mark
write("mark.svg", svg("0 0 128 128", "stata-code mark", mark_defs("m"), mark_body("m")), 512, 1024)

# horizontal lockups (mark 128 + gap 40 + wordmark)
for name, base in (("horizontal.svg", INK), ("horizontal-on-dark.svg", CREAM)):
    size = 92
    grad, wm, ww = wordmark(size, base, 168, 95, "wm")
    W = int(168 + ww + 8)
    body = f'<g>{mark_body("m")}</g>\n  {wm}'
    write(name, svg(f"0 0 {W} 128", "stata-code", mark_defs("m") + "\n    " + grad, body), 1024, 1280)

# stacked
size = 56
_, _, ww = wordmark(size, INK, 0, 0, "wm")
W = int(max(ww, 128) + 16)
grad, wm, ww = wordmark(size, INK, (W - ww) / 2, 196, "wm")
body = f'<g transform="translate({(W - 128) / 2} 0)">{mark_body("m")}</g>\n  {wm}'
write("stacked.svg", svg(f"0 0 {W} 212", "stata-code", mark_defs("m") + "\n    " + grad, body), 600)

# favicon: no glow / axis / mesh, chunkier whiskers for small sizes
fav_body = mark_body("f", glow=False, axis=False, mesh=False).replace(
    'stroke-width="5"', 'stroke-width="9"'
).replace('r="8"', 'r="10.5"')
write("favicon.svg", svg("0 0 128 128", "stata-code favicon", mark_defs("f", glow=False), fav_body))
for w, n in ((16, "favicon-16.png"), (32, "favicon-32.png")):
    subprocess.run(["rsvg-convert", "-w", str(w), "-o", str(OUT / n), str(OUT / "favicon.svg")], check=True)
subprocess.run(["rsvg-convert", "-w", "180", "-o", str(OUT / "apple-touch-icon-180.png"), str(OUT / "mark.svg")], check=True)

print("ok", sorted(p.name for p in OUT.iterdir()))
