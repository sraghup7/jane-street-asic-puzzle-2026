"""tools/checks -- the executable gates.

Not a package of library code: this exists so a gate can import shared check
infrastructure as `tools.checks.<module>` (a real dotted import) rather than as a bare
sibling import, which the hygiene gate forbids repo-wide.
"""
