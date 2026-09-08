# Conversational context, grocery workflows, and inbox reliability

This change includes PR #4 at `52484b1` and main at `849e56f`,
including the Outlook provider. It preserves the grocery updates and
upstream protections for drafts using imported private context. The added
inbox changes address three concrete failures: related notes were unavailable
at triage/archive time, a verifier refusal authorized clearing, and an expired
Gmail history cursor skipped directly to the present.

## Resulting behavior

- Profile no longer shows Never miss, Everything before now, or Tell Nano
  something. Users set priorities and share context in conversation.
- The conversational `remember_context` action saves the user's actual words
  in `saved_context`, scoped to that user and idempotent for repeated saves.
  The next conversation receives recent notes; older notes enter hybrid search.
  A failed index write leaves the canonical note intact for dispatcher retry.
  Searchable chat context is private reference, so drafts using it retain the
  existing explicit-review hold. Priority and mute rules continue to use their
  existing durable actions through chat. `forget_context` removes the selected
  saved note and its searchable chunks in one transaction; other users' notes
  cannot be deleted. Save, delete, and indexing retry share a PostgreSQL lock
  to prevent a concurrent indexer recreating deleted context. Whole conversation
  transcripts are no longer archived on close, so they cannot recreate a note
  the person just asked to forget.
- Gmail and Outlook connections initialize a fixed window of three calendar
  years. OAuth starts background processing; the dispatcher also discovers
  previously connected mailboxes. Each page and its checkpoint commit together,
  with no total-message cutoff. An expired page token restarts the same window
  and deduplicates records. Completed imports are not restarted on reconnect.
  Mail history supplies context and never enters the triage/draft/send queue.
  Background history loading is reported as incomplete evidence, rather than a
  global veto on every inbox action. Actual retrieval/index failures and
  unindexed saved notes still hold automatic decisions; missing old exchanges
  must not be interpreted as proof that a sender is new or unimportant.
- Triage, archive verification, and drafting retrieve scoped, dated source
  excerpts. Source content remains untrusted reference material.
- Reply context matches complete correspondent addresses and thread IDs.
  Drafts using imported private material stay held until the user explicitly
  saves reviewed words; that action releases the hold for their manual send.
- Failed retrieval or verification keeps mail visible. Verification requires
  the JSON boolean `veto: false`; malformed output, a refusal, an offline stub,
  and exceptions cannot authorize clearing. High importance or a reply
  obligation cannot be silently overridden by a conflicting cleared tier.
- Missing embedding credentials retain source text for lexical retrieval and
  later indexing. Semantic search excludes legacy hash stubs. Embedding calls
  process and validate every batch, including documents longer than 128 chunks.
  Existing vectors without provenance are re-indexed; pending source indexing
  keeps the incomplete-context hold even after the query embedding service recovers.
- Gmail bootstrap and expired-history recovery capture a boundary before a
  paginated scan. Each page and its checkpoint commit together. A subsequent
  history read from that boundary catches arrivals during scanning. A failed
  page retains its cursor and retries; a forbidden message is not silently
  skipped. A deleted message can no longer be fetched and is skipped explicitly.
- Recovered mail can generate drafts for review but cannot automatically send
  or archive. The same hold applies when enabling an auto-reply rule and when
  an already scheduled draft reaches its deadline. Direct user-reviewed sends
  remain available.
- Inbox state, the mobile banner, the hub card, and the morning briefing expose
  incomplete sync. `/dispatch-tick` resumes bounded recovery work after a
  restart; ordinary sync and pull-to-refresh also advance it.

## Grocery workflows

- Groceries is reachable from the hub and voice. The native screen opens item
  details, saves new items, marks stock corrections, and edits shopping-list
  quantities and removals. Adding an item preserves the existing list, including
  after a previous store handoff. Background scans preserve user edits.
- Voice adds unfamiliar items directly. Ambiguous names ask for clarification
  before writing any part of the request. Nano describes the actual next step:
  review the list and open Instacart, where the user chooses products and pays.
- The mobile handoff uses a version fingerprint, reuses an existing link on
  retry, and only opens HTTPS Instacart URLs. It never calls confirm/place.
  Unsupported stores and missing configuration show simple availability text;
  provider keys and partner approval instructions stay out of the product UI.
- Both live mail and three-year history feed receipt extraction. A durable
  per-user/source ledger prevents repeated processing after restarts and
  deduplicates a receipt appearing in both stores. Candidate filtering happens
  before the batch limit, so unrelated recent mail cannot hide older receipts.
  A per-user PostgreSQL lock serializes extraction. Failed model responses
  retry promptly, then once a day after repeated failures.
