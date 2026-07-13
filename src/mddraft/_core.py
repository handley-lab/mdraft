"""Draft cards over mddb and the msmtp flush: at, compose, flush.

The library is the substrate half of gated email sending. It renders a draft
card at a pinned git sha (so approval display and flush share one immutable
object), composes it to RFC822, and hands the bytes to msmtp exactly once.
The gate itself — who may call flush, under which user, behind which token —
is tenancy wiring, deliberately outside this library.
"""

import subprocess
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formataddr, make_msgid

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

bcc is deliberately absent from v1: envelope-vs-header semantics with
``msmtp -t`` are a trap, deferred until actually needed.

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


def compose(card, mid="", realname=""):
    """Compose a draft card into an RFC822 message.

    Pure and deterministic: no I/O, no clock, no defaults invented — the
    same card and mid always yield the same bytes. Send-time metadata (the
    Date header, like the Message-ID) is stamped by flush(), where the send
    effect lives. Missing required envelope keys raise KeyError naturally.

    Args:
        card: A draft card following ENVELOPE_DOC.
        mid: Message-ID to stamp, when the message is actually being sent.
        realname: Display name for the From header; empty emits the bare
            envelope address.

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
        msg["References"] = " ".join(card.yaml["references"])
    msg.set_content(card.body)
    return msg


def _sign(msg, sign_key):
    """Emit ``msg`` as RFC 3156 multipart/signed bytes, signing with gpg.

    The text part is serialised exactly once (SMTP policy, CRLF); those
    bytes are both what gpg signs and what lands verbatim as the first
    part of the framed message — the signature is a function of the
    transmitted bytes, never a re-serialisation of them. The digest is
    pinned to SHA256 so the ``micalg`` parameter is truthful.

    Args:
        msg: The composed message (flat text/plain with envelope headers).
        sign_key: gpg key selector passed to ``-u``; the keyring comes
            from ``GNUPGHOME`` in the environment.

    Returns:
        The complete multipart/signed message as bytes, ready for msmtp.

    Raises:
        subprocess.CalledProcessError: gpg exited nonzero; nothing was
            signed and nothing must be sent.
    """
    part = EmailMessage(policy=SMTP)
    part.set_content(msg.get_content())
    del part["MIME-Version"]
    part_bytes = bytes(part)
    signature = subprocess.run(
        [
            "gpg",
            "--batch",
            "--armor",
            "--detach-sign",
            "--digest-algo",
            "SHA256",
            "-u",
            sign_key,
        ],
        input=part_bytes,
        capture_output=True,
        check=True,
    ).stdout
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
        f'multipart/signed; micalg="pgp-sha256"; '
        f'protocol="application/pgp-signature"; boundary="{boundary}"'
    )
    outer.set_payload("")
    return bytes(outer) + b"".join(
        [
            b"--" + boundary.encode() + b"\r\n",
            part_bytes,
            b"\r\n--" + boundary.encode() + b"\r\n",
            b'Content-Type: application/pgp-signature; name="signature.asc"\r\n',
            b"\r\n",
            signature,
            b"\r\n--" + boundary.encode() + b"--\r\n",
        ]
    )


def flush(deck, card_id, sha, msmtp=("msmtp",), *, realname="", sign_key=""):
    """Send the card's bytes at ``sha`` and stamp the send on the deck.

    msmtp is invoked exactly once per call. The ConflictError retry wraps
    ONLY the stamp commit, reusing the already-generated Message-ID — a
    concurrent deck commit can never cause a second send. A nonzero msmtp
    exit propagates before anything is committed, and a nonzero gpg exit
    propagates before msmtp is even invoked.

    Args:
        deck: Path to the outbox deck.
        card_id: The card to send.
        sha: The commit Will read verbatim; its bytes go to the wire.
        msmtp: The msmtp argv prefix (tests substitute a capture script).
        realname: Display name for the From header, forwarded to compose.
        sign_key: When non-empty, the message leaves as RFC 3156
            multipart/signed, detach-signed by this gpg key (see _sign);
            empty sends unsigned, byte-identical to the pre-signing path.

    Returns:
        The Message-ID of the sent mail (notmuch holds the product).

    Raises:
        AlreadySent: The card at HEAD already carries a sent_mid.
        subprocess.CalledProcessError: gpg or msmtp exited nonzero;
            nothing was committed and the card is still a draft.
    """
    db = mddb.MDDB(deck)
    head_yaml = db.read(card_id).yaml
    if "sent_mid" in head_yaml:
        raise AlreadySent(head_yaml["sent_mid"])
    card = at(deck, card_id, sha)
    sender = card.yaml["from"]
    mid = make_msgid(domain=sender.split("@")[1])
    msg = compose(card, mid, realname=realname)
    msg["Date"] = datetime.now(timezone.utc)
    payload = _sign(msg, sign_key) if sign_key else bytes(msg)
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
