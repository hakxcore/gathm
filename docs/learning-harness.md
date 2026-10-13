# Learning harness

Gathm turns tool experience and explicit user preferences or corrections into
proposed reusable lessons. Learning is enabled by default. This improves the context
available to its model; it does not train model weights or establish that the
model is getting more accurate. The design is inspired by
[AutoHarness](https://github.com/tigerless-labs/autoharness), with an explicit
human review gate for every new lesson and revision.

## Start learning

```bash
gathm harness status
```

No activation step is needed. The terminal assistant and browser GUI record
selected, short local episodes as you use Gathm. A tool episode
contains the question, up to six tool commands, bounded output snippets, and
whether the turn completed, failed, or was interrupted.

Conversation-only episodes are selected conservatively from explicit preferences
or corrections, for example:

- "I prefer concise bullet points when explaining database storage."
- "From now on, use small examples when explaining programming concepts."
- "No, use decimal units when explaining storage capacity."

A completed preference turn stores the user's request. A correction can also
include the preceding user/assistant exchange, with assistant text labelled
unverified. Each field is bounded and redacted. Greetings, ordinary factual
questions, incomplete conversation turns, and recognized one-off or private
requests are skipped. "Don't learn this" skips capture for that request.

These English phrase checks are deliberately narrow; not every natural-language
preference is detected. `/harness teach` remains available for explicit lessons.
Full histories and current model answers are not saved as learning evidence.
Existing browser transcripts are not bulk-imported. Direct Bash orchestrator
commands (`gathm ask`, `gathm run`) are outside this first version.

After five recorded turns, a separate process asks the configured model for up
to three short procedures or communication preferences. It can propose text; it cannot execute tools,
change Gathm's source, or approve its own output. A process stops after at most
75 seconds. Reflection errors keep episodes available for retry and do not
interrupt chat or replay actions. A database lease prevents overlapping passes.

To reflect on fewer episodes now:

```bash
gathm harness reflect
```

In the terminal assistant, use `/harness status` and `/learn`.
Review browser-session lessons through the same CLI; there is no browser review
screen in this version.

An explicit `gathm harness disable` setting survives restarts and upgrades.
Use `gathm harness enable` to resume after disabling. Learning happens in response
to new qualifying turns, not continuously while Gathm is idle.

## Conversation state versus lasting lessons

Gathm already has short-term state in LangGraph's `AgentState`: recent messages,
tool results, the next step, and recalled lessons are passed through the current
reasoning loop. Holding a variable alone does not give a model knowledge; Gathm
includes the relevant messages and notes in each model request.

| Layer | Stored where | Lifetime |
| --- | --- | --- |
| Terminal conversation | Process memory; latest 12 messages by default (`PILOT_MAX_HISTORY`) | Until terminal assistant exits |
| Browser conversation | Browser `localStorage`; up to 200 displayed messages, latest 12 turns sent as model context | Survives reloads in that browser; cleared by Clear chat/browser storage removal |
| API conversation | Caller supplies `history` each turn | The server worker does not independently remember prior chats |
| Learned lessons | SQLite file in the user's scoped Gathm state directory | Across restarts, until retired or the state is removed |

There is no shared, durable conversation checkpoint that resumes an entire task
across terminal and browser, and no unlimited conversation window. The harness
adds selective long-term guidance without changing those existing state limits.

## Review before use

```bash
gathm harness list --status pending
gathm harness show LESSON_ID
gathm harness approve LESSON_ID
# Or discard a proposal:
gathm harness reject LESSON_ID
```

`show` includes the trigger, guidance, captured evidence, and any approved lesson
it would replace. Check that the guidance follows from the evidence and applies
beyond a single task. Tool output labelled `returned` means it produced an
output; it is not proof the requested task succeeded.

Conversation proposals must cite an exact quote from the current user request;
previous assistant statements cannot supply that quote. Common attempts to weaken
approval, permission, or safety rules are rejected by a local lint before storage.
The same exact lesson is not proposed again after you reject it (within retained
history); an explicit `teach` can submit it again for your review.

The model is instructed to omit unsupported claims, changing facts, jokes,
insults, and one-off instructions. These instructions and pattern checks cannot
prove a lesson is correct or beneficial. A user correction can also be mistaken.
Your review remains the final boundary before any new lesson influences replies.

Pending lessons never enter the model's prompt. Approval makes a lesson eligible
for subsequent turns. A revision leaves the current approved lesson active until
you approve its replacement. Pending IDs are immutable, so a background pass
cannot change what you are about to approve. Reject a pending proposal before
submitting a different version of the same trigger.

You can propose a correction or preference directly without sending it through
model reflection:

```bash
gathm harness teach "When planning my weekly study schedule" \
  "Leave time for review before introducing new material."
gathm harness show LESSON_ID
gathm harness approve LESSON_ID
```

## Recall, feedback, and retirement

Approved lessons matching at least two significant words in the question are
offered as fallible reference notes. With the optional local embedding model
prepared, semantic search can also add one clear paraphrase match. Up to three
lessons fit within a 2,400-character
content budget per turn. Recall runs once per turn, preserving a stable prompt
through its tool steps. Current instructions, fresh results, disabled tools,
and existing device/shell confirmations retain precedence. Learning does not
grant additional permissions or execute stored commands.

```bash
gathm harness list --status active
gathm harness helpful LESSON_ID
gathm harness unhelpful LESSON_ID
gathm harness archive LESSON_ID
```

`offered` counts inclusion in a prompt, not adherence or task success. Helpful
and unhelpful feedback is explicitly supplied by you and breaks ties in recall.
Exact triggers, ignoring case and whitespace, merge into one scenario. Broader
semantic consolidation and automatic quality evaluation are not implemented.

The library holds at most 50 pending and 50 approved lessons. When full, review,
reject, or archive entries before adding more. Approved lessons are not silently
evicted based on unreliable usage signals. The latest 200 episodes, 100 retired
lessons, and 500 audit entries are retained; older entries are pruned. Superseded
lessons are archived with their evidence. To restore retired guidance, `teach`
it again and review the new proposal.

## Local semantic recall and latency

SQLite remains the authoritative store for approval, evidence, and ownership.
Normalized 384-dimensional vectors for approved lessons are stored in a separate
SQLite table. At the 50-lesson limit, an exact cosine scan is small enough to
avoid an additional vector database service or native SQLite extension.

Install the optional CPU embedding runtime and prepare its model once:

```bash
# Packaged installation:
pip install 'gathm[memory]'
gathm harness prepare

# From this checkout instead:
pilot/venv/bin/python -m pip install -e '.[memory]'
./gathm harness prepare
```

`prepare` downloads public `BAAI/bge-small-en-v1.5` model files into Gathm's
embedding cache. No conversations are sent for this download. Chat loads those
local files directly and never downloads models. The default embedding model
is for English; cross-language recall is not claimed. Systems without supported
FastEmbed/ONNX Runtime wheels, including unverified Termux environments, keep
keyword recall and the same learning/review workflow.

After preparation, semantic recall is used automatically when approved lessons
exist. An optional `gathm harness index` command builds vectors for the current
approved library in advance. Otherwise a background worker builds missing
vectors lazily. Approving a revision or archiving a lesson removes its old
vector; inference also checks the current lesson version before saving a vector.
Rebuilding the model changes the cache fingerprint and invalidates old vectors.

Each process has one daemon embedding worker and at most one outstanding job.
The encoder uses one CPU inference thread. The chat path waits at most 25 ms for
semantic work, then proceeds with keyword recall. Cold model loading and
indexing happen in the worker, so the first turn may use keyword recall. Repeated
query vectors have a bounded, owner-scoped, 64-entry memory cache; query text is
redacted and bounded before encoding and is not stored in the vector table.
Embedding failures back off for 60 seconds and leave keyword recall available.

Semantic-only recall requires cosine similarity of at least 0.70 and a margin
of 0.04 over the next candidate. Ambiguous matches are omitted. Current approval,
owner/scope, lesson text, and context limits are rechecked before prompt use.
SQLite recall uses short lock timeouts; contention omits memory instead of
waiting on the usual one-second write timeout. The 25 ms budget is for the
embedding wait, not a hard deadline for the entire database/recall operation.

On this macOS development machine, a fixed synthetic evaluation with 50 active
lessons measured warm unique-query recall at about 18 ms median and 20 ms p95,
versus about 5 ms median and 6 ms p95 for keyword recall. Repeated-query recall
was about 13 ms median. Cold fallback took about 37 ms including normal recall
work. Eight of twelve held-out paraphrases recalled the expected lesson, versus
zero using the existing keyword matcher; none of five unrelated questions
recalled guidance. These are small development measurements, not production
latency guarantees or a general accuracy benchmark.

## Storage and privacy

Learning data lives in private SQLite files under `~/.gathm/harness/` (or the configured
Gathm state directory). The namespace includes the OS, scope, and API principal.
The `learning.sqlite3` file holds episodes, proposed/approved lessons, feedback,
settings, and a bounded audit trail. SQLite is embedded in Python: no external
database server or vector database needs to be installed.
The local GUI and terminal share a namespace. Authenticated API clients have
separate namespaces, so one client's lessons cannot enter another's prompts.
Local administrators can inspect such a namespace by setting `GATHM_CHAT_OWNER`
to its existing principal hash. Do not use the same principal for unrelated users.

Secret patterns and credential values present in the process environment are
redacted before storage and reflection. This is best effort, not a guarantee
that every secret or personal detail is removed. Hosted backends receive the
redacted episode snippets for reflection, and approved recalled notes during
chat; local backends keep inference on their configured server. Disable capture
for sessions whose contents must not be stored or sent to that backend.

```bash
gathm harness disable       # stops capture and recall, keeps your library
gathm harness status        # includes location and last reflection result
```

Disabling invalidates an in-flight reflection's write lease; it does not retract
a request already sent to the model. A model turn already in progress keeps its
existing prompt. Configuration:

| Variable | Meaning |
| --- | --- |
| `GATHM_HARNESS_DIR` | Override the base directory containing isolated namespaces |
| `GATHM_CONFIG_DIR` | Existing Gathm state root, used when the override is absent |
| `GATHM_HARNESS_SCOPE` | Stable workspace name, default `personal`; use different names for unrelated projects |
| `GATHM_HARNESS_DISABLED=1` | Process-level switch that disables capture, recall, and reflection |
| `GATHM_HARNESS_SEMANTIC=0` | Use keyword recall without embedding work; learning stays enabled |
| `GATHM_HARNESS_EMBEDDING_DIR` | Override the shared cache of public embedding model files |

Learning defaults to enabled in a new namespace and in existing stores with no
explicit enable/disable setting. An explicitly disabled namespace stays disabled.
Changing scope selects a different library, which is enabled by default. Storage
is created lazily on the first qualifying capture; checking empty status does
not create a database. Basic learning needs no new Python packages; local
semantic recall uses the optional `memory` dependency extra.

## Verification

`tests/test_harness.py` exercises the lifecycle, redaction, evidence validation,
immutable review IDs, scope/owner isolation, retention, concurrent reflection,
the real compiled agent graph, and the shell CLI. Its model is deterministic.
These tests verify the mechanism; they do not demonstrate better real-world
answers. Measure that separately with representative tasks before and after
approving lessons, including failures and irrelevant-lesson cases.

`tests/test_semantic_memory.py` covers paraphrases, stale/corrupt vectors,
approval/archive races, namespace isolation, redaction, failures, and bounded
fallback. The opt-in offline evaluation uses the real prepared encoder:

```bash
pilot/venv/bin/python tests/eval_harness_recall.py --held-out \
  --output /tmp/gathm-recall-results.json
```
