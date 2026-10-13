# Tencent Cloud VectorDB fit for Gathm

Reviewed on 2026-10-13. This assesses Tencent Cloud VectorDB, the vector search
product relevant to agent memory; TencentDB also includes other database
products. No cloud instance was provisioned or benchmarked.

Tencent Cloud VectorDB is a managed distributed vector database. Its documented
features include similarity search, embedding support, and hybrid dense/sparse
search. Its hybrid search supports BM25 with reciprocal-rank or weighted fusion.
It is technically suitable for a large shared knowledge service.

Sources: [product](https://cloud.tencent.cn/product/vdb),
[feature history](https://cloud.tencent.cn/document/product/1709/95477).

For Gathm's current personal library of at most 50 approved lessons, keep local
SQLite and the small offline encoder. A cloud service introduces network round
trips, authentication, region selection, a recurring service bill, and an online
dependency. Those are deployment tradeoffs, not measured Tencent performance
results. Advertised database query latency does not establish the total latency
from a user's laptop or phone through embedding, network, retrieval, and reply.

Tencent's pricing documentation lists prepaid and pay-as-you-go instance billing,
region-dependent memory/disk prices, and token-based embedding charges. The
appropriate instance and total cost depend on deployment region and workload;
this review does not claim a quoted price for Gathm.

Sources: [pricing](https://buy.cloud.tencent.cn/price/vdb),
[VectorDB regions](https://cloud.tencent.cn/document/product/1709/95008).

If Gathm later serves multiple users or a large shared document library, evaluate
Tencent as an optional remote index. Preserve local approval and owner/scope
checks, index only approved redacted versions, resolve returned IDs against
canonical lesson state, and retain an offline fallback. Benchmark p50/p95
end-to-end retrieval from the actual client regions before adopting it. Do not
automatically send conversation history or pending lessons to a hosted backend.
