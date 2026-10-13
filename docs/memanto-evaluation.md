# Memanto fit for Gathm

Reviewed on 2026-10-13 against Memanto commit
[`fbc1d67`](https://github.com/moorcheh-ai/memanto/tree/fbc1d67d2f9d40964e78de14e645844e7342f91d).
This is a source review. Memanto and its backend were not installed or benchmarked.

## Recommendation

Keep Gathm's SQLite harness as the default. Consider Memanto as an optional
semantic search service when testing shows that the current word matching misses
useful approved lessons. Integrating it is technically feasible through its
Python client or REST API, but it adds services and does not replace Gathm's
approval workflow or conversation checkpoints.

## What it could add

Memanto provides typed memories, semantic retrieval, conflict reports, retention
policies, portable exports, and a memory dashboard. These are useful directions
for Gathm. In particular, semantic retrieval could match paraphrases that share
fewer than the two significant words required by the current harness. That
benefit needs measurement on Gathm's own questions; it was not demonstrated by
this review. See the [project README](https://github.com/moorcheh-ai/memanto).

Gathm already has selective capture, reflection, persistent SQLite storage,
owner/scope isolation, evidence, review, recall, feedback, and retirement. Adding
a second independent learning loop would duplicate those responsibilities.

| Concern | Existing Gathm harness | Memanto integration implications |
| --- | --- | --- |
| Storage | Embedded SQLite; no extra service | Moorcheh Cloud or a local Moorcheh service |
| Search | Bounded word matching | Semantic retrieval via the configured backend |
| Approval | Pending lessons excluded from replies | Preserve this gate in Gathm; regular Memanto writes default to active |
| Memory maintenance | Exact-trigger revisions and explicit retirement | Policies, conflict management, exports, and dashboard |
| Conversation continuity | Limited recent context; no shared durable task checkpoint | Still requires separate work in Gathm |

## Deployment and privacy

The Python package is MIT licensed and supports Python 3.10+, compatible with
Gathm's declared Python version. Its base dependencies include FastAPI, Uvicorn,
Moorcheh SDK, Pydantic settings, and HTTPX; it is a larger dependency set than
the current standard-library harness.

The [backend adapter](https://github.com/moorcheh-ai/memanto/blob/fbc1d67d2f9d40964e78de14e645844e7342f91d/memanto/app/clients/backend.py)
supports Cloud and On-Prem. The default configuration is Cloud and requires a
Moorcheh API key. The local onboarding path requires Docker and installs the
additional Moorcheh client. Its quick setup uses Ollama with `nomic-embed-text`
and `qwen2.5`; these are additional model/runtime choices beyond Gathm's current
llama.cpp setup. A local deployment can keep inference local when its embedding
and generation providers are also local. Selecting a local storage service alone
does not guarantee that an optionally configured hosted model stays local.

These requirements need separate installation and resource testing on Gathm's
supported environments, especially Termux. No claim of working Termux support
is made here.

Sources: [dependencies](https://github.com/moorcheh-ai/memanto/blob/fbc1d67d2f9d40964e78de14e645844e7342f91d/pyproject.toml),
[configuration](https://github.com/moorcheh-ai/memanto/blob/fbc1d67d2f9d40964e78de14e645844e7342f91d/memanto/app/config.py),
[local setup](https://github.com/moorcheh-ai/memanto/blob/fbc1d67d2f9d40964e78de14e645844e7342f91d/memanto/cli/commands/core.py).

## Preserve Gathm's review contract

Memanto's normal write service stores memories directly. Its record lifecycle
has `active` and `expired` states. Conversation extraction has a dry-run option,
but normal writes do not implement Gathm's mandatory pending-to-approved gate.
Confidence values and extraction filters do not prove that a memory is correct.

An optional integration should therefore:

1. Keep captured evidence and pending proposals in Gathm's local SQLite store.
2. Publish only approved, redacted lesson versions into a namespace derived from
   the same owner and scope that Gathm uses.
3. Use Memanto to return candidate lesson IDs. Resolve their text from SQLite
   and recheck approval, scope, version, relevance, and the context budget before
   including them in a reply. A remote edit must not silently rewrite guidance.
4. Remove indexed versions on archive or replacement; recheck local state even
   if that synchronization fails. Treat expired Memanto results as ineligible.
5. Fall back to local recall when the optional service is unavailable, without
   replaying user actions or blocking chat for a long network timeout.

This is a proposed design, not an implemented integration. Any later Memanto
consolidation or conflict-resolution output that changes lesson text should
return to Gathm's review queue.

Sources: [memory record](https://github.com/moorcheh-ai/memanto/blob/fbc1d67d2f9d40964e78de14e645844e7342f91d/memanto/app/core.py),
[write path](https://github.com/moorcheh-ai/memanto/blob/fbc1d67d2f9d40964e78de14e645844e7342f91d/memanto/app/services/memory_write_service.py),
[extraction endpoint](https://github.com/moorcheh-ai/memanto/blob/fbc1d67d2f9d40964e78de14e645844e7342f91d/memanto/app/routes/memory.py),
[read path](https://github.com/moorcheh-ai/memanto/blob/fbc1d67d2f9d40964e78de14e645844e7342f91d/memanto/app/services/memory_read_service.py).

## Adoption test

Compare local word matching with semantic recall on held-out paraphrases,
unrelated questions, contradictory lessons, rejected lessons, and archived
versions. Measure relevant recall, irrelevant guidance, latency, installation
size, and offline behavior. Cross-owner retrieval or unapproved guidance must
remain absent. Install the optional integration only if the measured improvement
justifies its operating cost for the intended deployment.
