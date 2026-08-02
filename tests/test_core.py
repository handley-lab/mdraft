import subprocess
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import SMTP
from email.policy import default as default_policy

import mddb
import pytest

import mddraft


@pytest.fixture
def deck(tmp_path):
    db = mddb.MDDB.init(tmp_path / "outbox")
    with db.editor(rationale="draft: reply to smith") as editor:
        card = editor.create(
            title="Re: referee report",
            summary="decline politely",
            yaml={
                "to": ["smith@example.org"],
                "cc": ["jones@example.org"],
                "from": "wh260@cam.ac.uk",
                "subject": "Re: referee report",
                "in_reply_to": "<orig@example.org>",
                "references": ["<root@example.org>", "<orig@example.org>"],
                "kind": "draft",
                "state": "draft",
            },
            body="Dear Smith,\n\nI must decline.\n\nWill\n",
        )
    return db, card.id


@pytest.fixture
def fake_msmtp(tmp_path):
    log = tmp_path / "msmtp.log"
    script = tmp_path / "fake-msmtp"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "CALL %s\\n" "$*" >> {log}\n'
        f"cat >> {log}\n"
        f"[ -f {tmp_path}/msmtp-fail ] && exit 1\n"
        "exit 0\n"
    )
    script.chmod(0o755)
    return script, log


@pytest.fixture
def signer(tmp_path, monkeypatch):
    home = tmp_path / "gnupg"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("GNUPGHOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "commit.gpgsign")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "",
            "--quick-gen-key",
            "gate test <gate@test.invalid>",
            "ed25519",
            "sign",
        ],
        capture_output=True,
        check=True,
    )
    listing = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--list-secret-keys"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    key = next(
        line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr")
    )
    return (
        "gpg",
        "--batch",
        "--armor",
        "--detach-sign",
        "--digest-algo",
        "SHA512",
        "--local-user",
        f"{key}!",
    )


def wire_message(log):
    raw = log.read_bytes()
    return raw[raw.index(b"\n") + 1 :]


def verify_signed(wire, tmp_path):
    parsed = message_from_bytes(wire, policy=default_policy)
    body, signature = parsed.iter_parts()
    boundary = ("--" + parsed.get_boundary()).encode()
    raw_body = wire.split(boundary + b"\r\n", 1)[1].split(boundary, 1)[0][:-2]
    (tmp_path / "signed-part").write_bytes(raw_body)
    (tmp_path / "signature.asc").write_bytes(signature.get_content())
    subprocess.run(
        ["gpg", "--verify", tmp_path / "signature.asc", tmp_path / "signed-part"],
        capture_output=True,
        check=True,
    )
    return body


def test_compose_full_envelope(deck):
    db, card_id = deck
    msg = mddraft.compose(db.read(card_id), mid="<mid@cam.ac.uk>")
    assert msg["From"] == "wh260@cam.ac.uk"
    assert msg["To"] == "smith@example.org"
    assert msg["Cc"] == "jones@example.org"
    assert msg["Subject"] == "Re: referee report"
    assert msg["Message-ID"] == "<mid@cam.ac.uk>"
    assert msg["In-Reply-To"] == "<orig@example.org>"
    assert msg["References"] == "<root@example.org> <orig@example.org>"
    assert msg.get_content() == "Dear Smith,\n\nI must decline.\n\nWill\n"


def test_compose_keeps_paragraphs_unwrapped_on_the_wire(tmp_path):
    body = (
        "word " * 300 + "one unwrapped paragraph\n"
        "\n"
        "-- \n"
        "Will\n"
    )
    card = mddb.Card(
        yaml={"to": ["a@example.org"], "from": "wh260@cam.ac.uk", "subject": "wire"},
        body=body,
    )
    msg = mddraft.compose(card)
    assert msg["Content-Transfer-Encoding"] == "quoted-printable"
    assert msg.get_content() == body
    assert max(len(line) for line in bytes(msg).splitlines()) <= 998


def test_compose_fresh_mail_omits_threading_and_cc(tmp_path):
    card = mddb.Card(
        yaml={
            "to": ["a@example.org"],
            "from": "wh260@cam.ac.uk",
            "subject": "hello",
        },
        body="hi\n",
    )
    msg = mddraft.compose(card)
    assert "Cc" not in msg
    assert "In-Reply-To" not in msg
    assert "References" not in msg
    assert "Message-ID" not in msg


