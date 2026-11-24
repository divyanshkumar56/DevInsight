"""Rebuild sample_project.zip from the sample_project/ folder.

Also injects a fake `venv/` file and a `__pycache__/` entry so you can see the
analyzer's folder-ignore logic in action.

    python scripts/build_sample_zip.py
"""

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "sample_project"
OUT = ROOT / "sample_project.zip"


def main() -> None:
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(SRC.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                zf.write(path, Path("sample_project") / path.relative_to(SRC))
        # These must be ignored by the analyzer:
        zf.writestr("sample_project/venv/lib/site_helper.py", "def ignored():\n    return 'venv code is skipped'\n")
        zf.writestr("sample_project/__pycache__/README.txt", "compiled cache - ignored\n")
        zf.writestr("sample_project/README.md", "# Sample project for DevInsight\n")
    print(f"Wrote {OUT}")
    with zipfile.ZipFile(OUT) as zf:
        for name in zf.namelist():
            print("  ", name)


if __name__ == "__main__":
    main()

