/**
 * Deterministic voice cancellation regressions using production app functions.
 * Browser audio and permission promises are stubbed. Browser integration is
 * exercised separately with synthetic audio in conversation_browser_test.js.
 * Run with: node tests/voice_lifecycle_test.js
 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'gui', 'app.js'), 'utf8');
function section(start, end) {
    const first = source.indexOf(start);
    const last = end ? source.indexOf(end, first) : source.length;
    assert(first >= 0 && last > first, 'production section exists: ' + start);
    return source.slice(first, last);
}
const production = [
    section('let isSending = false;', '// -- Conversation memory'),
    section('function updateComposerState()', 'function saveChat()'),
    section('async function sendMessage(', "sendBtn.addEventListener('click'"),
    section('let audioCtx    = null;'),
].join('\n');

function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

function element() {
    const attributes = {}, listeners = {}, classes = new Set();
    return {
        style: {}, value: '', textContent: '', disabled: false, hidden: false,
        attributes, listeners,
        classList: {
            add(name) { classes.add(name); },
            remove(name) { classes.delete(name); },
            toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); },
        },
        setAttribute(name, value) { attributes[name] = value; },
        removeAttribute(name) { delete attributes[name]; },
        addEventListener(name, handler) { listeners[name] = handler; },
        appendChild() {}, remove() {}, focus() {},
        querySelectorAll() { return []; }, querySelector() { return null; },
    };
}

function harness({ permission, transcription } = {}) {
    const calls = [], messages = [], tracks = [], contexts = [];
    let cleared = 0, micRequests = 0;
    function stream() {
        const track = { stopped: false, stop() { this.stopped = true; } };
        tracks.push(track);
        return { getTracks: () => [track] };
    }
    class AudioContext {
        constructor() {
            this.state = 'running'; this.sampleRate = 16000;
            this.destination = {}; this.closed = false;
            contexts.push(this);
        }
        createAnalyser() { return { frequencyBinCount: 32, getByteFrequencyData() {} }; }
        createMediaStreamSource() { return { connect() {} }; }
        createScriptProcessor() { return { connect() {}, disconnect() {} }; }
        createGain() { return { gain: {}, connect() {} }; }
        close() { this.closed = true; return Promise.resolve(); }
    }
    const context = vm.createContext({
        console, performance, Float32Array, Uint8Array, Blob, AbortSignal,
        document: { createElement: element },
        location: { protocol: 'http:', host: 'localhost' },
        navigator: { mediaDevices: { getUserMedia() {
            micRequests++;
            return permission ? permission.promise : Promise.resolve(stream());
        } } },
        window: { AudioContext },
        freqBars: element(), aiOrb: element(), mainOrb: element(),
        botStatus: element(), micBtn: element(), convoBtn: element(),
        clearBtn: element(), voiceHint: element(), messageInput: element(),
        sendBtn: element(), chatArea: element(),
        speechAvailable: true, speakEnabled: true, API_BASE: '',
        history: [], HISTORY_MAX: 12, speaking: Promise.resolve(),
        GathmVAD: { VAD: class { reset() {} rearm() {} } },
        fetch: async function(url, options) {
            calls.push({ url, options });
            if (url.endsWith('/transcribe')) {
                if (transcription) return transcription.promise;
                return { ok: true, json: async () => ({ text: 'Old spoken words' }) };
            }
            if (url.endsWith('/agent/chat')) {
                return { ok: true, json: async () => ({ reply: 'A reply' }) };
            }
            return { ok: true, json: async () => ({ available: true }) };
        },
        setInterval() { return 1; }, setTimeout() { return 1; }, clearInterval() {},
        requestAnimationFrame() { return 1; }, cancelAnimationFrame() {},
        syncConversationView() {}, refreshIcons() {}, scrollToBottom() {},
        stopSpeaking() {}, setOrbState() {}, checkConnectivity() {},
        autoGrow() {}, showTyping() {}, hideTyping() {}, saveChat() {},
        formatAgentReply(data) { return data.reply; }, speakReply() {},
        clearChat() { cleared++; },
        addMessage(text, sender) { messages.push({ text, sender }); },
        isSpeakingNow() { return false; },
    });
    vm.runInContext(production, context, { filename: 'gui/app.js' });
    return {
        context, calls, messages, tracks, contexts, stream,
        cleared: () => cleared, micRequests: () => micRequests,
        run: expression => vm.runInContext(expression, context),
        count: suffix => calls.filter(call => call.url.endsWith(suffix)).length,
    };
}

let passed = 0;
async function test(name, body) {
    await body();
    passed++;
    console.log('  ok   ' + name);
}

async function main() {
    await test('ending during microphone permission closes the late stream', async () => {
        const permission = deferred();
        const h = harness({ permission });
        await flush();
        const starting = h.context.startConversation();
        await flush();
        assert.equal(h.run('voiceStarting && convoMode'), true);
        assert.equal(h.context.clearBtn.disabled, true);
        h.context.stopConversation();
        const lateStream = h.stream();
        permission.resolve(lateStream);
        await starting;
        assert.equal(h.run('voiceActive || voiceStarting || convoMode'), false);
        assert.equal(h.tracks[0].stopped, true, 'late permission cannot leave a microphone open');
        assert.equal(h.contexts.length, 0, 'canceled capture never builds an audio graph');
        assert.equal(h.context.clearBtn.disabled, false);
        assert.equal(h.count('/transcribe'), 0);
        assert.equal(h.count('/agent/chat'), 0);
    });

    await test('ending mid-sentence discards capture instead of sending dictation', async () => {
        const h = harness();
        await flush();
        await h.context.startConversation();
        h.run('pcmChunks = [new Float32Array(16000)]; capturing = true;');
        h.context.stopConversation();
        await flush();
        assert.equal(h.run('pcmChunks.length'), 0);
        assert.equal(h.run('voiceActive || convoMode'), false);
        assert(h.tracks.every(track => track.stopped));
        assert(h.contexts.every(context => context.closed));
        assert.equal(h.count('/transcribe'), 0);
        assert.equal(h.count('/agent/chat'), 0);
    });

    await test('an ended session cannot submit a late speech transcript', async () => {
        const transcription = deferred();
        const h = harness({ transcription });
        await flush();
        await h.context.startConversation();
        const pendingTurn = h.context.handleTurn([new Float32Array(16000)]);
        await flush();
        assert.equal(h.count('/transcribe'), 1);
        h.context.stopConversation();
        transcription.resolve({ ok: true, json: async () => ({ text: 'Old spoken words' }) });
        await pendingTurn;
        assert.equal(h.count('/agent/chat'), 0);
        assert.equal(h.context.messageInput.value, '');
        assert.equal(h.run('isTranscribing || turnBusy || voiceActive || convoMode'), false);
        assert.equal(h.context.clearBtn.disabled, false);
    });

    await test('typed reset obeys busy guards and still works when idle', async () => {
        const h = harness();
        await flush();
        for (const command of ['/clear', '/reset']) {
            for (const flag of ['isSending', 'voiceActive', 'voiceStarting', 'convoMode', 'isTranscribing', 'turnBusy']) {
                h.run(flag + ' = true');
                h.context.messageInput.value = command;
                await h.context.sendMessage();
                assert.equal(h.cleared(), 0, command + ' must wait for ' + flag);
                assert.equal(h.context.messageInput.value, command, 'keep the unsent reset visible');
                h.run(flag + ' = false');
            }
        }
        assert.equal(h.count('/agent/chat'), 0);
        await h.context.sendMessage();
        assert.equal(h.cleared(), 1, 'idle reset clears the conversation');
        assert.equal(h.context.messageInput.value, '');
    });

    await test('dictation shows an active microphone and restores the idle hint on stop', async () => {
        const h = harness();
        await flush();
        await h.context.startVoice();
        assert.equal(h.run('voiceActive && !convoMode'), true);
        assert.match(h.context.voiceHint.textContent, /microphone is on/);
        assert.equal(h.context.micBtn.attributes['aria-label'], 'Send voice message');
        h.context.stopVoice(true);
        assert.match(h.context.voiceHint.textContent, /microphone stays off/);
        assert.equal(h.context.micBtn.attributes['aria-label'], 'Dictate a message');
        assert(h.tracks.every(track => track.stopped));
    });

    await test('permission denial restores usable voice controls', async () => {
        const permission = deferred();
        const h = harness({ permission });
        await flush();
        const starting = h.context.startVoice();
        assert.equal(h.context.micBtn.attributes['aria-label'], 'Cancel microphone request');
        assert.equal(h.context.convoBtn.disabled, true, 'do not start a second pending capture');
        permission.reject(Object.assign(new Error('Permission denied'), { name: 'NotAllowedError' }));
        await starting;
        assert.equal(h.run('voiceStarting || voiceActive'), false);
        assert.equal(h.context.micBtn.attributes['aria-label'], 'Dictate a message');
        assert.equal(h.context.convoBtn.disabled, false);
        assert.equal(h.context.clearBtn.disabled, false);
        assert.match(h.context.voiceHint.textContent, /microphone stays off/);
        assert(h.messages.some(message => /Microphone blocked/.test(message.text)));
    });
    console.log('\n' + passed + ' passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