def test_compose_is_deterministic(deck):
    db, card_id = deck
    card = db.read(card_id)
    assert bytes(mddraft.compose(card, "<m@x>")) == bytes(mddraft.compose(card, "<m@x>"))
    assert "Date" not in mddraft.compose(card, "<m@x>")


def test_flush_stamps_date_on_the_wire(deck, fake_msmtp):
    db, card_id = deck
    script, log = fake_msmtp
    mddraft.flush(db.root, card_id, db.head(), msmtp=(str(script),))
    assert "Date: " in log.read_text()


def test_signed_plain_body_verifies_and_keeps_envelope_outside_signature(
    deck, fake_msmtp, signer, tmp_path
):
    db, card_id = deck
    script, log = fake_msmtp
    mddraft.flush(
        db.root,
        card_id,
        db.head(),
        msmtp=(str(script),),
        realname="Will Handley",
        signer=signer,
    )
    wire = wire_message(log)
    parsed = message_from_bytes(wire, policy=default_policy)
    assert parsed["From"] == "Will Handley <wh260@cam.ac.uk>"
    assert parsed.get_content_type() == "multipart/signed"
    body = verify_signed(wire, tmp_path)
    assert body.get_content_type() == "text/plain"
    assert body.get_content().replace("\r\n", "\n") == (
        "Dear Smith,\n\nI must decline.\n\nWill\n"
    )
    assert "From" not in body


def test_signed_attachment_body_verifies_and_retains_exact_file(
    tmp_path, fake_msmtp, signer
):
    db, card_id = attachment_deck(
        tmp_path,
        [
            (
                {
                    "filename": "result.dat",
                    "content_type": "application/octet-stream",
                    "representation": "payload",
                },
                b"attachment bytes",
            )
        ],
    )
    script, log = fake_msmtp
    mddraft.flush(
        db.root, card_id, db.head(), msmtp=(str(script),), signer=signer
    )
    body = verify_signed(wire_message(log), tmp_path)
    attachment = next(body.iter_attachments())
    assert attachment.get_filename() == "result.dat"
    assert attachment.get_payload(decode=True) == b"attachment bytes"


def test_signed_utf8_message_and_entity_attachments_verify(
    tmp_path, fake_msmtp, signer
):
    embedded = EmailMessage(policy=SMTP)
    embedded["From"] = "josé@example.org"
    embedded.set_content("pièce jointe\n")
    entity = EmailMessage(policy=SMTP)
    entity.make_related()
    html = EmailMessage(policy=SMTP)
    html.set_content("<p>déjà vu</p>", subtype="html")
    entity.attach(html)
    db, card_id = attachment_deck(
        tmp_path,
        [
            (
                {
                    "filename": "forwarded.eml",
                    "content_type": "message/rfc822",
                    "representation": "message",
                },
                embedded.as_bytes(policy=SMTP),
            ),
            (
                {
                    "content_type": "multipart/related",
                    "representation": "entity",
                },
                entity.as_bytes(policy=SMTP),
            ),
        ],
    )
    card = db.read(card_id)
    card.body = "Chère collègue — merci.\n"
    with db.editor(rationale="utf-8 signed body") as editor:
        editor.update(card, summary=card.summary)
    script, log = fake_msmtp
    mddraft.flush(
        db.root, card_id, db.head(), msmtp=(str(script),), signer=signer
    )
    body = verify_signed(wire_message(log), tmp_path)
    assert body.get_body().get_content().replace("\r\n", "\n") == (
        "Chère collègue — merci.\n"
    )
    message_part, entity_part = body.iter_attachments()
    assert message_part.get_payload(0)["From"] == "josé@example.org"
    assert entity_part.get_content_type() == "multipart/related"


def test_gpg_failure_sends_and_commits_nothing(deck, fake_msmtp):
    db, card_id = deck
    script, log = fake_msmtp
    sha = db.head()
    with pytest.raises(subprocess.CalledProcessError):
        mddraft.flush(
            db.root,
            card_id,
            sha,
            msmtp=(str(script),),
            signer=("false",),
        )
    assert not log.exists()
    assert mddb.MDDB(db.root).head() == sha


