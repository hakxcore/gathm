/** Synthetic benchmark helper; never part of Gathm runtime. */
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const { TdaiGateway } = await import(pathToFileURL(path.join(process.argv[2], 'src/gateway/server.ts')).href);

const fixture = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const before = performance.now();
const gateway = new TdaiGateway();
Object.assign((gateway as any).logger, {debug: () => {}, info: () => {}, warn: () => {}, error: console.error});
async function stop() { await gateway.stop(); process.exit(0); }
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
await gateway.start();
const core = (gateway as any).core;
const store = core.getVectorStore();
if (!store || store.isDegraded() || !store.isFtsAvailable()) throw new Error('Actual store not ready');
const embedder = core.getEmbeddingService();
const created = new Date().toISOString();
for (const row of fixture.records) {
  const embedding = embedder ? new Float32Array(row.vector) : undefined;
  if (!store.upsertL1({
    id: row.id, content: row.content, type: 'instruction', priority: 50,
    scene_name: 'benchmark', source_message_ids: [], metadata: {}, timestamps: [created],
    createdAt: created, updatedAt: created, version: 1,
    sessionKey: 'benchmark', sessionId: 'synthetic', teamId: 'gathm', userId: 'benchmark', agentId: 'assistant',
  }, embedding)) throw new Error('Failed to seed actual L1 store');
}
const beforeGC = process.memoryUsage();
(globalThis as any).gc?.();
await new Promise(resolve => setTimeout(resolve, 1000));
(globalThis as any).gc?.();
fs.writeFileSync(path.join((gateway as any).config.data.baseDir, 'benchmark-ready.json'), JSON.stringify({
  init_and_index_ms: performance.now() - before, memory_before_gc: beforeGC, memory_after_gc: process.memoryUsage(),
  dimensions: embedder?.getDimensions?.(), records: fixture.records.length,
  fts: store.isFtsAvailable(), vectors: store.getCapabilities().vectorSearch,
}));
console.log('GATHM_BENCHMARK_READY');
