/** Real Chromium checks for incremental chat, early speech, and cancellation.
 * The model and speech server are deterministic stubs; audio is a real WAV.
 * GATHM_PW_PATH and GATHM_CHROMIUM select an existing Playwright/browser install.
 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

async function main() {
    let pw;
    try { pw = require(process.env.GATHM_PW_PATH || 'playwright-core'); }
    catch (_) { console.log('SKIP: playwright-core is not installed'); return; }
    const waiting = new Map(), calls = [], errors = [];
    const wav = Buffer.alloc(44 + 8000 * 2 / 2);
    wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4);
    wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16);
    wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
    wav.writeUInt32LE(8000, 24); wav.writeUInt32LE(16000, 28);
    wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
    wav.write('data', 36); wav.writeUInt32LE(wav.length - 44, 40);
    const server = http.createServer(async (req, res) => {
        const url = req.url.split('?')[0];
        if (!url.startsWith('/api/')) {
            const file = path.join(__dirname, '../gui', url === '/' ? 'index.html' : path.basename(url));
            try {
                res.setHeader('content-type', { '.js': 'application/javascript', '.css': 'text/css', '.html': 'text/html' }[path.extname(file)] || 'text/plain');
                res.end(fs.readFileSync(file));
            } catch (_) { res.writeHead(404); res.end(); }
            return;
        }
        const buffers = [];
        for await (const part of req) buffers.push(part);
        const body = JSON.parse(Buffer.concat(buffers).toString() || '{}');
        if (url === '/api/v1/agent/chat' && body.query !== 'legacy') {
            res.writeHead(200, { 'content-type': 'text/event-stream' });
            res.flushHeaders();
            waiting.set(body.query, res);
            return;
        }
        if (url === '/api/v1/speech') {
            calls.push(body.text);
            res.writeHead(200, { 'content-type': 'audio/wav' });
            res.end(wav);
            return;
        }
        res.setHeader('content-type', 'application/json');
        res.end(JSON.stringify(url === '/api/v1/agent/chat'
            ? { reply: 'The legacy JSON reply still works.' } : { ok: true, available: true }));
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    let browser, passed = 0;
    function check(name, body) { body(); passed++; console.log('  ok   ' + name); }
    const event = (res, data) => res.write('data: ' + JSON.stringify(data) + '\n\n');
    const token = (res, text) => event(res, { event: 'token', text });
    const finish = (res, reply) => { event(res, { event: 'result', data: { reply } }); res.end(); };
    async function open(query) {
        const page = await browser.newPage();
        page.on('pageerror', error => errors.push(String(error)));
        await page.goto('http://127.0.0.1:' + server.address().port + '/');
        await page.waitForFunction('speechAvailable');
        const offset = calls.length;
        await page.fill('#messageInput', query);
        await page.click('#sendBtn');
        for (let i = 0; query !== 'legacy' && !waiting.has(query) && i < 100; i++) {
            await new Promise(resolve => setTimeout(resolve, 20));
        }
        assert(query === 'legacy' || waiting.has(query), 'chat request reached stub');
        return { page, res: waiting.get(query), offset };
    }
    async function settled(page) {
        return page.evaluate(() => Promise.race([
            speaking.then(() => true), new Promise(resolve => setTimeout(() => resolve(false), 1000)),
        ]));
    }
    try {
        browser = await pw.chromium.launch({
            executablePath: process.env.GATHM_CHROMIUM || undefined,
            headless: true, args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'],
        });
        console.log('Streaming chat in Chromium ' + browser.version());
        const first = 'Hello from the first sentence.';
        const rest = ' This second sentence arrives before the final response is ready.';
        const tail = ' The final tail is complete.';
        {
            const { page, res, offset } = await open('stream');
            token(res, first + ' ');
            await page.waitForFunction('currentAudio !== null');
            check('text and audio start while the final response is withheld', () =>
                assert.deepEqual(calls.slice(offset), [first]));
            assert.equal(await page.evaluate('isSending'), true);
            assert((await page.locator('#chatArea').innerText()).includes(first));
            token(res, rest.trimStart());
            finish(res, first + rest + tail);
            await page.waitForFunction('!isSending');
            await page.evaluate(() => speaking);
            check('final-only tail is spoken once, in order, with no repeated first sentence', () =>
                assert.equal(calls.slice(offset).join(' '), first + rest + tail));
            const messageCount = await page.evaluate('transcript.filter(m => m.sender === "bot").length');
            check('partial text becomes one final transcript message', () => assert.equal(messageCount, 1));
            assert.equal(await page.evaluate('history[1].content'), first + rest + tail);
            await page.close();
        }
        for (const action of ['mute', 'barge-in']) {
            const { page, res, offset } = await open(action);
            token(res, first + ' ');
            await page.waitForFunction('currentAudio !== null');
            await page.waitForFunction('currentAudio === null');
            if (action === 'mute') await page.click('#speakBtn');
            else await page.evaluate(() => stopSpeaking());
            const released = await settled(page);
            check(action + ' releases speech while waiting between SSE sentences', () => assert(released));
            token(res, rest.trimStart());
            finish(res, first + rest);
            await page.waitForFunction('!isSending');
            check(action + ' prevents later tokens from restarting audio', () =>
                assert.deepEqual(calls.slice(offset), [first]));
            await page.close();
        }
        {
            const { page, res, offset } = await open('trimmed-final');
            token(res, '\n' + first + ' \n');
            await page.waitForFunction('currentAudio !== null');
            finish(res, first);
            await page.waitForFunction('!isSending');
            await page.evaluate(() => speaking);
            check('trimming the final answer never repeats the streamed speech', () =>
                assert.deepEqual(calls.slice(offset), [first]));
            await page.close();
        }
        {
            const { page, res, offset } = await open('early-barge-in');
            await page.evaluate(() => stopSpeaking());
            token(res, first + ' ');
            finish(res, first);
            await page.waitForFunction('!isSending');
            await page.evaluate(() => speaking);
            check('barge-in before the first token suppresses the late answer speech', () =>
                assert.deepEqual(calls.slice(offset), []));
            await page.close();
        }
        {
            const { page, offset } = await open('legacy');
            await page.waitForFunction('!isSending');
            await page.evaluate(() => speaking);
            check('JSON fallback still renders and speaks the reply once', () =>
                assert.deepEqual(calls.slice(offset), ['The legacy JSON reply still works.']));
            assert.equal(await page.evaluate('history[1].content'), 'The legacy JSON reply still works.');
            await page.close();
        }
        {
            const { page, res, offset } = await open('truncated');
            token(res, 'Incomplete text');
            res.end();
            await page.waitForFunction('!isSending');
            check('truncated streams restore the composer and do not speak an unfinished draft', () =>
                assert.deepEqual(calls.slice(offset), []));
            assert.equal(await page.evaluate('history.length'), 0);
            assert((await page.locator('#chatArea').innerText()).includes('can’t reach Gathm'));
            await page.close();
        }
        check('no browser script errors', () => assert.deepEqual(errors, []));
        console.log(passed + ' passed');
    } finally {
        if (browser) await browser.close();
        for (const res of waiting.values()) res.end();
        server.closeAllConnections();
        await new Promise(resolve => server.close(resolve));
    }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
