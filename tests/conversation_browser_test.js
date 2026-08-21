/**
 * End-to-end test for hands-free conversation mode, in a real browser.
 *
 * The microphone is synthesised inside the page: getUserMedia is replaced with
 * an oscillator feeding a MediaStreamDestination, whose gain the test turns up
 * to "speak" and down to fall silent. Everything downstream of that is the real
 * thing — the Web Audio graph, the ScriptProcessor capture, the endpointer in
 * gui/vad.js, the transcribe and chat requests, playback, and the return to
 * listening. The server is a stub, so no Ollama and no audio.cpp are needed.
 *
 * Why not Chromium's own fake device: --use-file-for-fake-audio-capture needs a
 * sound card to enumerate against, and in a container with none it either fails
 * with NotFoundError or delivers digital silence. Injecting the stream also
 * makes the timing deterministic, which a looping WAV is not. The cost is that
 * the getUserMedia call itself is not exercised; its failure path is one branch
 * with a visible message, and startVoice() is covered by the mode tests.
 *
 * This exists because the unit tests cannot catch the failure that actually
 * happens: a loop that works perfectly in isolation and never fires in the page.
 *
 *   node tests/conversation_browser_test.js
 *
 * Skips (exit 0) when playwright-core is not installed.
 */
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { splitSpeech } =
    require(path.join(__dirname, '..', 'gui', 'chunker.js'));

const ROOT = path.join(__dirname, '..');
const GUI = path.join(ROOT, 'gui');

// Several sentences, so the client has something to chunk.
const REPLY = 'It is thirty two degrees and clear in Delhi right now. ' +
              'A light breeze is coming from the north west. ' +
              'No rain is expected before the evening.';
const FIRST_SENTENCE = 'It is thirty two degrees and clear in Delhi right now.';

let PASS = 0, FAIL = 0;
function check(name, got, want) {
    if (JSON.stringify(got) === JSON.stringify(want)) {
        PASS++; console.log('  ok   ' + name);
    } else {
        FAIL++;
        console.log('  FAIL ' + name);
        console.log('         got:  ' + JSON.stringify(got));
        console.log('         want: ' + JSON.stringify(want));
    }
}
function ok(name, cond) { check(name, !!cond, true); }

function loadPlaywright() {
    const candidates = [
        'playwright-core', 'playwright',
        path.join(os.tmpdir(), 'node_modules', 'playwright-core'),
    ];
    for (const c of candidates) {
        try { return require(c); } catch (_) { /* next */ }
    }
    // Anywhere npm put it for this sandbox.
    const scratch = process.env.GATHM_PW_PATH;
    if (scratch) { try { return require(scratch); } catch (_) { /* fall through */ } }
    return null;
}

/** Static files plus a stub of the API the page talks to. */
function startStubServer(calls) {
    const types = {
        '.html': 'text/html', '.js': 'application/javascript',
        '.css': 'text/css',
    };
    const server = http.createServer(function (req, res) {
        const url = req.url.split('?')[0];
        const json = function (obj, code) {
            res.writeHead(code || 200, { 'Content-Type': 'application/json',
                                         'Access-Control-Allow-Origin': '*' });
            res.end(JSON.stringify(obj));
        };

        if (url.startsWith('/api/')) {
            let body = [];
            req.on('data', function (d) { body.push(d); });
            req.on('end', function () {
                const raw = Buffer.concat(body);
                let text = '';
                if (url === '/api/v1/speech') {
                    try { text = JSON.parse(raw.toString()).text || ''; }
                    catch (_) { text = ''; }
                }
                calls.push({ url: url, method: req.method, bytes: raw.length,
                             text: text, at: Date.now() });

                if (url === '/api/v1/ping') return json({ ok: true });
                if (url === '/api/v1/transcribe/status')
                    return json({ available: true });
                if (url === '/api/v1/speech/status')
                    return json({ available: true });
                if (url === '/api/v1/transcribe')
                    return json({ text: 'what is the weather in delhi' });
                if (url === '/api/v1/agent/chat')
                    return json({ reply: REPLY });
                if (url === '/api/v1/speech') {
                    // Four seconds, so there is time to talk over it. Silence
                    // is fine: what matters is that it plays and can be cut.
                    const samples = 44100 * 4;
                    const wav = Buffer.alloc(44 + samples * 2);
                    wav.write('RIFF', 0); wav.writeUInt32LE(36 + samples * 2, 4);
                    wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16);
                    wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
                    wav.writeUInt32LE(44100, 24); wav.writeUInt32LE(88200, 28);
                    wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
                    wav.write('data', 36); wav.writeUInt32LE(samples * 2, 40);
                    res.writeHead(200, { 'Content-Type': 'audio/wav',
                                         'Access-Control-Allow-Origin': '*' });
                    return res.end(wav);
                }
                return json({ error: 'no stub for ' + url }, 404);
            });
            return;
        }

        const name = url === '/' ? '/index.html' : url;
        const file = path.join(GUI, path.basename(name));
        fs.readFile(file, function (err, data) {
            if (err) { res.writeHead(404); return res.end('not found'); }
            res.writeHead(200, {
                'Content-Type': types[path.extname(file)] || 'text/plain',
            });
            res.end(data);
        });
    });
    return new Promise(function (resolve) {
        server.listen(0, '127.0.0.1', function () {
            resolve({ server: server, port: server.address().port });
        });
    });
}