def test_compose_missing_envelope_raises_keyerror():
    with pytest.raises(KeyError):
        mddraft.compose(mddb.Card(yaml={"to": ["a@example.org"]}, body="hi"))


def test_scalar_references_fail_before_msmtp(tmp_path, fake_msmtp):
    db = mddb.MDDB.init(tmp_path / "scalar-references")
    with db.editor(rationale="malformed legacy draft") as editor:
        card = editor.create(
            title="bad references",
            summary="must not send",
            yaml={
                "kind": "draft",
                "to": ["a@example.org"],
                "from": "me@example.org",
                "subject": "bad references",
                "references": "<root@example.org>",
            },
            body="body\n",
        )
    script, log = fake_msmtp
    with pytest.raises(TypeError, match="references must be a list"):
        mddraft.flush(db.root, card.id, db.head(), msmtp=(str(script),))
    assert not log.exists()


def attachment_deck(tmp_path, attachment_rows):
    db = mddb.MDDB.init(tmp_path / "attachments")
    with db.editor(rationale="draft with attachments") as editor:
        ids = []
        for number, (yaml, data) in enumerate(attachment_rows):
            attachment = editor.create(
                title=yaml.get("filename") or f"attachment {number}",
                summary=yaml["content_type"],
                yaml={"kind": "attachment", **yaml},
                relpath=f"attachments/{number}.md",
                blob=data,
                blob_ext=".bin",
            )
            ids.append(attachment.id)
        draft = editor.create(
            title="message with files",
            summary="attachment test",
            yaml={
                "kind": "draft",
                "to": ["a@example.org"],
                "from": "me@example.org",
                "subject": "files",
                "attachments": ids,
            },
            body="See attached.\n",
        )
    return db, draft.id


def test_payload_attachment_roundtrips_from_pinned_commit(tmp_path):
    db, card_id = attachment_deck(
        tmp_path,
        [
            (
                {
                    "filename": "archive.tar.gz",
                    "content_type": "application/octet-stream",
                    "representation": "payload",
                },
                b"original bytes",
            )
        ],
    )
    sha = db.head()
    card = mddraft.at(db.root, card_id, sha)
    selected = mddraft.attachments(db.root, card, sha)
    attachment = db.read(card.yaml["attachments"][0])
    attachment.blob.write_bytes(b"changed after approval")
    part = next(mddraft.compose(card, attachment_data=selected).iter_attachments())
    assert part.get_filename() == "archive.tar.gz"
    assert part.get_content_type() == "application/octet-stream"
    assert part.get_payload(decode=True) == b"original bytes"


def test_message_and_multipart_entity_attachments_roundtrip(tmp_path):
    embedded = EmailMessage(policy=SMTP)
    embedded["From"] = "source@example.org"
    embedded["To"] = "target@example.org"
    embedded.set_content("embedded body\n")
    entity = EmailMessage(policy=SMTP)
    entity.make_related()
    html = EmailMessage(policy=SMTP)
    html.set_content("<p>body</p>", subtype="html")
    image = EmailMessage(policy=SMTP)
    image.set_content(b"png", maintype="image", subtype="png", disposition="inline")
    entity.attach(html)
    entity.attach(image)
    db, card_id = attachment_deck(
        tmp_path,
        [
            (
                {
                    "filename": "forwarded.eml",
                    "content_type": "message/rfc822",
                    "representation": "message",
                },
                bytes(embedded),
            ),
            (
                {
                    "content_type": "multipart/related",
                    "representation": "entity",
                },
                bytes(entity),
            ),
        ],
    )
    card = mddraft.at(db.root, card_id, db.head())
    msg = mddraft.compose(
        card, attachment_data=mddraft.attachments(db.root, card, db.head())
    )
    message_part, entity_part = msg.iter_attachments()
    assert message_part.get_content_type() == "message/rfc822"
    assert message_part.get_filename() == "forwarded.eml"
    assert message_part.get_payload(0)["From"] == "source@example.org"
    assert entity_part.get_content_type() == "multipart/related"
    assert [part.get_content_type() for part in entity_part.iter_parts()] == [
        "text/html",
        "image/png",
    ]


