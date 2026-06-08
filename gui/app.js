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
// /agent/ask now executes the matched tool and returns {status, output}.
// Translate all response shapes into plain human-readable text.
function formatAgentReply(data) {
    // Tool ran successfully
    if (data.status === 'success' && data.output) {
        return data.output;
    }

    // Tool ran but errored
    if (data.status === 'error') {
        const msg = data.error || data.output || 'Tool returned an error.';
        return (data.tool ? '"' + data.tool + '" error: ' : '') + msg;
    }

    // No tool matched -- friendly hint
    if (data.error && /no matching tool/i.test(data.error)) {
        return 'I can run tools for you. Try requests like:\n' +
               '  * weather in Tokyo\n' +
               '  * dns records for github.com\n' +
               '  * ip info 8.8.8.8\n' +
               '  * define serendipity\n' +
               '  * crypto price bitcoin\n' +
               '  * news';
    }

    // Fallback
    return data.raw_output || data.output || data.result || data.error
        || JSON.stringify(data, null, 2);
}

// -- Send via API ----------------------------------------------------------
let isSending = false;

async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text || isSending) return;

    addMessage(text, 'user');
    messageInput.value = '';
    isSending = true;
    sendBtn.disabled = true;
    showTyping();

    try {
        const res = await fetch(API_BASE + '/api/v1/agent/ask', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: text }),
        });

        hideTyping();

        if (!res.ok) {
            const err = await res.json().catch(function() { return {}; });
            addMessage(err.error || 'Server error (' + res.status + ')', 'bot', 'bot-error');
            return;
        }

        const data = await res.json();
        addMessage(formatAgentReply(data), 'bot');

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
