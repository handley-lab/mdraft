# mddraft — founding design record

Interests elicited by interview with Will, 2026-07-05. This document records the
full context of the "bring email into the alan system" programme — wider than
mddraft itself — so the interests survive as a unit. The scope table below says
which repo owns which part.

## The one principle

**Store the process, reference the product.**

The mail store (notmuch over Maildir) already holds every message ever sent or
received, addressable by Message-ID. mddraft never copies it. What the mailbox
structurally cannot hold is the *trajectory*: the agent's proposed draft, the
owner's edits, the spoken steers ("too formal", "don't chase him yet"), the
abandoned versions. None of those ever become mail, so none have a Message-ID —
they exist only if something else records them. mddraft is that something: a
deck of draft cards where edits are commits, so the drafting process is
captured for free by the substrate, and finished artifacts are referenced by
MID rather than duplicated.

## The interests

1. **The never-event.** An email going out in Will's name without his explicit
   say-so must be *structurally impossible* — not discouraged by prompt, not
   gated by a UI the agent can route around. Past experience: agents defeat
   every soft gate; even mutt's send-confirmation was sometimes circumvented.
   What earns trust is capability separation: the agent's surface is
   write-draft only; the flush capability lives outside the agent's reach, and
   the sole trigger is the owner's act. "It cannot send. I press a button."

2. **Verbatim approval, surface-agnostic.** At the moment of approval Will is
   reading the *full, exact* outgoing text — no summary, no paraphrase — and
   knows that what he reads is byte-for-byte what goes out. Pressing send
   should feel like sending an email. The surface is deliberately open: phone
   app, Linux app, Vim interface, cockpit — any is acceptable; the terminal is
   not required.

3. **Collaborative drafting.** Alan drafts from the cockpit or WhatsApp; Will
   redrafts by speech; iteration feels like working with a secretary. Editing
   in Vim via mutt is pleasant but not essential. The bright line is never the
   composing — it is the send.

4. **Continuous learning.** The current system is amnesiac. Every draft, every
   edit Will makes to it, every steer, plus enough surrounding context, is
   stored to improve the drafting model over time. The proposal→final diff is
   the training datum. mddb supplies this natively: `editor()` mutations are
   commits, `history()` replays the trajectory, no bespoke versioning layer.

5. **Alan's own address, secretary norms.** Alan gets his own email address.
   Sending from that address to anyone important is governed by the same
   never-event discipline — a secretary knows not to contact somebody in
   another's name, or over their head, without asking.

6. **Inbound: rate-limit the surfacing, not the transport.** Today the sync is
   held to hourly purely to protect deep work from an inbox full of spam and
   other-people's-urgency. Given a triage layer Will trusts, sync becomes
   continuous and attention is protected downstream:
   - *Interrupt tier* — the rare email where a prompt reply is significantly
     advantageous **to Will** (his interest, not the sender's; email is mail —
     letters opened asynchronously — not messaging). Only these surface
     immediately.
   - *Batch tier* — everything else waits for the morning brief and a daily
     digest: "in the past 24 hours these arrived; these N are spam, I'm
     confident; shall I unsubscribe from X and Y?" The digest is exhaustive at
     a glance — Will needs to see everything that came through at least a bit;
     nothing is silently swallowed. Alan proposes, Will disposes.
   - *Clarify* — triaging, processing, and clarifying email is part of Alan's
     GTD role: actionable mail mints GTD cards referencing the MID.

7. **What David Allen says (checked, not assumed).** Allen sanctions email as
   its own collection bucket — "as many inboxes as you need, as few as you can
   get by with" — and never asks for a universal funnel. His demands: process
   each bucket to zero; clarify every item; actionable outcomes land in the one
   trusted system with the email filed/referenced, never left as its own
   reminder. The "corrupted inbox" is his canonical failure (an inbox doubling
   as an amorphous action pile). His *emergency scanning* vs *processing*
   distinction maps directly onto the design: the agent does the emergency
   scanning continuously so Will never has to; the agent pre-chews processing
   so Will's clarify pass is confirm/adjust. Email does NOT get mirrored into
   the GTD inbox — the GTD inbox receives only what clarification mints.

