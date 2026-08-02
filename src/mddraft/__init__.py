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
    compose,
    flush,
    reconcile,
)
from mddraft._correspondence import forward, reply

__version__ = "0.0.7"
__all__ = [
    "ENVELOPE_DOC",
    "AlreadySent",
    "AmbiguousSend",
    "at",
    "attachments",
    "compose",
    "flush",
    "reconcile",
    "forward",
    "reply",
]
