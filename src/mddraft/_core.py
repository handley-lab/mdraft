"""Draft cards over mddb and the msmtp flush: at, compose, flush.

The library is the substrate half of gated email sending. It renders a draft
card at a pinned git sha (so approval display and flush share one immutable
object), composes it to RFC822, and hands the bytes to msmtp exactly once.
The gate itself — who may call flush, under which user, behind which token —
is tenancy wiring, deliberately outside this library.
"""

import subprocess
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import SMTP
from email.utils import formataddr, make_msgid
from pathlib import Path

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
  attachments: [card-id, ...]  optional; ordered immutable attachment cards
  state: draft | abandoned     workflow convention; inert data — nothing
                         triggers on it (approval is an act, not a field)
  sent_mid / sent_sha / sent_at   stamped by flush() and only meaningful when
                         flush stamped them

bcc is deliberately absent from v1: envelope-vs-header semantics with
``msmtp -t`` are a trap, deferred until actually needed.

Body = the exact plain-text email body. Sent mail is never copied into cards:
notmuch holds the product, sent_mid references it.
"""


class AlreadySent(RuntimeError):
    """The card already carries a sent_mid — flushing again would resend."""


def _at(deck, card_id, sha):
    paths = subprocess.run(
        ["git", "-C", str(deck), "ls-tree", "-r", "-z", "--name-only", sha],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.split("\0")
    for relpath in paths:
        if not relpath.endswith(".md"):
            continue
        text = subprocess.run(
            ["git", "-C", str(deck), "show", f"{sha}:{relpath}"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        card = mddb.Card.from_text(text)
        if card.id == card_id:
            return card, relpath
    raise KeyError(card_id)


def at(deck, card_id, sha):
    """Read a card's content as it existed at a commit.

    Resolves ``card_id`` inside the pinned tree, so a later move or deletion
    cannot change the object approved at ``sha``.

    Args:
        deck: Path to the (trusted) mddb deck.
        card_id: The card's id.
        sha: The commit whose bytes the caller approved.

    Returns:
        The Card parsed from the bytes at ``sha`` — immutable with respect to
        any later working-tree or HEAD change.
    """
    return _at(deck, card_id, sha)[0]


def attachments(deck, card, sha):
    """Return ordered ``(attachment card, pinned bytes)`` pairs."""
    result = []
    for card_id in card.yaml.get("attachments", []):
        attachment, relpath = _at(deck, card_id, sha)
        data = subprocess.run(
            ["git", "-C", str(deck), "show", f"{sha}:{Path(relpath).with_suffix('.bin')}"],
            capture_output=True,
            check=True,
        ).stdout
        result.append((attachment, data))
    return result


def compose(card, mid="", attachment_data=(), realname=""):
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
    sender = card.yaml["from"]
    msg["From"] = formataddr((realname, sender)) if realname else sender
    msg["To"] = ", ".join(card.yaml["to"])
    if "cc" in card.yaml:
        msg["Cc"] = ", ".join(card.yaml["cc"])
    msg["Subject"] = card.yaml["subject"]
    if mid:
        msg["Message-ID"] = mid
    if "in_reply_to" in card.yaml:
        msg["In-Reply-To"] = card.yaml["in_reply_to"]
    if "references" in card.yaml:
        references = card.yaml["references"]
        if not isinstance(references, list):
            raise TypeError("references must be a list")
        msg["References"] = " ".join(references)
    msg.set_content(card.body)
    for attachment, data in attachment_data:
        representation = attachment.yaml["representation"]
        filename = attachment.yaml.get("filename") or None
        if representation == "payload":
            maintype, subtype = attachment.yaml["content_type"].split("/", 1)
            msg.add_attachment(
                data, maintype=maintype, subtype=subtype, filename=filename
            )
        elif representation == "message":
            msg.add_attachment(
                BytesParser(policy=SMTP).parsebytes(data), filename=filename
            )
        elif representation == "entity":
            if not msg.is_multipart():
                msg.make_mixed()
            entity = BytesParser(policy=SMTP).parsebytes(data)
            if entity.get_content_disposition() is None:
                entity["Content-Disposition"] = "attachment"
            msg.attach(entity)
        else:
            raise ValueError(representation)
    return msg


def _sign(msg, signer):
    part = deepcopy(msg)
    for name in list(part.keys()):
        if name.lower() not in (
            "content-type",
            "content-transfer-encoding",
            "content-disposition",
            "mime-version",
        ):
            del part[name]
    part_bytes = part.as_bytes(policy=SMTP)
    signature = subprocess.run(
        signer,
        input=part_bytes,
        capture_output=True,
        check=True,
    ).stdout
    signature = signature.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    boundary = uuid.uuid4().hex
    outer = EmailMessage(policy=SMTP)
    for name, value in msg.items():
        if name.lower() not in (
            "content-type",
            "content-transfer-encoding",
            "mime-version",
        ):
            outer[name] = value
    outer["MIME-Version"] = "1.0"
    outer["Content-Type"] = (
        f'multipart/signed; micalg="pgp-sha512"; '
        f'protocol="application/pgp-signature"; boundary="{boundary}"'
    )
    outer.set_payload("")
    return bytes(outer) + b"".join(
        [
            b"--" + boundary.encode() + b"\r\n",
            part_bytes,
            b"\r\n--" + boundary.encode() + b"\r\n",
            b'Content-Type: application/pgp-signature; name="signature.asc"\r\n',
            b'Content-Disposition: attachment; filename="signature.asc"\r\n',
            b"\r\n",
            signature,
            b"\r\n--" + boundary.encode() + b"--\r\n",
        ]
    )


def flush(deck, card_id, sha, msmtp=("msmtp",), *, realname="", signer=()):
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
    msg = compose(
        card,
        mid,
        attachments(deck, card, sha),
        realname=realname,
    )
    msg["Date"] = datetime.now(timezone.utc)
    payload = _sign(msg, signer) if signer else bytes(msg)
    subprocess.run(
        [*msmtp, "-a", sender, "-t"],
        input=payload,
        stderr=subprocess.PIPE,
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
