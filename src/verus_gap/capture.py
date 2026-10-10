#!/usr/bin/env python3
"""Capture the selected Cargo invocation without changing its arguments."""

import json
import os
import sys
from pathlib import Path


compiler, *args = sys.argv[1:]
root = Path(os.environ["VERUS_GAP_ROOT"]).resolve()
if any(
    arg.endswith(".rs") and Path(arg).resolve() == root
    for arg in args
):
    environment = {
        key: value
        for key, value in os.environ.items()
        if (key.startswith(("CARGO_", "DEP_")) or key == "OUT_DIR")
        and not any(part in key for part in ("TOKEN", "PASSWORD", "SECRET", "CREDENTIAL"))
        and key != "CARGO_MAKEFLAGS"
    }
    Path(os.environ["VERUS_GAP_CAPTURE"]).write_text(
        json.dumps({"args": args, "cwd": os.getcwd(), "environment": environment})
    )
os.execv(compiler, [compiler, *args])
