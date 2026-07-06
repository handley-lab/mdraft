"""Draft cards over mddb and the msmtp flush: at, compose, flush.

The library is the substrate half of gated email sending. It renders a draft
card at a pinned git sha (so approval display and flush share one immutable
object), composes it to RFC822, and hands the bytes to msmtp exactly once.
The gate itself — who may call flush, under which user, behind which token —
is tenancy wiring, deliberately outside this library.
"""

import subprocess
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid

import mddb

ENVELOPE_DOC = """\
Draft-card envelope convention (documented, never validated — the caller
constructs the calls; compose() raises KeyError on missing required keys):

  to: [addr, ...]        required at flush
  cc: [addr, ...]        optional
  from: <address>        required — must equal an msmtp account name; selects
                         the sending identity (msmtp -a <from>)
  subject: <text>        required
  in_reply_to: "<mid>"   optional; threading
  references: ["<mid>", ...]   optional; threading
  state: draft | abandoned     workflow convention; inert data — nothing
                         triggers on it (approval is an act, not a field)
  sent_mid / sent_sha / sent_at   stamped by flush() and only meaningful when
                         flush stamped them

Body = the exact plain-text email body. Sent mail is never copied into cards:
notmuch holds the product, sent_mid references it.
"""


class AlreadySent(RuntimeError):
    """The card already carries a sent_mid — flushing again would resend."""


def at(deck, card_id, sha):
    """Read a card's content as it existed at a commit.

    Resolves ``card_id`` to its relpath in the CURRENT deck index, then reads
    that relpath at ``sha`` via ``git show``. Deliberately does not scan
    historical trees: if the card was moved between display and send, git
    fails visibly and the caller re-reads.

    Args:
        deck: Path to the (trusted) mddb deck.
        card_id: The card's id.
        sha: The commit whose bytes the caller approved.

    Returns:
        The Card parsed from the bytes at ``sha`` — immutable with respect to
        any later working-tree or HEAD change.
    """
    db = mddb.MDDB(deck)
    ((relpath,),) = db.conn.execute(
        "SELECT relpath FROM entries WHERE id = ?", (card_id,)
    )
    text = subprocess.run(
        ["git", "-C", str(deck), "show", f"{sha}:{relpath}"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return mddb.Card.from_text(text)


def compose(card, mid=""):
    """Compose a draft card into an RFC822 message.

    Pure and deterministic: no I/O, no clock, no defaults invented — the
    same card and mid always yield the same bytes. Send-time metadata (the
    Date header, like the Message-ID) is stamped by flush(), where the send
    effect lives. Missing required envelope keys raise KeyError naturally.

    Args:
        card: A draft card following ENVELOPE_DOC.
        mid: Message-ID to stamp, when the message is actually being sent.

    Returns:
        An email.message.EmailMessage ready for msmtp -t.
    """
    msg = EmailMessage()
    msg["From"] = card.yaml["from"]
    msg["To"] = ", ".join(card.yaml["to"])
    if "cc" in card.yaml:
        msg["Cc"] = ", ".join(card.yaml["cc"])
    msg["Subject"] = card.yaml["subject"]
    if mid:
        msg["Message-ID"] = mid
    if "in_reply_to" in card.yaml:
        msg["In-Reply-To"] = card.yaml["in_reply_to"]
    if "references" in card.yaml:
        msg["References"] = " ".join(card.yaml["references"])
    msg.set_content(card.body)
    return msg


def flush(deck, card_id, sha, msmtp=("msmtp",)):
    """Send the card's bytes at ``sha`` and stamp the send on the deck.

    msmtp is invoked exactly once per call. The ConflictError retry wraps
    ONLY the stamp commit, reusing the already-generated Message-ID — a
    concurrent deck commit can never cause a second send. A nonzero msmtp
    exit propagates before anything is committed.

    Args:
        deck: Path to the outbox deck.
        card_id: The card to send.
        sha: The commit Will read verbatim; its bytes go to the wire.
        msmtp: The msmtp argv prefix (tests substitute a capture script).

    Returns:
        The Message-ID of the sent mail (notmuch holds the product).

    Raises:
        AlreadySent: The card at HEAD already carries a sent_mid.
        subprocess.CalledProcessError: msmtp exited nonzero; nothing was
            committed and the card is still a draft.
    """
    db = mddb.MDDB(deck)
    head_yaml = db.read(card_id).yaml
    if "sent_mid" in head_yaml:
        raise AlreadySent(head_yaml["sent_mid"])
    card = at(deck, card_id, sha)
    sender = card.yaml["from"]
    mid = make_msgid(domain=sender.split("@")[1])
    msg = compose(card, mid)
    msg["Date"] = datetime.now(timezone.utc)
    subprocess.run(
        [*msmtp, "-a", sender, "-t"],
        input=bytes(msg),
        check=True,
    )
    stamp = {
        "state": "sent",
        "sent_mid": mid,
        "sent_sha": sha,
        "sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    while True:
        try:
            with db.editor(rationale=f"sent {mid}") as editor:
                fresh = editor.read(card_id)
                fresh.yaml.update(stamp)
                editor.update(fresh, summary=fresh.summary)
            return mid
        except mddb.ConflictError:
            db = mddb.MDDB(deck)
