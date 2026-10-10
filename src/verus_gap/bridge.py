#!/usr/bin/env python3
"""Cargo-Verus proxy: build real ghost dependencies, stop explicitly at the root."""

import json
import os
import sys
from pathlib import Path


def main():
    real = os.environ["VERUS_GAP_REAL_VERUS"]
    args = sys.argv[1:]
    root = Path(os.environ["VERUS_GAP_ROOT"]).resolve()
    if len(args) > 1 and any(
        arg.endswith(".rs") and Path(arg).resolve() == root for arg in args[1:]
    ):
        environment = {
            key: value for key, value in os.environ.items()
            if (key.startswith(("CARGO_PKG_", "CARGO_FEATURE_", "DEP_"))
                or "__VERUS_DRIVER_" in key
                or key in {
                    "CARGO_BIN_NAME", "CARGO_CRATE_NAME", "CARGO_MANIFEST_DIR",
                    "CARGO_MANIFEST_PATH", "CARGO_PRIMARY_PACKAGE", "OUT_DIR",
                })
            and not any(part in key for part in ("TOKEN", "PASSWORD", "SECRET", "CREDENTIAL"))
        }
        Path(os.environ["VERUS_GAP_CAPTURE"]).write_text(json.dumps({
            "compiler": args[0], "args": args[1:], "cwd": os.getcwd(),
            "environment": environment, "root_execution": "intentionally_deferred",
            "setup_id": os.environ["VERUS_GAP_SETUP_ID"],
        }))
        print("verus-gap: dependencies built; root execution deliberately deferred to isolated probes.", file=sys.stderr)
        return 86
    os.execv(real, [real, *args])


if __name__ == "__main__":
    raise SystemExit(main())
