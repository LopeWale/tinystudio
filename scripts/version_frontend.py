#!/usr/bin/env python3
"""Version the complete native module graph so browsers receive one coherent release."""
import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / "tinystudio" / "ui"
IMPORT = re.compile(r'''(?m)^(\s*(?:import|export)\b[^\n]*?\bfrom\s*["'])(\.[^"']+?\.js)(?:\?v=[a-f0-9]+)?(["'])''')


def version_frontend(root: Path = ROOT) -> str:
    sources = {path: IMPORT.sub(r"\1\2\3", path.read_text()) for path in sorted(root.rglob("*.js"))}
    digest = hashlib.sha256()
    for path, source in sources.items():
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + source.encode() + b"\0")
    release = digest.hexdigest()[:12]
    for path, source in sources.items():
        path.write_text(IMPORT.sub(lambda match: f"{match[1]}{match[2]}?v={release}{match[3]}", source))
    index = root / "index.html"
    index.write_text(re.sub(r'studio\.js(?:\?v=[a-f0-9]+)?', 'studio.js?v=' + release, index.read_text()))
    return release


if __name__ == "__main__":
    print(version_frontend())
