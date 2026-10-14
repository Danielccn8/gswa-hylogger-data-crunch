"""Single entry point for the desktop and its frozen subprocesses."""

import multiprocessing
import os
import runpy
import sys

from hylogger.runtime import root

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = sys.stdout
    sys.path.insert(0, str(root() / "platform"))
    if len(sys.argv) > 2 and sys.argv[1] == "--module":
        module = sys.argv[2]
        if module not in ("hylogger.jobs.worker", "hylogger.jobs.step", "uvicorn"):
            raise SystemExit("Unknown subprocess")
        sys.argv = [module, *sys.argv[3:]]
        runpy.run_module(module, run_name="__main__")
    else:
        from hylogger.desktop import main

        main()
