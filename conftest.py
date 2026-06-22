"""Ensure the repo root is importable so ``import qrabackup_app`` works under
pytest regardless of the invocation directory."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
