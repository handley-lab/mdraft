"""Email drafting and gated sending over the mddb card substrate.

Draft cards hold the process (proposals, edits, steers); notmuch holds the
product (sent mail, referenced by Message-ID, never copied). See DESIGN.md
for the founding interests and CLAUDE.md for the working rules — above all
the never-event: no agent-reachable path may flush the outbox.
"""

from mddraft._core import (
    ENVELOPE_DOC,
    AlreadySent,
    AmbiguousSend,
    at,
    attachments,
    render,
    flush,
    reconcile,
)
from mddraft._correspondence import compose, forward, reply, source_text

__version__ = "0.0.14"
__all__ = [
    "ENVELOPE_DOC",
    "AlreadySent",
    "AmbiguousSend",
    "at",
    "attachments",
    "render",
    "flush",
    "reconcile",
    "compose",
    "forward",
    "reply",
    "source_text",
]