- Old purchases remain available for learning, but items last bought more than
  120 days ago do not populate the current shelf unless explicitly pinned,
  added to the list, or marked out. Imported one-off purchases therefore do
  not immediately create a huge restocking list. Forecasts remain estimates.

## Migration sequence

Main's already merged revisions `0020`–`0023` remain unchanged. PR #4 is
still unmerged and reused those numbers, so its grocery migrations follow main:

| Revision | Change |
| --- | --- |
| 0020–0023 | Existing main: draft generation, inbox signals, memory provenance, imported-context draft hold |
| 0024 | Grocery tables from PR #4 |
| 0025 | Grocery quantities and product sizes from PR #4 |
| 0026 | Grocery handoff URL from PR #4 |
| 0027 | Recovery checkpoint, sync error, last successful sync, conservative legacy vector re-indexing |
| 0028 | Automatic history checkpoints, durable chat context, wider historical IDs for Outlook |
| 0029 | Durable receipt extraction ledger and wider purchase source IDs for Outlook |

Run `alembic upgrade head` before starting this API version. This sequence
supports current main through `0023`. Databases that applied the **unmerged
PR #4** or an earlier PR #5 commit under conflicting revision numbers need
schema/version reconciliation before deploying this combined branch; do not
stamp a new revision blindly. This PR does not deploy or migrate any user database.

Migration `0027` marks existing successful vectors pending once, because
main's older migration labelled historical vectors successful without knowing
whether they were hash stubs. Source text stays searchable lexically while the
index catches up; automatic actions remain held during incomplete indexing.

## Verification

`python -m pytest -q` exercises the combined inbox and grocery code. New tests
check evidence reaching all three decisions, verifier failure modes, missing
context, expired cursors, a forbidden fetch, restart/rollback of a partially
classified page, conservative recovery actions, and embedding batch integrity.
Conversation/history regressions additionally check exact-word persistence,
cross-user isolation, indexing failure/retry, OAuth initialization, fixed dates,
page rollback, restart deduplication, and the absence of historical queue writes. Product-flow regressions cover
historical receipt consumption, duplicate receipts across both stores, outage
retry, tenant-scoped forgetting, preserving edited shopping lists, and narrowed
history holds.

The `Inbox release checks` workflow runs the suite, TypeScript checking, nine mobile interaction tests, and
`scripts/check_release_postgres.py` against a disposable PostgreSQL 16/pgvector
service. The PostgreSQL check upgrades main, adds the grocery schema, then
preserves existing mail, a grocery row, and source text through the recovery
migration. It checks source retention and scoped lexical/dense SQL, verifies JSON NULL
recovery state, and confirms a failed retrieval does not poison the transaction.
It also checks automatic-history JSON, private chat indexing with savepoint
recovery, deletion of saved notes and search chunks, historical receipt SQL
and locking, and Outlook's longer historical message IDs.
Its model vectors and store responses are test doubles, not evidence of model
quality or live retailer integration. The actual GroceryScreen was also viewed
at 393×852 in a React Native Web preview, exercising list review, quantity
changes, and adding an item. This is not a native iOS device/build check.

The migration tests also start from a populated main database at `0023` and
verify all grocery tables exist after upgrade. A valid head stamp alone cannot
detect a revision ID reused for a different schema change.

## Remaining release limits

Recovery covers the current inbox accepted by the existing Gmail label filter;
it is not a full historical-mail import or recovery of deleted mail. Recovery
throughput depends on dispatcher cadence (up to 25 listed messages per account
per tick). Keep that worker scheduled; large inboxes remain visibly incomplete
until scanning and catch-up finish.

The separate historical-context worker processes up to 50 messages per page,
one page per account per dispatch invocation (at most five accounts, oldest
progress first), plus four initial rounds after OAuth. Completion time depends
on mailbox size, provider latency and dispatcher cadence; it is not immediate
onboarding. The existing dispatch schedule is ten minutes. Monitor pending and
retrying `history_import_state` records and keep the dispatcher scheduled.
Historical imports update correspondent counters without one LLM call per old
email. Source text remains available for retrieval and current-message reasoning.

Receipt extraction processes at most 12 candidates per user/run and five
users per dispatch call, separately from importing mail. Keep the dispatcher
scheduled. The shopping handoff requires a configured Instacart developer key;
Nano does not read a user's Walmart/Instacart account history or confirm that a
handed-off list was purchased. Receipts and explicit stock corrections update
the shelf. No live retailer checkout or native mobile build was tested.

This PR does not implement verified discovery of new email recipients,
automatic Teams/document connectors, or reconciliation of an uncertain send
after a provider timeout. It is not a claim
that users can safely turn off all their mail notifications. Scoped real-user
validation and production configuration checks are still required.
