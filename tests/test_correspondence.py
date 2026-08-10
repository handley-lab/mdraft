from email import policy
from email.parser import BytesParser
from pathlib import Path

import mddraft
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def message(headers, body=""):
    """Parse from bytes, as the production path does -- charsets must round-trip."""
    return BytesParser(policy=policy.default).parsebytes((headers + "\n\n" + body).encode())


def test_reply_matches_mutt_shape_and_threading():
    source = message(
        "Date: Fri, 13 Mar 2026 11:41:34 +0000\n"
        'From: "Senior Bursar (Sarah Tebbutt)" <senior.bursar@cai.cam.ac.uk>\n'
        "To: Will Handley <wh260@cam.ac.uk>\n"
        "Subject: RE: Paternity leave\n"
        "Message-ID: <answer@outlook.example>\n"
        "References: <root@boltzmann> <question@outlook.example>",
        "Hi Will\nI have approved this.",
    )
    envelope, body = mddraft.reply(
        source,
        "wh260@cam.ac.uk",
        "Hi Sarah,\n\nThank you.",
    )
    assert envelope == {
        "kind": "draft",
        "state": "draft",
        "from": "wh260@cam.ac.uk",
        "to": ['"Senior Bursar (Sarah Tebbutt)" <senior.bursar@cai.cam.ac.uk>'],
        "subject": "RE: Paternity leave",
        "in_reply_to": "<answer@outlook.example>",
        "references": [
            "<root@boltzmann>",
            "<question@outlook.example>",
            "<answer@outlook.example>",
        ],
    }
    assert body == (FIXTURES / "reply.txt").read_text()


def test_reply_all_deduplicates_and_excludes_own_addresses():
    source = message(
        "Date: Tue, 21 Jul 2026 10:00:00 +0100\n"
        "From: Alice <ALICE@example.org>\n"
        "Reply-To: Team <team@example.org>, Alice <alice@example.org>\n"
        "To: Will <wh260@cam.ac.uk>, Gmail Me <williamjameshandley@gmail.com>, "
        "Team Duplicate <TEAM@example.org>, Bob <b@example.org>\n"
        "Cc: Bob Again <B@example.org>, Other Me <will@example.net>, Carol <c@example.org>\n"
        "Subject: Topic\nMessage-ID: <topic@example.org>",
        "source",
    )
    envelope, _ = mddraft.reply(
        source,
        "wh260@cam.ac.uk",
        "answer",
        reply_all=True,
        own_addresses=("will@example.net", "williamjameshandley@gmail.com"),
    )
    assert envelope["to"] == [
        "Team <team@example.org>",
        "Alice <alice@example.org>",
        "Bob <b@example.org>",
    ]
    assert envelope["cc"] == ["Carol <c@example.org>"]


def test_reply_requires_message_id():
    source = message(
        "Date: Tue, 21 Jul 2026 10:00:00 +0100\n"
        "From: Alice <alice@example.org>\nSubject: Topic"
    )
    with pytest.raises(KeyError, match="Message-ID"):
        mddraft.reply(source, "wh260@cam.ac.uk", "answer")


def test_reply_without_references_starts_thread_and_preserves_blank_lines():
    source = message(
        "Date: Tue, 21 Jul 2026 10:00:00 +0100\n"
        "From: José <jose@example.org>\nSubject: Olá\nMessage-ID: <unicode@example.org>\n"
        "MIME-Version: 1.0\n"
        'Content-Type: text/plain; charset="utf-8"',
        "um\n\ntrês",
    )
    envelope, body = mddraft.reply(source, "wh260@cam.ac.uk", "Olá — obrigado")
    assert envelope["references"] == ["<unicode@example.org>"]
    assert "> um\n> \n> três" in body


def test_folded_references_are_parsed_in_order():
    source = message(
        "Date: Tue, 21 Jul 2026 10:00:00 +0100\nFrom: a@example.org\n"
        "Subject: Topic\nMessage-ID: <three@example.org>\n"
        "References: <one@example.org>\n <two@example.org>",
        "source",
    )
    envelope, _ = mddraft.reply(source, "me@example.org", "answer")
    assert envelope["references"] == [
        "<one@example.org>",
        "<two@example.org>",
        "<three@example.org>",
    ]


def test_message_id_comments_do_not_enter_thread_headers():
    source = message(
        "Date: Tue, 21 Jul 2026 10:00:00 +0100\nFrom: a@example.org\n"
        "Subject: Topic\nMessage-ID: (before) <two@example.org> (after)\n"
        "References: (root) <one@example.org>",
        "source",
    )
    envelope, _ = mddraft.reply(source, "me@example.org", "answer")
    assert envelope["in_reply_to"] == "<two@example.org>"
    assert envelope["references"] == ["<one@example.org>", "<two@example.org>"]


