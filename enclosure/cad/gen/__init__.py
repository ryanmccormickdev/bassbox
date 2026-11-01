"""Generators and shared helpers for the pedal enclosure. Units are mm throughout; +Z is up as printed."""

from __future__ import annotations

import logging


def cli_logging(level: int = logging.INFO) -> None:
    """Logging for generator CLIs: ours at `level`, the CAD libraries' chatter at WARNING."""
    logging.basicConfig(level=level, format="%(message)s")
    for noisy in ("build123d", "ezdxf", "OCP", "trimesh"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
