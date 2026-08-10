"""Pure reply and forward construction from parsed mail."""

import re
from email.utils import formataddr, getaddresses
from urllib.parse import unquote

import html2text

from mddraft import _core

SAFELINK = re.compile(
    r"https?://[a-zA-Z0-9.-]*\.safelinks\.protection\.outlook\.com/\?url="
    r"((?:[^&]|%[0-9a-fA-F]{2})+)&[-a-zA-Z0-9+/&;=%.]*"
)


def _addresses(values):
    return [(addr.casefold(), formataddr((name, addr))) for name, addr in getaddresses(values)]


def _subject(subject, prefix):
    subject = str(subject or "")
    return subject if subject.casefold().startswith(prefix.casefold()) else prefix + subject


def _message_ids(header):
    if header is None:
        return []
    return [part.value.strip() for part in header._parse_tree if part.token_type == "msg-id"]


def _body(*sections):
    return "\n\n".join(section.rstrip("\n") for section in sections if section) + "\n"


def unwrap_safelinks(text):
    """Restore the real URL from Outlook's SafeLinks wrapper.

    Correspondence through Exchange arrives with every link rewritten to a
    ``*.safelinks.protection.outlook.com`` redirect carrying the target in
    ``url=`` and several hundred characters of tracking state after it. Quoting
    that back is unreadable and republishes the tokens, so the wrapper is
    unwound wherever it appears.
    """
    return SAFELINK.sub(lambda match: unquote(match.group(1)), text)


def _comparable(line):
    """Reduce a quoted line to the text it carries, however it was rendered.

    Quote markers, Markdown escaping and self-linked URLs all vary with the
    depth and rendering of a quotation while the words beneath them do not.
    """
    line = re.sub(r"^[>\s]*", "", line)
    line = re.sub(r"\[\s*([^]]+)\]\([^)]*\)", r"\1", line)
    return " ".join(line.replace("\\", "").split())


def _carries(line, footer, start):
    """Return how many consecutive footer lines from ``start`` this line carries.

    Usually one, but Outlook reflows an address block onto a single line, so a
    line may carry several — and the longest reading is the right one.
    """
    return next(
        (end - start for end in range(len(footer), start, -1) if line == " ".join(footer[start:end])),
        0,
    )


def _footer_run(lines, start, footer):
    """Return the index past a footer beginning at ``start``, else ``None``.

    Matching runs line by line rather than as one block: a rendering may
    rewrite a line — the website becomes a Markdown self-link — and an
    all-or-nothing match would then leave the whole footer in place. Two
    footer lines are required so that a lone "University of Cambridge" in a
    correspondent's own address block is not mistaken for ours.
    """
    matched = _carries(_comparable(lines[start]), footer, 0)
    if not matched:
        return None
    index = start + 1
    while index < len(lines) and matched < len(footer):
        line = _comparable(lines[index])
        if not line:
            index += 1
            continue
        carried = _carries(line, footer, matched)
        if not carried:
            break
        index, matched = index + 1, matched + carried
    return index if matched > 1 else None


def strip_own_footers(text):
    """Drop our own footers from text quoted back at us.

    A thread accumulates one copy per round: the footer the gate appended to
    our last message returns inside the correspondent's quotation, and quoting
    it again republishes it. Every occurrence goes, at any quote depth, along
    with the ``--`` delimiter introducing it. A correspondent's own signature
    is untouched — it is theirs, and quoting it is ordinary practice.
    """
    footers = [
        [_comparable(line) for line in path.read_text().split("\n") if line.strip()]
        for path in sorted(_core.FOOTERS_DIR.glob("*"))
    ]
    lines, kept, index = text.split("\n"), [], 0
    while index < len(lines):
        # Identities share their opening lines, so the longest match wins: taking
        # each footer in turn would let the shortest orphan the rest of a longer one.
        runs = [run for run in (_footer_run(lines, index, f) for f in footers) if run]
        if not runs:
            kept.append(lines[index])
            index += 1
            continue
        while kept and _comparable(kept[-1]) in ("", "--"):
            kept.pop()
        index = max(runs)
    return "\n".join(kept)


def source_text(message):
    """Return the quotable text of ``message``.

    A ``text/plain`` part is quoted verbatim. HTML-only mail — most Outlook
    correspondence — is rendered to Markdown rather than quoted as markup: a
    reply carrying a Word document's ``<!--[if !mso]>`` preamble is unreadable
    to the recipient and unreviewable in the approval surface. Links survive
    the rendering as ``[text](url)`` because they are frequently the load-
    bearing content of the quoted mail. Paragraphs are left unwrapped so the
    wire carries them as the sender wrote them.

    Two Exchange artefacts are removed either way: SafeLinks wrappers around
    the links, and our own identity footers echoed back by the quotation.
    """
    part = message.get_body(preferencelist=("plain", "html"))
    if part.get_content_type() == "text/html":
        converter = html2text.HTML2Text()
        converter.body_width = 0
        converter.ignore_images = True
        text = converter.handle(part.get_content())
    else:
        text = part.get_content()
    return strip_own_footers(unwrap_safelinks(text))


def compose(sender, recipients, subject, text, *, cc=()):
    """Return ``(envelope, body)`` for a fresh composition."""
    envelope = {
        "kind": "draft",
        "state": "draft",
        "from": sender,
        "to": list(recipients),
        "subject": subject,
    }
    if cc:
        envelope["cc"] = list(cc)
    return envelope, _body(text)


def reply(message, sender, text, *, reply_all=False, own_addresses=()):
    """Return ``(envelope, body)`` for a reply to ``message``."""
    target = message.get_all("Reply-To") or message.get_all("From", [])
    excluded = {sender.casefold(), *(address.casefold() for address in own_addresses)}
    seen = set(excluded)
    to = []
    for key, display in _addresses(target):
        if key not in seen:
            to.append(display)
            seen.add(key)
    cc = []
    if reply_all:
        for key, display in _addresses(message.get_all("To", [])):
            if key not in seen:
                to.append(display)
                seen.add(key)
        for key, display in _addresses(message.get_all("Cc", [])):
            if key not in seen:
                cc.append(display)
                seen.add(key)
    if message["Message-ID"] is None:
        raise KeyError("Message-ID")
    (mid,) = _message_ids(message["Message-ID"])
    references = _message_ids(message["References"])
    if mid not in references:
        references.append(mid)
    envelope = {
        "kind": "draft",
        "state": "draft",
        "from": sender,
        "to": to,
        "subject": _subject(message["Subject"], "Re: "),
        "in_reply_to": mid,
        "references": references,
    }
    if cc:
        envelope["cc"] = cc
    quoted = "\n".join("> " + line for line in source_text(message).rstrip("\n").split("\n"))
    attribution = f"On {message['Date']}, {message['From']} wrote:"
    return envelope, _body(text, attribution + "\n" + quoted)


def forward(message, sender, text, *, recipients=()):
    """Return ``(envelope, body)`` for an inline Mutt-shaped forward."""
    intro = f"----- Forwarded message from {message['From']} -----"
    headers = []
    for name in ("Date", "From", "To", "Cc", "Subject"):
        if message[name] is not None:
            headers.append(message.policy.fold(name, message[name]).rstrip("\n"))
    forwarded = _body(intro, "\n".join(headers), source_text(message))
    trailer = "----- End forwarded message -----"
    envelope = {
        "kind": "draft",
        "state": "draft",
        "from": sender,
        "to": list(recipients),
        "subject": _subject(message["Subject"], "Fwd: "),
    }
    return envelope, _body(text, forwarded, trailer)