def test_forward_matches_mutt_inline_shape_and_has_no_threading():
    source = message(
        "Date: Mon, 1 Jun 2026 12:00:00 +0100\n"
        "From: Michelle Fell <mf762@cam.ac.uk>\n"
        "To: Will Handley <wh260@cam.ac.uk>\n"
        "Cc: Vasily <v@example.org>\n"
        "Subject: Project descriptions\n"
        "Message-ID: <projects@example.org>",
        "Please submit your project description.",
    )
    envelope, body = mddraft.forward(
        source,
        "wh260@cam.ac.uk",
        "For your information.",
        recipients=("colleague@example.org",),
    )
    assert envelope == {
        "kind": "draft",
        "state": "draft",
        "from": "wh260@cam.ac.uk",
        "to": ["colleague@example.org"],
        "subject": "Fwd: Project descriptions",
    }
    assert body == (FIXTURES / "forward.txt").read_text()


def test_prefixes_are_not_duplicated_case_insensitively():
    reply_source = message(
        "Date: Tue, 21 Jul 2026 10:00:00 +0100\nFrom: a@example.org\n"
        "Subject: re: topic\nMessage-ID: <x@example.org>",
        "x",
    )
    forward_source = message(
        "Date: Tue, 21 Jul 2026 10:00:00 +0100\nFrom: a@example.org\n"
        "Subject: FWD: topic\nMessage-ID: <x@example.org>",
        "x",
    )
    assert mddraft.reply(reply_source, "me@example.org", "y")[0]["subject"] == "re: topic"
    assert mddraft.forward(forward_source, "me@example.org", "y")[0]["subject"] == "FWD: topic"


def test_compose_stamps_complete_envelope():
    envelope, body = mddraft.compose(
        "wh260@cam.ac.uk",
        ["a@example.org"],
        "Group computing receipts -- Codex chase",
        "Hi A,\n\nCould you forward the missing receipts?\n\nBest,\nWill",
    )
    assert envelope == {
        "kind": "draft",
        "state": "draft",
        "from": "wh260@cam.ac.uk",
        "to": ["a@example.org"],
        "subject": "Group computing receipts -- Codex chase",
    }
    assert body.endswith("Best,\nWill\n")


def test_html_only_mail_is_quoted_as_markdown_not_markup():
    source = message(
        "Date: Tue, 04 Aug 2026 15:35:50 +0100\n"
        "From: Daria Frank <dt346@cam.ac.uk>\n"
        "Subject: CATAM project setting\n"
        "Message-ID: <catam@outlook.example>\n"
        "MIME-Version: 1.0\n"
        'Content-Type: text/html; charset="utf-8"',
        '<html xmlns:o="urn:schemas-microsoft-com:office:office"><head>'
        "<!--[if !mso]><style>v\\:* {behavior:url(#default#VML);}</style><![endif]-->"
        "</head><body><p>The guidelines are "
        '<a href="https://example.org/guidelines.pdf">on SharePoint</a>.</p></body></html>',
    )
    _, body = mddraft.reply(source, "wh260@cam.ac.uk", "Noted.")
    assert "mso" not in body
    assert "<html" not in body
    assert "> The guidelines are [on SharePoint](https://example.org/guidelines.pdf)." in body


def test_plain_part_is_preferred_over_html_alternative():
    source = message(
        "Date: Tue, 04 Aug 2026 15:35:50 +0100\n"
        "From: A <a@example.org>\n"
        "Subject: Topic\n"
        "Message-ID: <alt@example.org>\n"
        "MIME-Version: 1.0\n"
        'Content-Type: multipart/alternative; boundary="b"',
        "--b\nContent-Type: text/plain\n\nthe plain part\n"
        "--b\nContent-Type: text/html\n\n<p>the html part</p>\n--b--",
    )
    assert "> the plain part" in mddraft.reply(source, "me@example.org", "y")[1]


def test_safelinks_wrapper_is_unwound_to_the_real_url():
    source = message(
        "Date: Fri, 07 Aug 2026 15:36:31 +0100\n"
        "From: IoA human resources <hr@ast.cam.ac.uk>\n"
        "Subject: Bursary application\n"
        "Message-ID: <bursary@outlook.example>",
        "The form is here: https://eur03.safelinks.protection.outlook.com/?url="
        "https%3A%2F%2Fexample.sharepoint.com%2Fbursary.pdf&data=05%7C02%7Cwh260"
        "%40cam.ac.uk%7C55d9d485a54d415f&sdata=bnRBb0JrUWM5NlREWitUUEhx",
    )
    _, body = mddraft.reply(source, "wh260@cam.ac.uk", "Thanks.")
    assert "> The form is here: https://example.sharepoint.com/bursary.pdf" in body
    assert "safelinks" not in body


