"""Pytest config: make backend modules importable as top-level packages."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