def test_at_reads_sha_pinned_bytes(deck):
    db, card_id = deck
    sha = db.head()
    card = db.read(card_id)
    card.body = "EDITED AFTER APPROVAL\n"
    with db.editor(rationale="edit after the approved sha") as editor:
        editor.update(card, summary=card.summary)
    pinned = mddraft.at(db.root, card_id, sha)
    assert pinned.body == "Dear Smith,\n\nI must decline.\n\nWill\n"


def test_flush_sends_and_stamps(deck, fake_msmtp):
    db, card_id = deck
    script, log = fake_msmtp
    sha = db.head()
    mid = mddraft.flush(db.root, card_id, sha, msmtp=(str(script),))
    wire = log.read_text()
    assert "CALL -a wh260@cam.ac.uk -t" in wire
    assert "Dear Smith," in wire
    assert mid in wire
    stamped = mddb.MDDB(db.root).read(card_id)
    assert stamped.yaml["state"] == "sent"
    assert stamped.yaml["sent_mid"] == mid
    assert stamped.yaml["sent_sha"] == sha
    assert "sent_at" in stamped.yaml


def test_flush_sends_the_approved_sha_not_head(deck, fake_msmtp):
    db, card_id = deck
    script, log = fake_msmtp
    sha = db.head()
    card = db.read(card_id)
    card.body = "SNEAKY POST-APPROVAL EDIT\n"
    with db.editor(rationale="edit landing between read and press") as editor:
        editor.update(card, summary=card.summary)
    mddraft.flush(db.root, card_id, sha, msmtp=(str(script),))
    wire = log.read_text()
    assert "Dear Smith," in wire
    assert "SNEAKY" not in wire


def test_flush_nonzero_msmtp_remains_ambiguous(deck, fake_msmtp, tmp_path):
    db, card_id = deck
    script, log = fake_msmtp
    (tmp_path / "msmtp-fail").touch()
    sha = db.head()
    with pytest.raises(subprocess.CalledProcessError):
        mddraft.flush(db.root, card_id, sha, msmtp=(str(script),))
    fresh = mddb.MDDB(db.root).read(card_id)
    assert fresh.yaml["send_state"] == "ambiguous"
    assert "sent_mid" not in fresh.yaml
    assert fresh.yaml["approved_sha"] == sha
    with pytest.raises(mddraft.AmbiguousSend):
        mddraft.flush(db.root, card_id, mddb.MDDB(db.root).head(), msmtp=(str(script),))
    assert log.read_text().count("CALL") == 1


def test_death_before_transport_is_non_resendable(deck, fake_msmtp):
    db, card_id = deck
    script, log = fake_msmtp

    def die(_payload):
        raise RuntimeError("process death before transport")

    with pytest.raises(RuntimeError, match="before transport"):
        mddraft.flush(
            db.root,
            card_id,
            db.head(),
            msmtp=(str(script),),
            before_transport=die,
        )
    assert not log.exists()
    fresh = mddb.MDDB(db.root).read(card_id)
    assert fresh.yaml["send_state"] == "ambiguous"
    with pytest.raises(mddraft.AmbiguousSend):
        mddraft.flush(db.root, card_id, mddb.MDDB(db.root).head(), msmtp=(str(script),))
    assert not log.exists()


def test_death_after_transport_reconciles_exact_wire_without_resend(
    deck, fake_msmtp
):
    db, card_id = deck
    script, log = fake_msmtp

    def die(_payload):
        raise RuntimeError("process death after transport")

    with pytest.raises(RuntimeError, match="after transport"):
        mddraft.flush(
            db.root,
            card_id,
            db.head(),
            msmtp=(str(script),),
            after_transport=die,
        )
    assert log.read_text().count("CALL") == 1
    with pytest.raises(mddraft.AmbiguousSend):
        mddraft.flush(db.root, card_id, mddb.MDDB(db.root).head(), msmtp=(str(script),))
    stored = b"X-Mailstore: added-after-send\r\n" + wire_message(log)
    mid = mddraft.reconcile(db.root, card_id, [stored])
    fresh = mddb.MDDB(db.root).read(card_id)
    assert fresh.yaml["sent_mid"] == mid
    assert fresh.yaml["send_state"] == "sent"
    assert log.read_text().count("CALL") == 1