async function main() {
    console.log('Conversation mode, in a browser');
    console.log('='.repeat(60));

    const pw = loadPlaywright();
    if (!pw) {
        console.log('  SKIP playwright-core is not installed');
        console.log('       npm install playwright-core, then re-run');
        return 0;
    }

    const calls = [];
    const { server, port } = await startStubServer(calls);
    const base = 'http://127.0.0.1:' + port;

    // The bundled browser build may not match this playwright-core; point it at
    // whatever Chromium the machine actually has.
    const exe = process.env.GATHM_CHROMIUM ||
        ['/opt/pw-browsers/chromium',
         '/usr/bin/chromium', '/usr/bin/chromium-browser',
         '/usr/bin/google-chrome'].find(function (p) {
            try { return fs.existsSync(p); } catch (_) { return false; }
        });

    const browser = await pw.chromium.launch({
        // headless:false plus --headless=new: old headless has no fake audio
        // device at all, and playwright adds the old flag when headless:true.
        headless: false,
        executablePath: exe || undefined,
        args: [
            '--headless=new',
            '--no-sandbox',
            '--use-fake-ui-for-media-stream',
            '--autoplay-policy=no-user-gesture-required',
        ],
    });

    const errors = [];
    try {
        const page = await browser.newPage();
        page.on('pageerror', function (e) { errors.push(String(e)); });
        page.on('console', function (m) {
            // Failed loads of the CDN icon font/script are expected offline and
            // are not what this test is about; real script errors are.
            if (m.type() === 'error' &&
                m.text().indexOf('Failed to load resource') === -1) {
                errors.push(m.text());
            }
        });

        // The synthetic microphone. speak(true) turns the tone up to something
        // the endpointer will treat as a voice; speak(false) returns to a level
        // that stands in for a quiet room.
        await page.addInitScript(function () {
            const QUIET = 0.0008, LOUD = 0.25;
            navigator.mediaDevices.getUserMedia = function () {
                const ctx = new (window.AudioContext || window.webkitAudioContext)();
                const dest = ctx.createMediaStreamDestination();
                const gain = ctx.createGain();
                gain.gain.value = QUIET;
                // Two detuned tones plus a little wobble: broadband enough to
                // look like a voice to an energy detector.
                [180, 320].forEach(function (hz) {
                    const osc = ctx.createOscillator();
                    osc.frequency.value = hz;
                    osc.connect(gain);
                    osc.start();
                });
                gain.connect(dest);
                window.__mic = {
                    ctx: ctx,
                    speak: function (on) { gain.gain.value = on ? LOUD : QUIET; },
                };
                return Promise.resolve(dest.stream);
            };
        });

        await page.goto(base + '/', { waitUntil: 'load' });
        await page.waitForFunction('typeof GathmVAD !== "undefined"',
                                   null, { timeout: 5000 });
        ok('the endpointer module loads in the page', true);

        // The conversation button appears once the server reports ASR.
        await page.waitForSelector('#convoBtn:not([hidden])', { timeout: 5000 });
        ok('the conversation button is offered when ASR is available', true);

        await page.click('#convoBtn');
        await page.waitForFunction('window.__mic && typeof convoMode !== "undefined" && convoMode',
                                   null, { timeout: 5000 });
        ok('the conversation is live and the mic is open', true);

        // Let the endpointer measure the room, then say something, then stop.
        // Nothing is clicked from here on: this is the whole point of the mode.
        await page.waitForTimeout(900);
        await page.evaluate(function () { window.__mic.speak(true); });
        await page.waitForTimeout(1500);
        await page.evaluate(function () { window.__mic.speak(false); });
        // Watch for the first moment audio is playing, to compare against the
        // server-side timestamps of the speech requests.
        let startedPlayingAt = 0;
        let requestsWhenPlaybackBegan = -1;
        const watchPlayback = (async function () {
            for (let i = 0; i < 400; i++) {
                const playing = await page.evaluate(function () {
                    return typeof currentAudio !== 'undefined' && currentAudio !== null;
                }).catch(function () { return false; });
                if (playing) {
                    startedPlayingAt = Date.now();
                    requestsWhenPlaybackBegan = calls.filter(function (c) {
                        return c.url === '/api/v1/speech';
                    }).length;
                    return;
                }
                await new Promise(function (r) { setTimeout(r, 50); });
            }
        })();

        const replied = await page.waitForFunction(function () {
            return document.body.innerText.indexOf('thirty two degrees') !== -1;
        }, null, { timeout: 25000 }).then(function () { return true; },
                                          function () { return false; });
        if (!replied) {
            // Say what the page was doing, rather than just "timed out".
            const diag = await page.evaluate(function () {
                return {
                    status: (document.getElementById('botStatus') || {}).textContent,
                    convoActive: !!(document.getElementById('convoBtn') || {})
                                    .classList.contains('active'),
                    voiceActive: (typeof voiceActive !== 'undefined') ? voiceActive : 'n/a',
                    convoMode: (typeof convoMode !== 'undefined') ? convoMode : 'n/a',
                    hasRecorder: (typeof recorderNode !== 'undefined') ? !!recorderNode : 'n/a',
                    vadState: (typeof convoVad !== 'undefined' && convoVad)
                                ? convoVad.state : 'n/a',
                    vadFloor: (typeof convoVad !== 'undefined' && convoVad)
                                ? convoVad.floor : 'n/a',
                    vadThreshold: (typeof convoVad !== 'undefined' && convoVad)
                                ? convoVad.threshold : 'n/a',
                    lastLevel: (typeof window.__lastLevel !== 'undefined')
                                ? window.__lastLevel : 'n/a',
                    frames: (typeof window.__frames !== 'undefined')
                                ? window.__frames : 'n/a',
                };
            }).catch(function (e) { return { evalError: String(e) }; });
            console.log('       page state: ' + JSON.stringify(diag));
            console.log('       api calls:  ' +
                        JSON.stringify(calls.map(function (c) { return c.url; })));
            console.log('       page errors: ' + JSON.stringify(errors.slice(0, 5)));
        }
        ok('a reply arrived without anything being pressed', replied);

        const urls = calls.map(function (c) { return c.url; });
        ok('audio was transcribed',
           urls.indexOf('/api/v1/transcribe') !== -1);
        ok('the transcript was sent to the agent',
           urls.indexOf('/api/v1/agent/chat') !== -1);
        ok('the reply was spoken', urls.indexOf('/api/v1/speech') !== -1);

        const audio = calls.filter(function (c) {
            return c.url === '/api/v1/transcribe';
        });
        ok('the captured turn carried real audio, not an empty buffer',
           audio.length > 0 && audio[0].bytes > 8000);
        ok('transcribe ran before chat',
           urls.indexOf('/api/v1/transcribe') < urls.indexOf('/api/v1/agent/chat'));

        // The user's words land in the transcript as their own message.
        const said = await page.evaluate(function () {
            return document.body.innerText.indexOf('weather in delhi') !== -1;
        });
        ok('what was said appears in the chat', said);

        await watchPlayback;

        // ── the reply is spoken sentence by sentence ────────────────────
        // Asking the server for the whole answer means waiting for the whole
        // answer to be rendered before hearing anything.
        const speechCalls = calls.filter(function (c) {
            return c.url === '/api/v1/speech';
        });
        ok('the reply was split into several requests, not one',
           speechCalls.length > 1);
        check('the first request is only the first sentence',
              speechCalls[0].text, FIRST_SENTENCE);
        ok('every request is shorter than the whole reply',
           speechCalls.every(function (c) { return c.text.length < REPLY.length; }));
        ok('what has been requested so far is a prefix of the reply',
           REPLY.replace(/\s+/g, ' ').trim().indexOf(
               speechCalls.map(function (c) { return c.text; }).join(' ')
                   .replace(/\s+/g, ' ').trim()) === 0);

        // The property this exists for: audio starts before the whole reply has
        // been rendered. Counted at the instant playback began, because the
        // later requests are issued as it plays — comparing against the final
        // total would be a race, and comparing timestamps proves nothing
        // against a stub that renders instantly.
        const expectedChunks = splitSpeech(REPLY).length;
        ok('the reply needs more than one request (' + expectedChunks + ')',
           expectedChunks > 1);
        ok('playback began before the whole reply was rendered (' +
           requestsWhenPlaybackBegan + ' of ' + expectedChunks + ' requested)',
           requestsWhenPlaybackBegan > 0 &&
           requestsWhenPlaybackBegan < expectedChunks);

        // ── barge-in ────────────────────────────────────────────────────
        // Talk over the reply: it should stop mid-sentence and listen instead.
        // speakingActive, not currentAudio: between sentences there is a
        // moment with no clip loaded, and that is still "Gathm is talking".
        const playing = await page.waitForFunction(
            'typeof speakingActive !== "undefined" && speakingActive',
            null, { timeout: 10000 }).then(function () { return true; },
                                           function () { return false; });
        ok('the reply is being played', playing);

        const before = calls.filter(function (c) {
            return c.url === '/api/v1/transcribe';
        }).length;

        await page.evaluate(function () { window.__mic.speak(true); });
        const cut = await page.waitForFunction(
            'speakingActive === false && currentAudio === null',
            null, { timeout: 8000 }).then(function () { return true; },
                                          function () { return false; });
        ok('speaking over the reply stops it', cut);
        await page.waitForTimeout(1200);
        await page.evaluate(function () { window.__mic.speak(false); });

        // The interruption becomes its own turn, which means a second
        // transcription request without anything being clicked.
        let grew = before;
        for (let i = 0; i < 40 && grew === before; i++) {
            await new Promise(function (r) { setTimeout(r, 250); });
            grew = calls.filter(function (c) {
                return c.url === '/api/v1/transcribe';
            }).length;
        }
        ok('the interruption became a new turn (' + before + ' -> ' + grew + ')',
           grew > before);

        // And it goes back to listening rather than stopping after one turn.
        await page.waitForFunction(function () {
            const s = document.getElementById('botStatus');
            return s && /listening/i.test(s.textContent);
        }, null, { timeout: 15000 });
        ok('it returns to listening after answering', true);

        // Stopping releases the microphone.
        await page.click('#convoBtn');
        const stopped = await page.waitForFunction(function () {
            const b = document.getElementById('convoBtn');
            return b && !b.classList.contains('active');
        }, null, { timeout: 5000 }).then(function () { return true; },
                                          function () { return false; });
        ok('stopping ends the conversation', stopped);

        check('no page errors', errors, []);
    } finally {
        await browser.close();
        server.close();
    }

    console.log('='.repeat(60));
    console.log(PASS + ' passed, ' + FAIL + ' failed');
    return FAIL ? 1 : 0;
}

main().then(function (code) { process.exit(code); },
            function (err) {
                console.error('harness error: ' + (err && err.stack || err));
                process.exit(1);
            });
