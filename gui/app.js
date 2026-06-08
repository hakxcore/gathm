// Gathm AI -- app.js

const API_BASE = window.GATHM_API_URL || 'http://127.0.0.1:8080';

lucide.createIcons();

// -- Element refs ----------------------------------------------------------
const aiOrb        = document.getElementById('aiOrb');
const mainOrb      = document.getElementById('mainOrb');
const freqBars     = document.getElementById('freqBars');
const botStatus    = document.getElementById('botStatus');
const chatArea     = document.getElementById('chatArea');
const messageInput = document.getElementById('messageInput');
const sendBtn      = document.getElementById('sendBtn');
const micBtn       = document.getElementById('micBtn');

// -- Orb state -------------------------------------------------------------
function setOrbState(state) {
    if (aiOrb) aiOrb.className = 'ai-orb ' + state;
}

// -- Connectivity ----------------------------------------------------------
let isOnline = false;

async function checkConnectivity() {
    isOnline = false;
    for (const p of ['/api/v1/ping', '/api/v1/tools']) {
        try {
            const res = await fetch(API_BASE + p, { signal: AbortSignal.timeout(4000) });
            if (res.ok) { isOnline = true; break; }
        } catch (_) { /* try next */ }
    }
    if (!voiceActive) {
        botStatus.textContent = isOnline ? 'Online - Voice & Text' : 'Offline - API not reachable';
    }
}

checkConnectivity();
setInterval(checkConnectivity, 30000);

// -- Scroll ----------------------------------------------------------------
function scrollToBottom() {
    chatArea.scrollTop = chatArea.scrollHeight;
}

// -- Time ------------------------------------------------------------------
function formatTime() {
    const d = new Date();
    let h = d.getHours(), m = d.getMinutes();
    const ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    return h + ':' + (m < 10 ? '0' + m : m) + ' ' + ampm;
}

// -- Messages --------------------------------------------------------------
function addMessage(text, sender, cssClass) {
    const wrapper = document.createElement('div');
    wrapper.className = 'message-wrapper ' + sender;

    const msg = document.createElement('div');
    msg.className = 'message ' + (cssClass || sender + '-text');

    const p = document.createElement('p');
    p.textContent = text;
    msg.appendChild(p);
    wrapper.appendChild(msg);

    const time = document.createElement('div');
    time.className = 'message-time ' + sender + '-time';
    time.textContent = formatTime();

    chatArea.appendChild(wrapper);
    chatArea.appendChild(time);
    scrollToBottom();
}

// -- Typing indicator ------------------------------------------------------
let typingEl = null;

function showTyping() {
    setOrbState('thinking');
    const wrapper = document.createElement('div');
    wrapper.className = 'message-wrapper bot';
    wrapper.id = 'typingWrapper';
    const indicator = document.createElement('div');
    indicator.className = 'typing-indicator';
    indicator.innerHTML = '<div class="dot"></div><div class="dot"></div><div class="dot"></div>';
    wrapper.appendChild(indicator);
    chatArea.appendChild(wrapper);
    scrollToBottom();
    typingEl = wrapper;
}

function hideTyping() {
    if (typingEl) { typingEl.remove(); typingEl = null; }
}

// -- Format API response ---------------------------------------------------
function formatAgentReply(data) {
    if (data.reply) return data.reply;
    if (data.status === 'success' && data.output) return data.output;
    if (data.matched_tool && data.matched_tool !== 'null') {
        return 'I can help with that using the "' + data.matched_tool + '" tool.' +
               (data.description ? '\n\n' + data.description : '');
    }
    if (data.error && /no matching tool/i.test(data.error)) {
        return "I couldn't find a tool for that. Try: weather in Tokyo, " +
               "dns github.com, ip info 8.8.8.8, define serendipity.";
    }
    return data.raw_output || data.output || data.result || data.error
        || JSON.stringify(data, null, 2);
}

// -- Send via API ----------------------------------------------------------
let isSending = false;
let history = [];
const HISTORY_MAX = 12;