def test_our_own_footer_is_stripped_at_every_occurrence(monkeypatch, tmp_path):
    footers = tmp_path / "footers"
    footers.mkdir()
    (footers / "wh260@cam.ac.uk").write_text(
        "\nDr Will Handley\nRoyal Society University Research Fellow\n"
        "Institute of Astronomy\nUniversity of Cambridge\n"
    )
    monkeypatch.setattr(mddraft._core, "FOOTERS_DIR", footers)
    source = message(
        "Date: Tue, 21 Jul 2026 15:49:23 +0100\n"
        "From: William Royce <wr286@cam.ac.uk>\n"
        "Subject: Re: The post\n"
        "Message-ID: <royce@outlook.example>",
        "Thank you for coming back to me.\n"
        "\n"
        "-- \n"
        "\n"
        "Dr Will Handley\n"
        "Royal Society University Research Fellow\n"
        "Institute of Astronomy\n"
        "University of Cambridge\n"
        "\n"
        "> Best wishes,\n"
        "> \n"
        "> William\n"
        "> \n"
        "> \\--  \n"
        ">   \n"
        "> Dr Will Handley  \n"
        "> Royal Society University Research Fellow  \n"
        "> Institute of Astronomy  \n"
        "> University of Cambridge  \n",
    )
    _, body = mddraft.reply(source, "wh260@cam.ac.uk", "Noted.")
    assert "Royal Society University Research Fellow" not in body
    assert "> Thank you for coming back to me." in body
    assert "> > William" in body


def test_a_correspondents_own_signature_is_left_alone(monkeypatch, tmp_path):
    footers = tmp_path / "footers"
    footers.mkdir()
    (footers / "wh260@cam.ac.uk").write_text("\nDr Will Handley\nInstitute of Astronomy\n")
    monkeypatch.setattr(mddraft._core, "FOOTERS_DIR", footers)
    source = message(
        "Date: Fri, 07 Aug 2026 12:31:24 +0100\n"
        "From: Helen Thirkettle <hr@ast.cam.ac.uk>\n"
        "Subject: Visa costs\n"
        "Message-ID: <helen@outlook.example>",
        "Is there any leeway here?\n\n-- \n\nHelen Thirkettle\nSenior HR Coordinator\n",
    )
    _, body = mddraft.reply(source, "wh260@cam.ac.uk", "Yes.")
    assert "> Helen Thirkettle" in body
    assert "> Senior HR Coordinator" in body


def test_the_longest_matching_identity_footer_wins(monkeypatch, tmp_path):
    """Identities share opening lines; the shortest must not orphan the rest."""
    footers = tmp_path / "footers"
    footers.mkdir()
    (footers / "a@example.org").write_text("\nDr Will Handley\nInstitute of Astronomy\n")
    (footers / "wh260@cam.ac.uk").write_text(
        "\nDr Will Handley\nInstitute of Astronomy\nPhone: +44-(0)7718-622713\n"
        "Website: https://www.handley-lab.co.uk\n"
    )
    monkeypatch.setattr(mddraft._core, "FOOTERS_DIR", footers)
    source = message(
        "Date: Tue, 21 Jul 2026 15:49:23 +0100\n"
        "From: A <a@example.org>\n"
        "Subject: Re: Topic\n"
        "Message-ID: <shared@example.org>",
        "Noted, thanks.\n"
        "\n"
        "> -- \n"
        "> \n"
        "> Dr Will Handley  \n"
        "> Institute of Astronomy  \n"
        "> Phone: +44-(0)7718-622713  \n"
        "> Website: [ https://www.handley-lab.co.uk](https://www.handley-lab.co.uk/)  \n",
    )
    _, body = mddraft.reply(source, "wh260@cam.ac.uk", "Right.")
    assert "handley-lab.co.uk" not in body
    assert "Phone" not in body
    assert "> Noted, thanks." in body


def test_a_footer_reflowed_onto_one_line_is_still_ours(monkeypatch, tmp_path):
    """Outlook flattens the address block; the words are the same footer."""
    footers = tmp_path / "footers"
    footers.mkdir()
    (footers / "wh260@cam.ac.uk").write_text(
        "\nDr Will Handley\nRoyal Society University Research Fellow\n"
        "Institute of Astronomy\nUniversity of Cambridge\n"
    )
    monkeypatch.setattr(mddraft._core, "FOOTERS_DIR", footers)
    source = message(
        "Date: Mon, 22 Jun 2026 12:38:00 +0100\n"
        "From: IoA human resources <hr@ast.cam.ac.uk>\n"
        "Subject: RE: Contract\n"
        "Message-ID: <reflow@outlook.example>",
        "Thanks for confirming.\n"
        "\n"
        "> > Dr Will Handley Royal Society University Research Fellow\n"
        "> > Institute of Astronomy University of Cambridge\n",
    )
    _, body = mddraft.reply(source, "wh260@cam.ac.uk", "Noted.")
    assert "Royal Society" not in body
    assert "> Thanks for confirming." in body
