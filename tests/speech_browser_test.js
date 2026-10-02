/**
 * Browser speech lifecycle regressions, using the production app functions.
 * Audio playback is stubbed: these test promise ordering, WAV bytes, and URL
 * cleanup. Native autoplay policy still needs a real browser/device check.
 * Run with: node tests/speech_browser_test.js
 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const chunker = require('../gui/chunker.js');

const source = fs.readFileSync(path.join(__dirname, '..', 'gui', 'app.js'), 'utf8');
const start = source.indexOf('// -- Spoken replies');
const end = source.indexOf('// -- Send via API', start);
assert(start >= 0 && end > start, 'the production speech section is present');

function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

const flush = () => new Promise(resolve => setImmediate(resolve));

function harness(plays = []) {
    const created = new Map(), revoked = [], elements = [], listeners = {};
    let serial = 0;
    class Audio {
        constructor() { this.src = ''; this.pauses = 0; this.plays = 0; elements.push(this); }
        play() { this.plays++; return plays.length ? plays.shift() : Promise.resolve(); }
        pause() { this.pauses++; if (this.onpause) this.onpause(); }
    }
    const context = vm.createContext({
        Audio, Blob, console, AbortSignal, AbortController, TextDecoder, Uint8Array,
        URL: {
            createObjectURL(blob) {
                const url = 'blob:http://localhost/test-' + (++serial);
                created.set(url, blob);
                return url;
            },
            revokeObjectURL(url) { revoked.push(url); },
        },
        document: { addEventListener(name, callback) { listeners[name] = callback; } },
        localStorage: { getItem() { return null; } },
        fetch: async () => ({ ok: false }),
        API_BASE: '', speakBtn: null, voiceActive: false,
        window: { GathmChunker: chunker }, GathmChunker: chunker,
        addMessage() {}, setOrbState() {},
    });
    vm.runInContext(source.slice(start, end), context, { filename: 'gui/app.js' });
    return {
        context, created, revoked, elements, listeners,
        state: () => vm.runInContext('({ unlocked: audioUnlocked, priming: audioPriming, current: currentAudio })', context),
    };
}

let passed = 0;
async function test(name, body) {
    await body();
    passed++;
    console.log('  ok   ' + name);
}

async function main() {
    await test('gesture plays a nonempty, silent PCM WAV from a CSP-compatible blob', async () => {
        const pending = deferred();
        const h = harness([pending.promise]);
        h.listeners.pointerdown();
        assert.equal(h.elements[0].plays, 1, 'play happens synchronously in the gesture');
        const url = h.elements[0].src;
        assert(url.startsWith('blob:'));
        const blob = h.created.get(url);
        assert.equal(blob.type, 'audio/wav');
        const wav = Buffer.from(await blob.arrayBuffer());
        assert.equal(wav.toString('ascii', 0, 4), 'RIFF');
        assert.equal(wav.toString('ascii', 8, 16), 'WAVEfmt ');
        assert.equal(wav.readUInt32LE(4), wav.length - 8);
        assert.equal(wav.readUInt16LE(20), 1, 'uncompressed PCM');
        assert.equal(wav.readUInt16LE(22), 1, 'one channel');
        assert.equal(wav.readUInt32LE(24), 8000);
        assert.equal(wav.readUInt16LE(34), 16);
        assert.equal(wav.toString('ascii', 36, 40), 'data');
        assert.equal(wav.readUInt32LE(40), 1600, '100 ms of actual samples');
        assert.equal(wav.length, 44 + 1600);
        assert(wav.subarray(44).every(byte => byte === 0), 'every sample is silent');
        pending.resolve();
        await flush();
        assert.equal(h.state().unlocked, true);
        assert.equal(h.elements[0].pauses, 1);
        assert.deepEqual(h.revoked, [url]);
    });

    await test('repeated gestures do not restart a pending or completed primer', async () => {
        const pending = deferred();
        const h = harness([pending.promise]);
        h.listeners.pointerdown();
        h.listeners.keydown();
        assert.equal(h.elements[0].plays, 1);
        pending.resolve();
        await flush();
        h.context.primeAudio();
        assert.equal(h.elements[0].plays, 1);
        assert.equal(h.created.size, 1);
    });

    await test('a rejected primer is cleaned up and can be retried', async () => {
        const first = deferred();
        const h = harness([first.promise]);
        h.context.primeAudio();
        first.reject(new Error('playback denied'));
        await flush();
        assert.equal(h.state().unlocked, false);
        assert.equal(h.state().priming, false);
        assert.equal(h.revoked.length, 1);
        h.context.primeAudio();
        await flush();
        assert.equal(h.state().unlocked, true);
        assert.equal(h.elements.length, 1, 'retry reuses the same audio element');
        assert.equal(h.revoked.length, 2);
    });

    await test('delayed primer completion cannot pause a real reply', async () => {
        const pending = deferred();
        const h = harness([pending.promise]);
        h.context.primeAudio();
        const clip = h.context.playClip('blob:http://localhost/reply');
        pending.resolve();
        await flush();
        assert.equal(h.elements.length, 1, 'reply reuses the gesture audio element');
        assert.equal(h.elements[0].pauses, 0);
        assert.equal(h.state().current, h.elements[0]);
        h.elements[0].onended();
        await clip;
        assert.equal(h.state().current, null);
        assert(h.revoked.includes('blob:http://localhost/reply'));
    });

    await test('a gesture during an unprimed reply does not replace its source', async () => {
        const h = harness();
        const clip = h.context.playClip('blob:http://localhost/reply');
        h.context.primeAudio();
        assert.equal(h.elements[0].src, 'blob:http://localhost/reply');
        assert.equal(h.created.size, 0);
        h.elements[0].onended();
        await clip;
    });

    await test('barge-in releases playback and clears its event handlers', async () => {
        const h = harness();
        const clip = h.context.playClip('blob:http://localhost/reply');
        h.context.stopSpeaking();
        await clip;
        assert.equal(h.state().current, null);
        assert.equal(h.elements[0].onended, null);
        assert.equal(h.elements[0].onpause, null);
        assert.deepEqual(h.revoked, ['blob:http://localhost/reply']);
    });
    await test('cancellation releases a stream waiting for its next sentence', async () => {
        const h = harness();
        await flush();
        vm.runInContext('speechAvailable = true', h.context);
        const stream = h.context.startStreamingSpeech();
        let settled = false;
        stream.done.then(() => { settled = true; });
        await flush();
        assert.equal(settled, false);
        h.context.stopSpeaking();
        await flush();
        assert.equal(settled, true, 'cancel must wake the empty-queue waiter');
        stream.feed('Never speak this. ');
        await stream.finish();
        assert.equal(h.created.size, 0);
    });
    await test('cancellation aborts synthesis and reclaims prefetched audio', async () => {
        const h = harness();
        await flush();
        vm.runInContext('speechAvailable = true', h.context);
        const requests = [];
        h.context.fetch = async (url, options) => {
            requests.push(options);
            return { ok: true, blob: async () => new Blob(['wav']) };
        };
        const stream = h.context.startStreamingSpeech();
        stream.feed('Hello. This second sentence is long enough to be prefetched. ');
        await flush();
        assert.equal(requests.length, 2);
        assert(h.state().current, 'the first sentence plays before final result');
        h.context.stopSpeaking();
        await stream.done;
        await flush();
        assert(requests.every(request => request.signal.aborted));
        assert.equal(h.state().current, null);
        assert.deepEqual([...new Set(h.revoked)].sort(), [...h.created.keys()].sort());
    });
    await test('SSE parser accepts split UTF-8 packets and a JSON fallback', async () => {
        const h = harness();
        const expected = 'Hello, नमस्ते 🌱.';
        const wire = Buffer.from('data: ' + JSON.stringify({ event: 'token', text: expected }) +
            '\r\n\r\ndata: ' + JSON.stringify({ event: 'result', data: { reply: expected } }) + '\n\n');
        const body = new ReadableStream({ start(controller) {
            for (const byte of wire) controller.enqueue(Uint8Array.of(byte));
            controller.close();
        } });
        const tokens = [];
        const result = await h.context.readChatReply(new Response(body, {
            headers: { 'content-type': 'text/event-stream' },
        }), text => tokens.push(text));
        assert.deepEqual(tokens, [expected]);
        assert.equal(result.reply, expected);
        const legacy = await h.context.readChatReply(Response.json({ reply: 'Legacy.' }),
            () => { throw new Error('JSON must not emit tokens'); });
        assert.equal(legacy.reply, 'Legacy.');
    });
    await test('a truncated SSE response fails instead of accepting a partial reply', async () => {
        const h = harness();
        await assert.rejects(h.context.readChatReply(new Response(
            'data: {"event":"token","text":"Partial"}\n\n',
            { headers: { 'content-type': 'text/event-stream' } }), () => {}),
            /before its final result/);
    });
    console.log('\n' + passed + ' passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
