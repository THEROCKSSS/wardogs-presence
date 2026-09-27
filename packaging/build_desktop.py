"""Build and verify the portable, single-file Electron release on Windows."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / ".tools"
ENGINE = TOOLS / "engine-dist" / "WardogsPresence"


def run(command: list[str], *, cwd: Path = ROOT, env: dict | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def build() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Build the Windows EXE on Windows")
    python = str(Path(sys.executable))
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise RuntimeError("Node/npm is needed to build the Electron app")

    env = os.environ.copy()
    env["PYINSTALLER_CONFIG_DIR"] = str(TOOLS / "pyinstaller-cache")

    run([python, "-m", "pytest", "tests", "-q"], env=env)
    run([npm, "ci"], cwd=ROOT / "frontend", env=env)
    run([npm, "run", "build"], cwd=ROOT / "frontend", env=env)
    run([
        python, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(TOOLS / "engine-dist"),
        "--workpath", str(TOOLS / "engine-build"),
        str(ROOT / "packaging" / "WardogsPresence.spec"),
    ], env=env)

    required = [
        ENGINE / "WardogsPresence.exe",
        ENGINE / "WardogsPresence-debug.exe",
        ENGINE / "_internal" / "tesseract" / "tesseract.exe",
        ENGINE / "_internal" / "tesseract" / "tessdata" / "eng.traineddata",
    ]
    for item in required:
        if not item.is_file():
            raise RuntimeError(f"Missing bundled component: {item.relative_to(ROOT)}")
    probe = subprocess.run(
        [str(required[2]), "--list-langs"], capture_output=True, text=True, timeout=30
    )
    if probe.returncode or "eng" not in probe.stdout:
        raise RuntimeError("Bundled Tesseract cannot find English language data")

    probe = subprocess.run([str(required[1]), "--bridge", "monitors"],
                           input="{}", capture_output=True, text=True, timeout=30)
    if probe.returncode or '"monitors"' not in probe.stdout:
        raise RuntimeError("Bundled Python bridge did not start cleanly")

    run([npm, "ci"], env=env)
    run([npm, "run", "dist:win"], env=env)
    final = ROOT / "dist" / "WardogsPresence-1.1.0-win-x64.exe"
    if not final.is_file():
        raise RuntimeError("Electron Builder did not produce the portable EXE")
    print(f"Release candidate: {final} ({final.stat().st_size / 1_048_576:.1f} MiB)")


if __name__ == "__main__":
    build()