async function sendMessage(text) {
    text = (text || messageInput.value).trim();
    if (!text || isSending) return;

    addMessage(text, 'user');
    messageInput.value = '';
    isSending = true;
    sendBtn.disabled = true;
    showTyping();

    let reply = null;
    try {
        const res = await fetch(API_BASE + '/api/v1/agent/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: text, history: history }),
        });

        hideTyping();

        if (!res.ok) {
            const err = await res.json().catch(function() { return {}; });
            addMessage(err.error || 'Server error (' + res.status + ')', 'bot', 'bot-error');
            return;
        }

        const data = await res.json();
        reply = formatAgentReply(data);
        addMessage(reply, 'bot');

        history.push({ role: 'user', content: text });
        history.push({ role: 'assistant', content: reply });
        if (history.length > HISTORY_MAX * 2) {
            history = history.slice(-HISTORY_MAX * 2);
        }

    } catch (err) {
        hideTyping();
        addMessage(
            isOnline ? 'Connection error: ' + err.message
                     : 'Cannot reach Gathm API. Start the server: gathm-api --port 8080',
            'bot', 'bot-error'
        );
    } finally {
        isSending = false;
        sendBtn.disabled = false;
        if (!voiceActive) messageInput.focus();
    }

    // Speak the reply if voice mode is active or TTS is desired
    if (reply && ttsEnabled) {
        speakReply(reply);
    }
}

sendBtn.addEventListener('click', function() { sendMessage(); });
messageInput.addEventListener('keypress', function(e) {
    if (e.key === 'Enter') sendMessage();
});

// =========================================================================
// Voice mode — mic input → STT → chat agent → TTS output
// =========================================================================

let audioCtx    = null;
let analyser    = null;
let micStream   = null;
let rafId       = null;
let voiceActive = false;
let ttsEnabled  = false;   // set to true when voice mode is started

// MediaRecorder for capturing audio to send to STT
let mediaRec    = null;
let audioChunks = [];

const bars = Array.from(freqBars.querySelectorAll('.fb'));

// -- Mic frequency visualisation ------------------------------------------
function driveFrequency() {
    if (!voiceActive || !analyser) return;

    const data = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteFrequencyData(data);

    const avg = data.reduce(function(s, v) { return s + v; }, 0) / data.length;
    const scale = 1 + (avg / 255) * 0.18;
    mainOrb.style.transform = 'scale(' + scale.toFixed(4) + ')';

    const step = Math.max(1, Math.floor(data.length / bars.length));
    bars.forEach(function(bar, i) {
        const val = data[i * step] || 0;
        const h = 4 + (val / 255) * 34;
        bar.style.height = h.toFixed(1) + 'px';
    });

    rafId = requestAnimationFrame(driveFrequency);
}

// -- TTS playback with orb visualisation ----------------------------------
async function speakReply(text) {
    if (!text || !isOnline) return;

    try {
        const res = await fetch(API_BASE + '/api/v1/voice/speak', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: text }),
        });

        if (!res.ok) return;  // TTS unavailable — silent fallback

        const arrayBuf = await res.arrayBuffer();
        if (!arrayBuf.byteLength) return;

        // Decode and play through Web Audio, driving orb with playback frequency
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const buf = await ctx.decodeAudioData(arrayBuf);

        const source = ctx.createBufferSource();
        const ttsAnalyser = ctx.createAnalyser();
        ttsAnalyser.fftSize = 64;
        ttsAnalyser.smoothingTimeConstant = 0.75;

        source.buffer = buf;
        source.connect(ttsAnalyser);
        ttsAnalyser.connect(ctx.destination);

        setOrbState('speaking');
        aiOrb.setAttribute('data-live', 'true');
        botStatus.textContent = 'Speaking…';

        const ttsData = new Uint8Array(ttsAnalyser.frequencyBinCount);
        const step = Math.max(1, Math.floor(ttsData.length / bars.length));
        let ttsRaf = null;

        function driveTTS() {
            ttsAnalyser.getByteFrequencyData(ttsData);
            const avg = ttsData.reduce(function(s, v) { return s + v; }, 0) / ttsData.length;
            mainOrb.style.transform = 'scale(' + (1 + (avg / 255) * 0.18).toFixed(4) + ')';
            bars.forEach(function(bar, i) {
                const val = ttsData[i * step] || 0;
                bar.style.height = (4 + (val / 255) * 34).toFixed(1) + 'px';
            });
            ttsRaf = requestAnimationFrame(driveTTS);
        }

        source.onended = function() {
            if (ttsRaf) cancelAnimationFrame(ttsRaf);
            mainOrb.style.transform = '';
            bars.forEach(function(b) { b.style.height = ''; });
            aiOrb.removeAttribute('data-live');
            if (voiceActive) {
                setOrbState('speaking');
                botStatus.textContent = 'Listening…';
                startRecording();  // resume listening after speaking
            } else {
                setOrbState('idle');
                checkConnectivity();
            }
            ctx.close();
        };

        source.start();
        driveTTS();

    } catch (_) {
        // TTS errors are non-fatal — the text reply is already shown
        if (voiceActive) startRecording();
    }
}

