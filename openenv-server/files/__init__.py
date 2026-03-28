"""
File registry module for U2 XPIA (indirect prompt injection) scenarios.

Provides per-session virtual file storage for mounting pre-extracted content
that mock file-reading tools can return during evaluation.
"""

from .registry import FileRegistry

__all__ = ["FileRegistry"]
