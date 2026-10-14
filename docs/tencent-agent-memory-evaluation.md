# TencentDB Agent Memory fit for Gathm

Reviewed on 2026-10-13 against the repository's current default branch,
`feat/server_team`, commit
[`badbd21`](https://github.com/TencentCloud/TencentDB-Agent-Memory/tree/badbd2179d06f4e0e1f2133bd3482880beab8a02).
The source review and an isolated local MemoryCore benchmark are complete.
**Keep Gathm's existing SQLite and local embedding recall.** Tencent's candidate
search is fast, but does not yet offer enough benefit for Gathm's small,
reviewed lesson library to justify another service and approval adapter.
The earlier [Tencent Cloud VectorDB assessment](./tencent-vectordb-evaluation.md)
covers a different product and does not describe this repository's local mode.

## Local operation

This project supports local operation. Its default memory store is SQLite with
`sqlite-vec` for vector retrieval and FTS5 for text search. Tencent Cloud VectorDB
is an optional backend; the deployment guide also describes an experimental
MongoDB backend that is off by default. A Tencent Cloud database subscription
is not required for the default SQLite deployment.

**Correction to the earlier source review:** an internal `node-llama-cpp`
EmbeddingGemma implementation remains in the tree, but the current config parser
explicitly disables `embedding.provider: local`, and the SQLite store factory
does not instantiate it. It is not a supported user configuration at this
commit. The default `provider: none` gives keyword search. Local semantic search
can use an OpenAI-compatible embedding server on localhost; that is the
supported configuration benchmarked here. No EmbeddingGemma model was downloaded.

Memory extraction/summarization and the proxy's upstream chat model have separate
configurable endpoints. Local storage does not by itself establish fully offline
operation: configure those model endpoints, embeddings, and any enabled external
integrations to stay local. The Docker template requires the operator to supply
model parameters; it does not bundle an entire local chat/extraction model setup.

Sources: [installation](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/badbd2179d06f4e0e1f2133bd3482880beab8a02/INSTALL.md),
[store factory](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/badbd2179d06f4e0e1f2133bd3482880beab8a02/MemoryCore/src/core/store/factory.ts),
[config parser](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/badbd2179d06f4e0e1f2133bd3482880beab8a02/MemoryCore/src/config.ts),
[embedding implementation](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/badbd2179d06f4e0e1f2133bd3482880beab8a02/MemoryCore/src/core/store/embedding.ts),
[deployment template](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/badbd2179d06f4e0e1f2133bd3482880beab8a02/deploy/global-images/.env.example).

## What could help Gathm

The project organizes memory into raw conversations (L0), extracted facts and
preferences (L1), scenario summaries (L2), and long-term profiles (L3). It also
manages skills, document Wiki assets, code graphs, ownership, and team sharing.
These capabilities make it a broader memory system than Gathm's current bounded
lesson library.

Its proxy caches a profile snapshot at session initialization, injects profile
content and scenario navigation, and exposes detailed history through tools.
For Gathm, reviewed scenario summaries and stable preferences could improve
continuity without inserting full transcripts into every turn. A memory review
screen and searchable conversation history would also be useful additions.

Sources: [overview](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/badbd2179d06f4e0e1f2133bd3482880beab8a02/README.md),
[profile injection](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/badbd2179d06f4e0e1f2133bd3482880beab8a02/MemoryProxy/src/injection/injectors/tdai-profile-memory-injector.ts).

## Measured latency and retrieval

The saved [measurement summary](./benchmarks/tencent-agent-memory-2026-10-13.json)
uses macOS ARM64, Python 3.11.15, Node 26.3.0, 50 synthetic approved lessons,
12 held-out paraphrases, five unrelated questions, and 60 distinct timing
queries per path. Both semantic paths use the same prepared
`BAAI/bge-small-en-v1.5` model, with 384 dimensions and one CPU inference thread.

Tencent runs its actual standalone gateway and SQLite/sqlite-vec/FTS5 store,
with an actual HTTP request to `/v3/atomic/search`, candidate limit three.
The benchmark seeds synthetic approved records directly into its store, so it
does not test automatic extraction or an approval publishing adapter. Capture,
extraction and external telemetry are disabled. Hub, Proxy and Docker are not
required for this standalone comparison. Tencent's sparse BM25 encoder is
disabled because SQLite's FTS5 search does not need it.

| Retrieval path | Warm median | Warm p95 | p95 during local chat generation | Expected lesson found | Unrelated questions returning memory |
| --- | ---: | ---: | ---: | ---: | ---: |
| Gathm approved, filtered recall | 16.4 ms | 17.3 ms | 19.3 ms | 8/12 | 0/5 |
| Tencent with localhost BGE, raw candidates | 5.3 ms | 6.3 ms | 8.2 ms | 11/12 | 5/5 |
| Tencent with embeddings disabled, raw FTS candidates | 0.7 ms | 0.9 ms | 0.6 ms | 5/12 | 4/5 |

These are different output contracts: Gathm returns prompt-ready reviewed notes;
Tencent returns search candidates. Returning nearest candidates for an unrelated
query is valid for a search API, but they cannot all be injected into Gathm's
prompt. The raw 11/12 result therefore does not establish better useful recall.
Hybrid scores use reciprocal rank fusion rather than cosine similarity, so
Gathm's cosine threshold cannot be applied directly to the API score.

As a post-hoc diagnostic, independently scoring those candidate IDs against the
complete approved fixture with Gathm's existing similarity/margin and keyword
rules gives 7/12 hits and zero unrelated returns for Tencent's semantic path
(3/12 and zero for FTS). This diagnostic is not an integrated adapter benchmark;
a larger candidate limit or different ranking might improve it. It does not
demonstrate a useful-recall advantage for the tested configuration.

Gathm's first cold recall fell back to keywords in about 34 ms total. Its 25 ms
limit is the semantic wait budget, not a deadline for all database and filtering
work. The configured approximately 1.88 GiB local chat model generated while
all three retrieval paths were measured; generation completed without errors.
This measures retrieval under load, not end-to-end answer quality, time to first
token, or the effect of Tencent's background extraction on generation speed.

With sparse BM25 disabled, the gateway reached readiness with 50 records in
about 1.5–1.7 seconds, including a one-second settling interval. Its measured RSS
after forced garbage collection was about 289–304 MiB, excluding the separate
embedding service and chat model. An earlier run with sparse BM25 enabled used
about 1.6–1.9 GiB and took 4.4–4.8 seconds. Disabling it reduced memory greatly
without changing retrieval hits; these are this machine's measurements, not
cross-platform resource guarantees.

Sources: [candidate search](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/badbd2179d06f4e0e1f2133bd3482880beab8a02/MemoryCore/src/core/tools/memory-search.ts),
[rank fusion](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/badbd2179d06f4e0e1f2133bd3482880beab8a02/MemoryCore/src/core/tools/l1-candidate-recall.ts).

## Integration decision

Keep Gathm's implemented SQLite/embedding recall as the default. This Tencent
project is relevant for a future optional memory hub, or as a source of design
ideas, rather than a database migration required for semantic recall. Its use
of SQLite with vector search supports that same architectural direction.

The recommended full deployment starts Memory Core, Memory Hub, and Proxy
containers. Memory Core declares Node.js 22.16+ and native SQLite/embedding
dependencies. Integrating this TypeScript service stack into Gathm's Python
runtime needs a REST adapter and deployment validation. A Python SDK exists,
but does not remove those runtime and lifecycle requirements. The supported
localhost setup avoids internet round trips, but adds a gateway, embedding
endpoint, duplicated indexes and synchronization of approval/revision/archive
state. The measured raw search savings of roughly 11 ms do not include that
adapter's relevance scoring and canonical approval checks.

The core configuration's default recall timeout is 5,000 ms. That is an upper
bound for auto-recall, not a measured query duration or a deadline for the atomic
search endpoint. Gathm's current semantic embedding wait is
25 ms with keyword fallback; an integration should preserve a similarly bounded
reply path instead of inheriting the service's defaults unchanged.

The documented automatic path captures conversations, refines profiles in the
background, and injects profiles into future sessions. The system has asset
governance and team sharing controls, but that automatic profile path is not
Gathm's mandatory human approval before any new lesson affects replies. An
adapter must stage new facts, summaries, and revisions for local review before
making them eligible. Model-based skill review must not be treated as the user's
approval.

Sources: [runtime dependencies](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/badbd2179d06f4e0e1f2133bd3482880beab8a02/MemoryCore/package.json),
[recall defaults](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/badbd2179d06f4e0e1f2133bd3482880beab8a02/MemoryCore/src/config.ts),
[Python SDK](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/badbd2179d06f4e0e1f2133bd3482880beab8a02/sdk/memory-core/python/README.md),
[profile injection](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/badbd2179d06f4e0e1f2133bd3482880beab8a02/MemoryProxy/src/injection/injectors/tdai-profile-memory-injector.ts).

No Tencent service or database has been added to Gathm's runtime. Default-on
learning, mandatory user review, owner isolation and bounded keyword fallback
continue through the existing harness. Reconsider an optional Tencent backend
if Gathm needs shared team memory, documents, skills or substantially more than
the current 50 approved lessons. First test that broader use case with a real
approval adapter and end-to-end latency measurements.

## Reproduce the local comparison

Use a separate checkout pinned to `badbd2179d06f4e0e1f2133bd3482880beab8a02`.
In its `MemoryCore` directory, install dependencies with
`npm install --ignore-scripts --legacy-peer-deps --package-lock=false --no-audit --no-fund`.
Avoid its install hooks: this benchmark does not require modifying an OpenClaw
installation or installing `node-llama-cpp`.

With Gathm's memory extra and public embedding model already prepared, and its
local chat model configured, run:

```sh
pilot/venv/bin/python tests/eval_tencent_memory.py \
  --core-dir /absolute/path/to/TencentDB-Agent-Memory/MemoryCore \
  --output-dir /tmp/gathm-tencent-benchmark
```

The opt-in script checks the source revision, builds the fixed synthetic
fixture, creates disposable databases, starts localhost servers on free ports,
and stops its own processes. It saves full results and logs in the output
directory. It does not download models, run extraction, or use real memories.
Only this lightweight configuration is reproduced by the saved script; the
earlier BM25-enabled measurement is retained in the summary as a comparison.
