# mddraft

Correct, minimal documentation is best. Omission is preferable to an
unsupported or obsolete claim. Incorrect documentation is worst.

Email drafting and gated sending over the [mddb](https://github.com/handley-lab/mddb)
card substrate — the outbound half of bringing email into an agentic system.

An agent and a human compose an email together: the draft is an mddb card in a
git-backed deck, every edit a commit, every steer recorded. The agent can write
drafts; it structurally **cannot send**. The only thing that flushes an approved
draft to the wire (msmtp underneath) is the owner's explicit act, taken after
reading the verbatim outgoing text. Sending without that act is a never-event
by construction, not by prompt.

The same deck is the learning corpus: the proposal→final diff and the steers
that produced it are exactly the data a mailbox throws away. Sent mail is never
duplicated — cards reference the mail store (notmuch) by Message-ID.

## Library

The public surface is `mddraft.__all__`:

- `ENVELOPE_DOC` — the draft-card envelope convention (documented, never
  validated).
- `AlreadySent` / `AmbiguousSend` — explicit indeterminate or terminal send
  outcomes.
- `at(deck, card_id, sha)` — a card's content as it existed at a commit; the
  approval display and the flush share this one immutable read.
- `attachments(...)` — immutable attachment bytes at the approved commit.
- `compose(card, mid="", ...)` — draft card to `email.message.EmailMessage`.
- `flush(deck, card_id, sha, msmtp=("msmtp",))` — send the bytes at `sha`
  (msmtp invoked exactly once) and stamp `sent_mid`/`sent_sha`/`sent_at`.
- `reconcile(...)` — resolve observations after an indeterminate send.
- `reply(...)` / `forward(...)` — pure correspondence constructors.

The gate — who may call `flush`, as which user, behind which token — is
tenancy wiring, deliberately not in this library.

The gate, approval PWA, and mail substrate are deployment concerns in tenancy
repositories. See `DESIGN.md` for the durable rationale and `CLAUDE.md` for
repository rules.
