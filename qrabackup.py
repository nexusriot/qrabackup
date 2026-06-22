#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qrabackup launcher.

A small rsync + restic/S3 backup GUI for Linux. The implementation lives in the
``qrabackup_app`` package, which separates pure, unit-tested logic
(``model``/``settings``/``commands``/``parsers``/``formats``) from the Qt UI
(``runner``/``ui``).

Run
  python3 qrabackup.py
  # or equivalently:
  python3 -m qrabackup_app

Dependencies
- Python 3.8+
- PyQt5 (pip install PyQt5)
- rsync in PATH  (for rsync profiles)
- restic in PATH (for restic profiles)

See README.md and docs/DESIGN-restic-s3.md.
"""
import os
import sys

# Make the package importable when launched by absolute path / symlink.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qrabackup_app.ui import main  # noqa: E402

if __name__ == "__main__":
    main()