def test_reconcile_rejects_same_mid_with_altered_wire(deck, fake_msmtp):
    db, card_id = deck
    script, log = fake_msmtp

    with pytest.raises(RuntimeError):
        mddraft.flush(
            db.root,
            card_id,
            db.head(),
            msmtp=(str(script),),
            after_transport=lambda _payload: (_ for _ in ()).throw(RuntimeError()),
        )
    altered = wire_message(log).replace(b"Dear Smith", b"Dear Smythe")
    with pytest.raises(mddraft.AmbiguousSend, match="1 observations and 0 matches"):
        mddraft.reconcile(db.root, card_id, [altered])


def test_reconcile_rejects_duplicate_exact_observations(deck, fake_msmtp):
    db, card_id = deck
    script, log = fake_msmtp
    with pytest.raises(RuntimeError):
        mddraft.flush(
            db.root,
            card_id,
            db.head(),
            msmtp=(str(script),),
            after_transport=lambda _payload: (_ for _ in ()).throw(RuntimeError()),
        )
    raw = wire_message(log)
    with pytest.raises(mddraft.AmbiguousSend, match="2 observations and 2 matches"):
        mddraft.reconcile(db.root, card_id, [raw, raw])


def test_reconcile_rejects_one_match_plus_one_mismatch(deck, fake_msmtp):
    db, card_id = deck
    script, log = fake_msmtp
    with pytest.raises(RuntimeError):
        mddraft.flush(
            db.root,
            card_id,
            db.head(),
            msmtp=(str(script),),
            after_transport=lambda _payload: (_ for _ in ()).throw(RuntimeError()),
        )
    raw = wire_message(log)
    altered = raw.replace(b"Dear Smith", b"Dear Smythe")
    with pytest.raises(mddraft.AmbiguousSend, match="2 observations and 1 matches"):
        mddraft.reconcile(db.root, card_id, [raw, altered])


def test_flush_refuses_already_sent(deck, fake_msmtp):
    db, card_id = deck
    script, log = fake_msmtp
    sha = db.head()
    mid = mddraft.flush(db.root, card_id, sha, msmtp=(str(script),))
    with pytest.raises(mddraft.AlreadySent, match=mid[1:-1]):
        mddraft.flush(db.root, card_id, db.head(), msmtp=(str(script),))
    assert log.read_text().count("CALL") == 1


def test_flush_stamp_conflict_retries_without_resending(deck, fake_msmtp, monkeypatch):
    db, card_id = deck
    script, log = fake_msmtp
    sha = db.head()
    real_editor = mddb.MDDB.editor
    fails = iter([True, True, True, True])

    def editor_conflicting(self, **kwargs):
        if next(fails, False):
            raise mddb.ConflictError("concurrent commit landed first")
        return real_editor(self, **kwargs)

    monkeypatch.setattr(mddb.MDDB, "editor", editor_conflicting)
    mid = mddraft.flush(db.root, card_id, sha, msmtp=(str(script),))
    assert log.read_text().count("CALL") == 1
    stamped = mddb.MDDB(db.root).read(card_id)
    assert stamped.yaml["sent_mid"] == mid


def test_flush_refuses_a_card_of_another_kind(tmp_path, fake_msmtp):
    """An addressable-looking card that mddraft does not own is never sent."""
    db = mddb.MDDB.init(tmp_path / "foreign")
    with db.editor(rationale="a task that happens to carry addresses") as editor:
        card = editor.create(
            title="Email Smith about the report",
            summary="looks addressable, is a task",
            kind="task",
            yaml={
                "status": "next",
                "to": ["smith@example.org"],
                "from": "wh260@cam.ac.uk",
                "subject": "report",
            },
            body="body\n",
        )
    script, log = fake_msmtp
    with pytest.raises(ValueError, match="is not a draft"):
        mddraft.flush(db.root, card.id, db.head(), msmtp=(str(script),))
    assert not log.exists()


def test_flush_refuses_a_kindless_card(tmp_path, fake_msmtp):
    db = mddb.MDDB.init(tmp_path / "kindless")
    with db.editor(rationale="a card no layer owns") as editor:
        card = editor.create(
            title="orphan",
            summary="no kind",
            yaml={
                "to": ["a@example.org"],
                "from": "me@example.org",
                "subject": "orphan",
            },
            body="body\n",
        )
    script, log = fake_msmtp
    with pytest.raises(ValueError, match="kind None is not a draft"):
        mddraft.flush(db.root, card.id, db.head(), msmtp=(str(script),))
    assert not log.exists()
