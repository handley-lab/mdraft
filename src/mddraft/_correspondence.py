"""Pure reply and forward construction from parsed mail."""

from email.utils import formataddr, getaddresses


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


def reply(
    message, source_text, sender, text, *, reply_all=False, own_addresses=()
):
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
        "from": sender,
        "to": to,
        "subject": _subject(message["Subject"], "Re: "),
        "in_reply_to": mid,
        "references": references,
    }
    if cc:
        envelope["cc"] = cc
    quoted = "\n".join("> " + line for line in source_text.rstrip("\n").split("\n"))
    attribution = f"On {message['Date']}, {message['From']} wrote:"
    return envelope, _body(text, attribution + "\n" + quoted)


def forward(message, source_text, sender, text, *, recipients=()):
    """Return ``(envelope, body)`` for an inline Mutt-shaped forward."""
    intro = f"----- Forwarded message from {message['From']} -----"
    headers = []
    for name in ("Date", "From", "To", "Cc", "Subject"):
        if message[name] is not None:
            headers.append(message.policy.fold(name, message[name]).rstrip("\n"))
    forwarded = _body(intro, "\n".join(headers), source_text)
    trailer = "----- End forwarded message -----"
    envelope = {
        "kind": "draft",
        "from": sender,
        "to": list(recipients),
        "subject": _subject(message["Subject"], "Fwd: "),
    }
    return envelope, _body(text, forwarded, trailer)
