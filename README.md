# mddraft

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

## Status

Design — see `DESIGN.md` for the founding interests and scope.
