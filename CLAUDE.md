# CLAUDE.md — mddraft

Correct, minimal documentation is best. Omission is preferable to an
unsupported or obsolete claim. Incorrect documentation is worst.

Working rules for any agent touching this repo. The founding interests and their
rationale live in DESIGN.md — read it before changing anything load-bearing.

## The invariant (never-event)

**No code path may exist by which an agent can flush the outbox.** No
agent-reachable process, file, socket, or endpoint may hold or reach a send
credential — for ANY identity, Will's or an agent's own. Every outbound email
flushes only through the gate, triggered by Will's explicit act, after he has
read the verbatim outgoing text. A change that creates an agent-reachable flush
path is wrong by definition, whatever else it improves. This is structural
security: the guarantee lives in process/user/permission separation, never in
prompts or policy checks.

## Store the process, reference the product

Draft cards hold what the mailbox throws away: the agent's proposal, Will's
edits (commits), the steers (rationales), the abandoned versions. The mail
store (notmuch) holds the finished artifacts; cards reference them by
Message-ID and NEVER copy them. If you find yourself writing mail content into
a card after send, or parsing the archive into cards, stop — that duplication
is the design smell this repo exists to avoid.

## Code philosophy

- **Lean code.** Fewest elegant lines that implement the interests. Every line
  justifies its existence. No defensive programming: this substrate runs on one
  trusted box; crash on drift, don't guard against it.
- **No premature abstraction.** mddraft is a thin layer over mddb the way mdcal
  is: cards + a handful of pure functions. Workflow (triage, digest, persona
  behaviour) is agents composing the substrate — it lives in tenancy repos,
  never here.
- **Config over code.** msmtp routes identities; systemd supervises; git
  versions. Don't reimplement what the substrate already provides — an edit is
  a commit, a history is a corpus, a rationale is a steer.
- **Draft content is attacker-controlled by design.** Approval surfaces render
  card content only as text/form values — never HTML, never markdown, never
  linkified. Same-origin XSS is a send path.
- **The send-capable user never runs git where agents write.** A repo with an
  untrusted writer can always smuggle executable configuration (hooks, config,
  attribute filters) past file-mode guards — so the outbox admits no untrusted
  writers, and agent-owned decks are read by the gate only via exec-safe
  plumbing (`git cat-file`/`rev-parse`/`ls-tree`) or plain file reads. A
  group-writable `.git` is a flush path wearing a git costume.
- **Agents are the readers.** Write for the next agent reasoning over this
  code: self-contained names, no path-dependent comments, invariants stated
  where they're enforced.
- **The editor is the only mutation.** All card writes go through
  `db.editor()` (one commit per logical change, rationale mandatory).
