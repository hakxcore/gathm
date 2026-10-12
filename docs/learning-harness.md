# Learning harness

Gathm can turn tool experience into reusable lessons. This improves the context
available to its model; it does not train model weights or establish that the
model is getting more accurate. The design is inspired by
[AutoHarness](https://github.com/tigerless-labs/autoharness), with an explicit
human review gate for every new lesson and revision.

## Start learning

```bash
gathm harness enable
gathm harness status
```

Learning is off until enabled. Once enabled, normal tool-using conversations in
the terminal assistant and browser GUI record short local episodes. An episode
contains the question, up to six tool commands, bounded output snippets, and
whether the turn completed, failed, or was interrupted. Ordinary conversation,
full history, and final model claims are not captured. Direct Bash orchestrator
commands (`gathm ask`, `gathm run`) are outside this first version.

After five recorded turns, a separate process asks the configured model for up
to three short procedural lessons. It can propose text; it cannot execute tools,
change Gathm's source, or approve its own output. A process stops after at most
75 seconds. Reflection errors keep episodes available for retry and do not
interrupt chat or replay actions. A database lease prevents overlapping passes.

To reflect on fewer episodes now:

```bash
gathm harness reflect
```

In the terminal assistant, use `/harness enable`, `/harness status`, and `/learn`.
Review browser-session lessons through the same CLI; there is no browser review
screen in this version.

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
offered as fallible reference notes. Up to three fit within a 2,400-character
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

## Storage and privacy

State lives in private SQLite files under `~/.gathm/harness/` (or the configured
Gathm state directory). The namespace includes the OS, scope, and API principal.
The local GUI and terminal share a namespace. Authenticated API clients have
separate namespaces, so one client's lessons cannot enter another's prompts.
Local administrators can inspect such a namespace by setting `GATHM_CHAT_OWNER`
to its existing principal hash. Do not use the same principal for unrelated users.

Secret patterns and credential values present in the process environment are
redacted before storage and reflection. This is best effort, not a guarantee
that every secret or personal detail is removed. Hosted backends receive the
redacted episode snippets for reflection, and approved recalled notes during
chat; local backends keep inference on their configured server. Do not enable
capture for sessions whose contents must not be stored or sent to that backend.

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

An enabled namespace remains enabled across restarts. Changing the scope selects
a different library, which starts disabled. No new Python packages are needed.

## Verification

`tests/test_harness.py` exercises the lifecycle, redaction, evidence validation,
immutable review IDs, scope/owner isolation, retention, concurrent reflection,
the real compiled agent graph, and the shell CLI. Its model is deterministic.
These tests verify the mechanism; they do not demonstrate better real-world
answers. Measure that separately with representative tasks before and after
approving lessons, including failures and irrelevant-lesson cases.
