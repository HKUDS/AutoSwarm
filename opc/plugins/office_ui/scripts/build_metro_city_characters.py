#!/usr/bin/env python3
"""
Assemble MetroCity v1 layered sprites (body + outfit + hair) into OPC-compatible
char_0.png … char_5.png sheets.

Expected AutoSwarm layout (see BootScene.ts):
  - Frame size 16×32, 7 columns × 3 rows = 112×96
  - Row 0: facing down (frames 0–6 of that row)
  - Row 1: facing up
  - Row 2: facing right (left uses flipX in Agent.ts)

MetroCity row layout: **768×32 = 24 frames × 32px** (NOT 16px). Slicing at 16px
would show only the left or right half of each sprite (“cut in half” in-game).

After compositing a row, each 32×32 source cell is resampled to 16×32 (nearest
neighbor) for the legacy AutoSwarm character sheet layout.

Per 24 frames: every 12 frames = one rotation — down(3)+left(3)+right(3)+up(3);
frames 12–23 repeat that with a second walk cycle (type/read variety).

AutoSwarm expects 7 slots per facing: walk 0–2, idle 1, type 3–4, read 5–6, etc.
We map:
  - Down:  0,1,2 (walk) + 12,13,14 (second down cycle for type) + 1 (read tail / idle)
  - Up:    9,10,11 + 21,22,23 + 10
  - Right: 6,7,8 + 18,19,20 + 7

Do NOT treat “12 frames per direction” along the X axis — that was the bug that
mixed left/right sprites into the down row (“split” characters).
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from PIL import Image

try:
    _NEAREST = Image.Resampling.NEAREST
except AttributeError:  # Pillow < 9.1
    _NEAREST = Image.NEAREST

FRAME_W = 16
FRAME_H = 32
COLS = 7
ROWS = 3

# Source cells in MetroCity sheets (before scaling down for AutoSwarm)
METRO_FRAME_W = 32
METRO_FRAME_H = 32

# Seven source frame indices per AutoSwarm row (within one MetroCity **24-frame** line, 32px each)
OPC_DOWN_FRAMES = (0, 1, 2, 12, 13, 14, 1)
OPC_UP_FRAMES = (9, 10, 11, 21, 22, 23, 10)
OPC_RIGHT_FRAMES = (6, 7, 8, 18, 19, 20, 7)


def metro_frame_to_opc(sheet_row: Image.Image, fi: int) -> Image.Image:
    """Take one 32×32 MetroCity cell for OPC."""
    x0 = fi * METRO_FRAME_W
    cell = sheet_row.crop((x0, 0, x0 + METRO_FRAME_W, METRO_FRAME_H))
    if cell.size != (METRO_FRAME_W, METRO_FRAME_H):
        raise ValueError(f"bad crop at fi={fi}: {cell.size}")
    if cell.size == (FRAME_W, FRAME_H):
        return cell
    return cell.resize((FRAME_W, FRAME_H), _NEAREST)


def paste_frames(sheet_row: Image.Image, indices: tuple[int, ...]) -> Image.Image:
    if len(indices) != COLS:
        raise ValueError(f"need {COLS} frame indices, got {len(indices)}")
    w_px = sheet_row.size[0]
    n_frames = w_px // METRO_FRAME_W
    out = Image.new("RGBA", (COLS * FRAME_W, FRAME_H))
    for col, fi in enumerate(indices):
        if fi < 0 or fi >= n_frames:
            raise ValueError(f"frame index {fi} out of range for row with {n_frames} cells")
        cell = metro_frame_to_opc(sheet_row, fi)
        out.paste(cell, (col * FRAME_W, 0))
    return out


def build_opc_sheet(sheet_row: Image.Image) -> Image.Image:
    out = Image.new("RGBA", (COLS * FRAME_W, ROWS * FRAME_H))
    out.paste(paste_frames(sheet_row, OPC_DOWN_FRAMES), (0, 0))
    out.paste(paste_frames(sheet_row, OPC_UP_FRAMES), (0, FRAME_H))
    out.paste(paste_frames(sheet_row, OPC_RIGHT_FRAMES), (0, 2 * FRAME_H))
    return out


def composite_row(body: Image.Image, outfit: Image.Image, hair: Image.Image) -> Image.Image:
    r = body.copy()
    r = Image.alpha_composite(r, outfit)
    r = Image.alpha_composite(r, hair)
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--metro-root",
        type=Path,
        default=Path("/root/autodl-tmp/MetroCity/MetroCity"),
        help="Path to MetroCity (v1) folder containing CharacterModel/, Hair/, Outfits/",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "frontend_src" / "public" / "assets" / "characters",
        help="Where to write char_0.png … char_5.png",
    )
    ap.add_argument(
        "--backup",
        action="store_true",
        help="Copy existing char_*.png to characters_opc_backup next to out-dir before overwrite",
    )
    args = ap.parse_args()

    metro = args.metro_root
    body_path = metro / "CharacterModel" / "Character Model.png"
    hairs_path = metro / "Hair" / "Hairs.png"
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.backup:
        bak = out_dir.parent / "characters_opc_backup"
        bak.mkdir(parents=True, exist_ok=True)
        for i in range(6):
            src = out_dir / f"char_{i}.png"
            if src.exists():
                shutil.copy2(src, bak / src.name)

    body = Image.open(body_path).convert("RGBA")
    hairs = Image.open(hairs_path).convert("RGBA")

    bw, bh = body.size
    if bw != 768 or bh % FRAME_H != 0:
        raise SystemExit(f"Unexpected Character Model size {bw}×{bh}, expected width 768")

    body_rows = bh // FRAME_H
    hair_rows = hairs.size[1] // FRAME_H
    if body_rows < 6:
        raise SystemExit(f"Character Model has only {body_rows} rows, need 6")

    for i in range(6):
        outfit_path = metro / "Outfits" / f"Outfit{i + 1}.png"
        if not outfit_path.exists():
            raise SystemExit(f"Missing {outfit_path}")
        outfit = Image.open(outfit_path).convert("RGBA")
        if outfit.size != (768, FRAME_H):
            raise SystemExit(f"Unexpected outfit size {outfit.size} for {outfit_path.name}")

        y0 = i * FRAME_H
        body_row = body.crop((0, y0, 768, y0 + FRAME_H))
        outfit_row = outfit.crop((0, 0, 768, FRAME_H))
        hair_i = min(i, hair_rows - 1)
        hy = hair_i * FRAME_H
        hair_row = hairs.crop((0, hy, 768, hy + FRAME_H))

        merged = composite_row(body_row, outfit_row, hair_row)
        sheet = build_opc_sheet(merged)
        dest = out_dir / f"char_{i}.png"
        sheet.save(dest)
        print(f"Wrote {dest} ({sheet.size[0]}×{sheet.size[1]})")


if __name__ == "__main__":
    main()
