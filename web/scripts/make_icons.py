"""Draws the app's icons (a basketball on the app's dark background) into web/public/.

Standard library only, so no image tool has to be installed. The PNGs are committed; run this
only to change the design:  python3 web/scripts/make_icons.py
"""

import struct
import sys
import zlib
from pathlib import Path

BACKGROUND = (0x14, 0x18, 0x1F)
BALL = (0xE8, 0x77, 0x2E)
SEAM = BACKGROUND
SEAM_WIDTH = 0.075  # in ball radii
SAMPLES = 3  # per pixel side, for smooth edges

# The two curved seams are arcs of circles of radius 0.9 ball radii, centred 1.25 to either side
# of the ball's centre, so they curve like ")" on the left and "(" on the right.
ARC_RADIUS = 0.9036
ARC_OFFSET = 1.2536

PUBLIC = Path(__file__).resolve().parents[1] / "public"


def colour_at(x: float, y: float) -> tuple[int, int, int]:
    """The colour at (x, y), in ball radii from the ball's centre."""
    if x * x + y * y > 1.0:
        return BACKGROUND
    half = SEAM_WIDTH / 2
    on_seam = (
        abs(x) < half
        or abs(y) < half
        or abs(((x - ARC_OFFSET) ** 2 + y * y) ** 0.5 - ARC_RADIUS) < half
        or abs(((x + ARC_OFFSET) ** 2 + y * y) ** 0.5 - ARC_RADIUS) < half
    )
    return SEAM if on_seam else BALL


def render(size: int, ball_radius: float) -> bytes:
    """A size x size RGB image as raw rows, each starting with PNG's "no filter" byte."""
    rows = bytearray()
    centre = size / 2
    for py in range(size):
        rows.append(0)
        for px in range(size):
            red = green = blue = 0
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    x = (px + (sx + 0.5) / SAMPLES - centre) / ball_radius
                    y = (py + (sy + 0.5) / SAMPLES - centre) / ball_radius
                    r, g, b = colour_at(x, y)
                    red, green, blue = red + r, green + g, blue + b
            n = SAMPLES * SAMPLES
            rows += bytes((red // n, green // n, blue // n))
    return bytes(rows)


def chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def png(size: int, ball_radius: float) -> bytes:
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB, no alpha
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(render(size, ball_radius), 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    # (file, size, ball radius as a share of the size). The maskable icon keeps the ball inside
    # the circle every phone is allowed to crop to (the middle 80%); the others fill more.
    icons = [
        ("icon-192.png", 192, 0.36),
        ("icon-512.png", 512, 0.36),
        ("icon-maskable-512.png", 512, 0.28),
        ("apple-touch-icon.png", 180, 0.36),
    ]
    for name, size, share in icons:
        (PUBLIC / name).write_bytes(png(size, size * share))
        sys.stdout.write(f"wrote {PUBLIC / name}\n")


if __name__ == "__main__":
    main()