// -- Recording (STT) -------------------------------------------------------
function startRecording() {
    if (!micStream) return;
    audioChunks = [];

    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
            ? 'audio/webm'
            : '';

    mediaRec = new MediaRecorder(micStream, mimeType ? { mimeType } : {});
    mediaRec.ondataavailable = function(e) {
        if (e.data.size > 0) audioChunks.push(e.data);
    };
    mediaRec.onstop = onRecordingStop;
    mediaRec.start();
    botStatus.textContent = 'Listening…';
}

function stopRecording() {
    if (mediaRec && mediaRec.state !== 'inactive') {
        mediaRec.stop();
    }
}

async function onRecordingStop() {
    if (!audioChunks.length) return;

    const blob = new Blob(audioChunks, { type: 'audio/webm' });
    audioChunks = [];

    if (!voiceActive) return;  // user stopped voice mode mid-recording

    botStatus.textContent = 'Transcribing…';
    setOrbState('thinking');
    mainOrb.style.transform = '';
    bars.forEach(function(b) { b.style.height = ''; });

    try {
        const res = await fetch(API_BASE + '/api/v1/voice/transcribe', {
            method: 'POST',
            headers: { 'Content-Type': 'audio/webm' },
            body: blob,
        });
        const data = await res.json();
        const transcript = (data.text || '').trim();

        if (transcript) {
            // Show what was heard, then send to agent
            messageInput.value = transcript;
            await sendMessage(transcript);
            // speakReply is called inside sendMessage when ttsEnabled
        } else {
            // Nothing heard — just go back to listening
            if (voiceActive) {
                setOrbState('speaking');
                startRecording();
                botStatus.textContent = 'Listening…';
            }
        }
    } catch (_) {
        if (voiceActive) {
            setOrbState('speaking');
            startRecording();
            botStatus.textContent = 'Listening…';
        }
    }
}

// -- Voice mode start / stop ----------------------------------------------
async function startVoice() {
    try {
        micStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    } catch (err) {
        botStatus.textContent = 'Microphone access denied';
        setTimeout(checkConnectivity, 3000);
        return;
    }

    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 64;
    analyser.smoothingTimeConstant = 0.75;

    const src = audioCtx.createMediaStreamSource(micStream);
    src.connect(analyser);

    voiceActive = true;
    ttsEnabled = true;
    aiOrb.setAttribute('data-live', 'true');
    setOrbState('speaking');
    micBtn.classList.add('active');

    driveFrequency();
    startRecording();
}

function stopVoice() {
    voiceActive = false;
    ttsEnabled = false;
    stopRecording();

    if (rafId) cancelAnimationFrame(rafId);
    if (micStream) micStream.getTracks().forEach(function(t) { t.stop(); });
    if (audioCtx) audioCtx.close();
    audioCtx = null; analyser = null; micStream = null; rafId = null;
    mediaRec = null; audioChunks = [];

    mainOrb.style.transform = '';
    bars.forEach(function(b) { b.style.height = ''; });

    aiOrb.removeAttribute('data-live');
    setOrbState('idle');
    micBtn.classList.remove('active');
    checkConnectivity();
}

// Toggle: short tap = record one utterance; hold is automatic via VAD timing
// Simple implementation: click to start, click again to stop + transcribe.
micBtn.addEventListener('click', function() {
    if (voiceActive) {
        // Stop the current recording and transcribe what was captured so far
        stopRecording();
        // stopVoice is called after TTS finishes (in onended) or immediately
        // if user hits mic again before TTS plays
        setTimeout(stopVoice, 200);
    } else {
        startVoice();
    }
});
