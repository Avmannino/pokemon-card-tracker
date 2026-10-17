"""Shrinks your slab photos for the site.

Reads slab-photos/PSA-<cert>-front.jpg (and -back; JPG, PNG or WebP), writes
web-sized copies to frontend/public/slabs/<cert>-<side>.jpg, and lists them
in frontend/src/slabPhotos.json, which the app uses to show the photo on
the card whose PSA cert number matches. Needs ffmpeg.

Run from the project root:  python3 scripts/slab_photos.py
"""

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "slab-photos"
OUTPUT = ROOT / "frontend" / "public" / "slabs"
MANIFEST = ROOT / "frontend" / "src" / "slabPhotos.json"

# Twice the widest the site shows a slab (the enlarged view), for sharp
# phone and retina screens.
WIDTH = 800

NAME = re.compile(r"^(?:PSA[-_ ]?)?(\d{5,})[-_ ](front|back)\.(jpe?g|png|webp)$", re.I)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    photos: dict[str, dict[str, str]] = {}

    for path in sorted(SOURCE.iterdir()):
        match = NAME.match(path.name)
        if not match:
            if path.name != "README.md":
                print(f"skipped {path.name} (name it PSA-<cert>-front.jpg or -back.jpg)")
            continue

        cert, side = match.group(1), match.group(2).lower()
        target = OUTPUT / f"{cert}-{side}.jpg"

        if not target.exists() or target.stat().st_mtime < path.stat().st_mtime:
            subprocess.run(
                [
                    "ffmpeg", "-loglevel", "error", "-y", "-i", str(path),
                    "-vf", f"scale='min({WIDTH},iw)':-2", "-q:v", "4",
                    str(target),
                ],
                check=True,
            )
            print(f"{path.name} -> {target.relative_to(ROOT)}")

        photos.setdefault(cert, {})[side] = f"slabs/{target.name}"

    MANIFEST.write_text(json.dumps(photos, indent=2, sort_keys=True) + "\n")
    print(f"{len(photos)} slab(s) listed in {MANIFEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
