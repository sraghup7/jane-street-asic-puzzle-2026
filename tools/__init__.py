"""Project tooling for the Jane Street ASIC puzzle reverse-engineering effort.

Importable as `tools.*` from the repository root, e.g.:

    from tools import inventory, target
    from tools.puzzle import layers

Stage entry point:  python -m tools.puzzle <stage>
"""
import os as _os
import sys as _sys

# The gates print "Δ" and the acceptance matrix prints "§". On a Windows console or a redirected
# stream the default codec is cp1252, which cannot encode them, and the print raises. Every gate
# and stage imports `tools`, so this is the one place that fixes all of them.
for _stream in (_sys.stdout, _sys.stderr):
    if hasattr(_stream, 'reconfigure') and (_stream.encoding or '').lower() != 'utf-8':
        _stream.reconfigure(encoding='utf-8', errors='replace')


def utf8_env() -> dict[str, str]:
    """The environment for a child Python process: this one's, with UTF-8 mode forced on."""
    return {**_os.environ, 'PYTHONUTF8': '1'}
