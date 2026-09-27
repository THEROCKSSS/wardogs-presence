"""PyInstaller entry point.

Exists because PyInstaller executes its target as a top-level script, where
``from . import ...`` is invalid (there is no parent package). This module is
imported as ``wardogs_presence``'s caller instead, so the package's relative
imports resolve normally.

Not used when running from source — `python -m wardogs_presence.cli` works
directly. This exists purely for the frozen build.
"""

import multiprocessing
import sys

from wardogs_presence.cli import main

if __name__ == "__main__":
    # Required by PyInstaller on Windows for any code that may spawn
    # processes; harmless when nothing does.
    multiprocessing.freeze_support()
    sys.exit(main())
