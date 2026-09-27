"""Frozen-build launcher.

PyInstaller executes its entry script as a top-level module, so a file that
uses relative imports (`from . import x`) cannot be the entry point directly.
This stub imports the package absolutely and calls into it.

Kept separate from __main__.py so the two entry paths stay obvious:
  * `python -m wardogs_presence`  ->  __main__.py   (source runs)
  * WardogsPresence.exe           ->  this file     (frozen runs)
"""

import multiprocessing
import sys

if __name__ == "__main__":
    # Must happen before any code that might spawn a process; PyInstaller
    # otherwise re-launches the app in every child on Windows.
    multiprocessing.freeze_support()

    from wardogs_presence.cli import main

    sys.exit(main())
