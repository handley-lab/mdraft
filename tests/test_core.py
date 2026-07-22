import subprocess
from email.message import EmailMessage
from email.policy import SMTP

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


def test_flush_nonzero_msmtp_commits_nothing(deck, fake_msmtp, tmp_path):
    db, card_id = deck
    script, log = fake_msmtp
    (tmp_path / "msmtp-fail").touch()
    sha = db.head()
    with pytest.raises(subprocess.CalledProcessError):
        mddraft.flush(db.root, card_id, sha, msmtp=(str(script),))
    fresh = mddb.MDDB(db.root).read(card_id)
    assert fresh.yaml["state"] == "draft"
    assert "sent_mid" not in fresh.yaml
    assert mddb.MDDB(db.root).head() == sha


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
