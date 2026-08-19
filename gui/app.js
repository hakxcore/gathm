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
const speakBtn     = document.getElementById('speakBtn');

// -- Orb state -------------------------------------------------------------
function setOrbState(state) {
    if (aiOrb) aiOrb.className = 'ai-orb ' + state;
}

// -- Connectivity ----------------------------------------------------------
let isOnline = false;

async function checkConnectivity() {
    // Try /ping first (instant). Fall back to /api/v1/tools for older
    // servers that pre-date the /ping endpoint.
    isOnline = false;
    for (const p of ['/api/v1/ping', '/api/v1/tools']) {
        try {
            const res = await fetch(API_BASE + p, { signal: AbortSignal.timeout(4000) });
            if (res.ok) { isOnline = true; break; }
        } catch (_) { /* try next */ }
    }
    botStatus.textContent = isOnline ? 'Online - Voice & Text' : 'Offline - API not reachable';
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
    setOrbState('idle');
    if (typingEl) { typingEl.remove(); typingEl = null; }
}

// -- Format API response ---------------------------------------------------
// The /agent/chat endpoint returns {reply} from the LLM agent. If the agent
// is unavailable the server falls back to the keyword router, so we still
// handle those shapes gracefully.
function formatAgentReply(data) {
    if (data.reply) return data.reply;                    // LLM agent answer
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

// -- Spoken replies --------------------------------------------------------
// The server renders speech with audio.cpp and hands back a WAV; the browser
// plays it. That keeps playback on the user's device — the API process may have
// no audio output at all — and on Termux the browser is the same phone.
let speechAvailable = false;
let speakEnabled    = localStorage.getItem('gathmSpeak') !== '0';
let currentAudio    = null;

async function checkSpeech() {
    try {
        const res = await fetch(API_BASE + '/api/v1/speech/status',
                                { signal: AbortSignal.timeout(4000) });
        speechAvailable = res.ok ? !!(await res.json()).available : false;
    } catch (_) {
        speechAvailable = false;
    }
    updateSpeakBtn();
}

function updateSpeakBtn() {
    if (!speakBtn) return;
    speakBtn.hidden = !speechAvailable;      // no voice runtime → no control
    speakBtn.classList.toggle('active', speakEnabled);
    speakBtn.setAttribute('aria-label', speakEnabled ? 'Mute replies' : 'Speak replies');
    speakBtn.innerHTML = '<i data-lucide="' + (speakEnabled ? 'volume-2' : 'volume-x') +
                         '" class="btn-icon"></i>';
    lucide.createIcons();
}

function stopSpeaking() {
    if (currentAudio) {
        try { currentAudio.pause(); } catch (_) { /* already gone */ }
        currentAudio = null;
    }
    if (!voiceActive) setOrbState('idle');
}

async function speakReply(text) {
    if (!speechAvailable || !speakEnabled || !text) return;
    stopSpeaking();
    try {
        const res = await fetch(API_BASE + '/api/v1/speech', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: text }),
        });
        if (!res.ok) return;                  // silence is the right failure here
        const url = URL.createObjectURL(await res.blob());
        const audio = new Audio(url);
        currentAudio = audio;
        if (!voiceActive) setOrbState('speaking');
        const done = function() {
            URL.revokeObjectURL(url);
            if (currentAudio === audio) currentAudio = null;
            if (!voiceActive) setOrbState('idle');
        };
        audio.onended = done;
        audio.onerror = done;
        await audio.play().catch(done);       // autoplay may need a tap first
    } catch (_) { /* speech is a bonus, never an error path */ }
}

if (speakBtn) {
    speakBtn.addEventListener('click', function() {
        speakEnabled = !speakEnabled;
        localStorage.setItem('gathmSpeak', speakEnabled ? '1' : '0');
        if (!speakEnabled) stopSpeaking();
        updateSpeakBtn();
    });
}

checkSpeech();

// -- Send via API ----------------------------------------------------------
let isSending = false;
let history = [];                 // conversation memory for multi-turn context
const HISTORY_MAX = 12;           // keep the last N turns

async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text || isSending) return;

    stopSpeaking();               // a new question outranks the old answer
    addMessage(text, 'user');
    messageInput.value = '';
    isSending = true;
    sendBtn.disabled = true;
    showTyping();

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
        const reply = formatAgentReply(data);
        addMessage(reply, 'bot');
        speakReply(reply);        // fire-and-forget; text is already on screen

        // Remember this turn so follow-ups have context
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
        messageInput.focus();
    }
}

sendBtn.addEventListener('click', sendMessage);
messageInput.addEventListener('keypress', function(e) {
    if (e.key === 'Enter') sendMessage();
});

// =========================================================================
// Voice mode -- Web Audio API drives real frequency visualization
// =========================================================================

let audioCtx    = null;
let analyser    = null;
let micStream   = null;
let rafId       = null;
let voiceActive = false;

const bars = Array.from(freqBars.querySelectorAll('.fb'));

async function startVoice() {
    stopSpeaking();               // don't record ourselves talking
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
    aiOrb.setAttribute('data-live', 'true');
    setOrbState('speaking');
    micBtn.classList.add('active');
    botStatus.textContent = 'Listening...';

    driveFrequency();
}

function stopVoice() {
    voiceActive = false;
    if (rafId) cancelAnimationFrame(rafId);
    if (micStream) micStream.getTracks().forEach(function(t) { t.stop(); });
    if (audioCtx) audioCtx.close();
    audioCtx = null; analyser = null; micStream = null; rafId = null;

    mainOrb.style.transform = '';
    bars.forEach(function(b) { b.style.height = ''; });

    aiOrb.removeAttribute('data-live');
    setOrbState('idle');
    micBtn.classList.remove('active');
    checkConnectivity();
}

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

micBtn.addEventListener('click', function() {
    if (voiceActive) stopVoice();
    else startVoice();
});
