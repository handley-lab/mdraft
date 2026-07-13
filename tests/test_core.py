import subprocess
from email import message_from_bytes
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
def sign_key(tmp_path, monkeypatch):
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
    return next(
        line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr")
    )


def wire_message(log):
    """The raw bytes msmtp received: everything after the CALL line."""
    raw = log.read_bytes()
    return raw[raw.index(b"\n") + 1 :]


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
    assert bytes(mddraft.compose(card, "<m@x>")) == bytes(
        mddraft.compose(card, "<m@x>")
    )
    assert "Date" not in mddraft.compose(card, "<m@x>")


def test_flush_stamps_date_on_the_wire(deck, fake_msmtp):
    db, card_id = deck
    script, log = fake_msmtp
    mddraft.flush(db.root, card_id, db.head(), msmtp=(str(script),))
    assert "Date: " in log.read_text()


def test_compose_missing_envelope_raises_keyerror():
    with pytest.raises(KeyError):
        mddraft.compose(mddb.Card(yaml={"to": ["a@example.org"]}, body="hi"))


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


def test_compose_realname_formats_from(deck):
    db, card_id = deck
    card = db.read(card_id)
    msg = mddraft.compose(card, realname="Will Handley")
    assert msg["From"] == "Will Handley <wh260@cam.ac.uk>"
    assert mddraft.compose(card)["From"] == "wh260@cam.ac.uk"


def test_flush_realname_reaches_the_wire(deck, fake_msmtp):
    db, card_id = deck
    script, log = fake_msmtp
    mddraft.flush(
        db.root, card_id, db.head(), msmtp=(str(script),), realname="Will Handley"
    )
    parsed = message_from_bytes(wire_message(log), policy=default_policy)
    assert parsed["From"] == "Will Handley <wh260@cam.ac.uk>"


def test_flush_signed_roundtrip(deck, fake_msmtp, sign_key, tmp_path):
    db, card_id = deck
    script, log = fake_msmtp
    mddraft.flush(db.root, card_id, db.head(), msmtp=(str(script),), sign_key=sign_key)
    wire = wire_message(log)
    assert b"\n" not in wire.replace(b"\r\n", b"")
    parsed = message_from_bytes(wire, policy=default_policy)
    assert parsed.get_content_type() == "multipart/signed"
    assert parsed.get_param("micalg") == "pgp-sha256"
    assert parsed.get_param("protocol") == "application/pgp-signature"
    body_part, sig_part = parsed.iter_parts()
    canonical = body_part.get_content().replace("\r\n", "\n")
    assert canonical == "Dear Smith,\n\nI must decline.\n\nWill\n"
    boundary = ("--" + parsed.get_boundary()).encode()
    raw_body = wire.split(boundary)[1].strip(b"\r\n")
    (tmp_path / "part").write_bytes(raw_body + b"\r\n")
    (tmp_path / "part.asc").write_bytes(sig_part.get_content())
    subprocess.run(
        ["gpg", "--verify", tmp_path / "part.asc", tmp_path / "part"],
        capture_output=True,
        check=True,
    )


def test_flush_signed_utf8_body_verifies(deck, fake_msmtp, sign_key, tmp_path):
    db, card_id = deck
    card = db.read(card_id)
    card.body = "Chère Smith,\n\nJe décline — désolé.\n\nWill\n"
    with db.editor(rationale="utf-8 body for the signed path") as editor:
        editor.update(card, summary=card.summary)
    script, log = fake_msmtp
    mddraft.flush(db.root, card_id, db.head(), msmtp=(str(script),), sign_key=sign_key)
    wire = wire_message(log)
    parsed = message_from_bytes(wire, policy=default_policy)
    body_part, sig_part = parsed.iter_parts()
    assert "décline" in body_part.get_content()
    boundary = ("--" + parsed.get_boundary()).encode()
    raw_body = wire.split(boundary)[1].strip(b"\r\n")
    (tmp_path / "part").write_bytes(raw_body + b"\r\n")
    (tmp_path / "part.asc").write_bytes(sig_part.get_content())
    subprocess.run(
        ["gpg", "--verify", tmp_path / "part.asc", tmp_path / "part"],
        capture_output=True,
        check=True,
    )


def test_flush_gpg_failure_sends_and_commits_nothing(deck, fake_msmtp, sign_key):
    db, card_id = deck
    script, log = fake_msmtp
    sha = db.head()
    with pytest.raises(subprocess.CalledProcessError):
        mddraft.flush(db.root, card_id, sha, msmtp=(str(script),), sign_key="NOSUCHKEY")
    assert not log.exists()
    fresh = mddb.MDDB(db.root).read(card_id)
    assert fresh.yaml["state"] == "draft"
    assert "sent_mid" not in fresh.yaml
    assert mddb.MDDB(db.root).head() == sha


def test_flush_unsigned_path_is_flat_text(deck, fake_msmtp):
    db, card_id = deck
    script, log = fake_msmtp
    mddraft.flush(db.root, card_id, db.head(), msmtp=(str(script),))
    parsed = message_from_bytes(wire_message(log), policy=default_policy)
    assert parsed.get_content_type() == "text/plain"
    assert parsed["From"] == "wh260@cam.ac.uk"
    assert parsed.get_content() == "Dear Smith,\n\nI must decline.\n\nWill\n"


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
