"""Run a bounded two-hole spectral contrast from existing extracted packages."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from hylogger.config import AppConfig
from hylogger.spectral_experiment import run_spectral_contrast

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--holes", nargs=2, required=True)
    parser.add_argument("--region", choices=["swir", "tir"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = AppConfig.load(data_dir=args.data_dir)
    print(json.dumps(run_spectral_contrast(config, args.holes, args.output, args.region), indent=2))
