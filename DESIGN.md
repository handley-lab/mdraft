# mddraft design

Correct, minimal documentation is best. Omission is preferable to an
unsupported or obsolete claim. Incorrect documentation is worst.

## Store the process, reference the product

The mail store already holds sent and received messages with their native bytes
and Message-IDs. Copying finished mail into cards creates a weaker second source
of truth. MDDraft therefore references finished mail by Message-ID.

What mail does not retain is the drafting trajectory: proposals, edits, steers,
rejected versions, and abandonments. Draft cards preserve that process. Git
commits record each mutation and its rationale, so proposal-to-final differences
remain available without a parallel event store.

## Never-event

An agent must have no path to send mail. The sending credential and flush
capability belong to a separate user and process that an agent cannot reach.
Only the owner's explicit act after reading the verbatim outgoing content crosses
that boundary. This applies to every sender identity, including an agent-owned
address.

The approval display and flush operation read the same immutable card commit.
Draft content is attacker-controlled and is rendered only as inert text or form
values. Same-origin script execution is a send path.

The send-capable process must not execute Git in a repository writable by an
agent: hooks, filters, and configuration are executable authority. Agent-owned
proposal decks are inspected only through the deployment's exec-safe Git
plumbing and copied into the trusted Outbox by the approved boundary.

## Boundaries

- mddb stores draft cards, history, and rationales.
- notmuch/Maildir stores finished mail and provider-native evidence.
- mddraft converts immutable cards to messages and performs one flush attempt.
- msmtp owns identity routing and SMTP transport.
- deployment repositories own users, credentials, browser approval, and service
  wiring.
- agents own judgement and proposals, never the flush capability.

An ambiguous transport result remains ambiguous. `reconcile` compares later mail
observations with the approved bytes; it does not invent success or repeat a send
whose outcome is unknown.
