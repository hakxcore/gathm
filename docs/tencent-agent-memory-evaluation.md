# TencentDB Agent Memory fit for Gathm

Reviewed on 2026-10-13 against the repository's current default branch,
`feat/server_team`, commit
[`badbd21`](https://github.com/TencentCloud/TencentDB-Agent-Memory/tree/badbd2179d06f4e0e1f2133bd3482880beab8a02).
This is a documentation and source review; the services were not installed or
benchmarked. The earlier [Tencent Cloud VectorDB assessment](./tencent-vectordb-evaluation.md)
covers a different product and does not describe this repository's local mode.

## Local operation

This project supports local operation. Its default memory store is SQLite with
`sqlite-vec` for vector retrieval and FTS5 for text search. Tencent Cloud VectorDB
is an optional backend; the deployment guide also describes an experimental
MongoDB backend that is off by default. A Tencent Cloud database subscription
is not required for the default SQLite deployment.

The embedding service supports local `node-llama-cpp` inference with an
EmbeddingGemma GGUF model, and OpenAI-compatible embedding endpoints. Its local
implementation loads the embedding model in the background and lets callers
fall back to keyword search while the model is unavailable.

Memory extraction/summarization and the proxy's upstream chat model have separate
configurable endpoints. Local storage does not by itself establish fully offline
operation: configure those model endpoints, embeddings, and any enabled external
integrations to stay local. The Docker template requires the operator to supply
model parameters; it does not bundle an entire local chat/extraction model setup.

Sources: [installation](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/badbd2179d06f4e0e1f2133bd3482880beab8a02/INSTALL.md),
[store factory](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/badbd2179d06f4e0e1f2133bd3482880beab8a02/MemoryCore/src/core/store/factory.ts),
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

## Integration tradeoffs and recommendation

Keep Gathm's implemented SQLite/embedding recall as the default. This Tencent
project is relevant for a future optional memory hub, or as a source of design
ideas, rather than a database migration required for semantic recall. Its use
of SQLite with vector search supports that same architectural direction.

The recommended full deployment starts Memory Core, Memory Hub, and Proxy
containers. Memory Core declares Node.js 22.16+ and native SQLite/embedding
dependencies. Integrating this TypeScript service stack into Gathm's Python
runtime needs a REST adapter and deployment validation. A service's local
deployment can avoid internet round trips, but adds local RPC and operational
work. Its effect on Gathm's response latency is unmeasured.

The core configuration's default recall timeout is 5,000 ms. That is an upper
bound, not a measured query duration. Gathm's current semantic embedding wait is
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
[profile injection](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/badbd2179d06f4e0e1f2133bd3482880beab8a02/MemoryProxy/src/injection/injectors/tdai-profile-memory-injector.ts).

Before adopting the service, compare its benefit on cross-session Gathm tasks
with the current local baseline, including cold/warm p95 latency, prompt size,
irrelevant memories, approval boundaries, and owner isolation. Its published
benchmark does not establish improvement for Gathm's model and workflows.
