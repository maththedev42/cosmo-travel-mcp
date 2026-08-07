#!/usr/bin/env python3
"""Print one version's section of CHANGELOG.md, for a GitHub Release body.

Run as ``scripts/release_notes.py v1.2.0`` (the leading ``v`` is optional).

This exists as a file rather than a few lines inlined in the workflow because
the failure it guards against is a silent one. If the heading format drifts —
a renamed section, a version that was tagged but never written up — a lenient
extractor emits an empty string and the release is published with no notes at
all, which nobody notices until someone goes looking for them months later.
So a missing or empty section is a non-zero exit, and the release step fails
loudly instead.

Trailing link-reference definitions (``[1.2.0]: https://…``) are dropped: they
live at the foot of the file and would otherwise be swept into the last
section, which is the oldest release.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

# A section runs from its own heading to the next version heading, or to the
# end of the file when it is the oldest one.
_SECTION = r"^## \[{version}\][^\n]*\n(.*?)(?=^## \[|\Z)"

_LINK_REF = re.compile(r"^\[[^\]]+\]:\s*http", re.M)


def extract(text: str, version: str) -> str:
    """Return the body of ``## [version]``, or raise ``LookupError``."""
    match = re.search(
        _SECTION.format(version=re.escape(version)), text, re.M | re.S
    )
    if match is None:
        raise LookupError(f"CHANGELOG.md has no '## [{version}]' section")

    body = "\n".join(
        line for line in match.group(1).splitlines() if not _LINK_REF.match(line)
    ).strip()

    if not body:
        raise LookupError(f"the '## [{version}]' section of CHANGELOG.md is empty")
    return body


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0] if argv else 'release_notes.py'} <version>",
              file=sys.stderr)
        return 2

    version = argv[1].lstrip("v")
    try:
        print(extract(CHANGELOG.read_text(encoding="utf-8"), version))
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