8. **notmuch tagging substrate.** notmuch never worked well for Will because
   nothing tags the corpus (190,713 messages, zero tagging automation — only
   built-in flags). Fixing classification (afew or notmuch hooks) is worth
   doing regardless of everything else and is a prerequisite for triage.

9. **Auth to the Cambridge account.** The current M365-IMAP XOAUTH2 refresh
   requires occasional interactive re-login; acceptable but not optimal. Goal:
   on the rare occasion re-auth is genuinely needed, a single Raven login,
   cached until the next genuine expiry. Survey the current flow before
   changing it.

10. **Modular placement.** No monolith. mddraft is a thin layer over mddb,
    parallel to mdcal/mdgtd. Workflow (triage, digest, brief) is agents
    composing the substrate — alan-work's business, not module code. The
    transport underneath (offlineimap → notmuch → msmtp) already works and is
    already de-Googled; mddraft does not reimplement it.

## Why MIDs alone are not sufficient (the argument for the deck)

Challenged and answered during the interview:

- The *sent* email is already in notmuch, with Message-ID and In-Reply-To.
  Re-storing it as a card is duplication with worse fidelity. For finished
  artifacts, a MID pointer is strictly better than a copy.
- But the learning signal is not the final email. It is the delta (what the
  agent proposed vs what Will changed), the steers, and the abandoned drafts —
  and none of these ever enter the mail store, so none have a MID to point at.
  Either something records the process or it is lost.
- The store must (a) version/diff, (b) be the live editable object the approval
  surface displays and the sender flushes, (c) link out by MID. mddb provides
  all three natively and is already a dependency of the whole stack; a bespoke
  ledger would hand-roll versioning next to an existing versioned store.
- The markdown is incidental: draft bodies are text. The deck exists solely to
  hold what the mailbox throws away.

## The substrate underneath (surveyed 2026-07-05, boltzmann)

- **Ingest**: offlineimap, hourly oneshot timer, `postsynchook = notmuch new`.
  Live accounts: Hermes (wh260@cam.ac.uk, Office365 XOAUTH2), Gmail
  (williamjameshandley@gmail.com), PolyChord (will.handley@polychord.co.uk),
  HandleyLab (handleylab@gmail.com). Dormant: BrandRadar, CambridgeMachines.
- **Index**: one notmuch DB at `~/mail`, 190,713 messages,
  `maildir.synchronize_flags=true`, no custom tagging.
- **Send**: msmtp configured for all six identities including both
  williamjameshandley@gmail.com and wh260@cam.ac.uk — iMIP replies and gated
  sends can go out as whichever identity received the mail. Send-as-you was
  never a blocker.
- **Wrapper**: mcp-handley-lab `email` module (~4k LOC: notmuch read/update,
  mutt-gated send, offlineimap sync, MIME/HTML extraction) — the pre-alan MCP
  surface this programme supersedes.

## Scope

| concern | home |
|---|---|
| draft cards, learning ledger, capability-separated send gate | **mddraft** (this repo) |
| verbatim approval surface (PWA) | alan-work (via alan-pwa) |
| inbound triage agent: continuous sync, interrupt tier, digest, brief feed | alan-work |
| notmuch tagging substrate | alan-work issue; config is ops |
| Alan's own address + secretary policy | alan-work |
| M365/Raven auth streamlining | alan-work issue; config is ops |
| outbound iMIP calendar invites (consume the send gate) | mdcal (#8) |
| IMAP/storage/search/transport | existing stack — not rebuilt |

## Non-goals

- Not a mail client, not a mail store, not a notmuch replacement.
- No copying of the mail archive into cards; no markdown-ification of email.
- No code path by which an agent can flush the outbox. If a change would
  create one, the change is wrong.

## Naming

`mddraft`: the repo's entire card content is markdown drafts, so the `md*`
family prefix is earned, not cosmetic. PyPI name claimed 2026-07-05 alongside
mddb and mdcal (both Handley Research Group).
