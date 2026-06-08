// Gathm AI — app.js

const API_BASE = window.GATHM_API_URL || 'http://127.0.0.1:8080';

lucide.createIcons();

// ── Element refs ───────────────────────────────────────────────
const aiOrb       = document.getElementById('aiOrb');
const mainOrb     = document.getElementById('mainOrb');
const freqBars    = document.getElementById('freqBars');
const botStatus   = document.getElementById('botStatus');
const chatArea    = document.getElementById('chatArea');
const messageInput = document.getElementById('messageInput');
const sendBtn     = document.getElementById('sendBtn');
const micBtn      = document.getElementById('micBtn');

// ── Orb state ──────────────────────────────────────────────────
function setOrbState(state) {
    if (aiOrb) aiOrb.className = `ai-orb ${state}`;
}

// ── Connectivity ───────────────────────────────────────────────
let isOnline = false;

async function checkConnectivity() {
    try {
        const res = await fetch(`${API_BASE}/api/v1/health`, {
            signal: AbortSignal.timeout(5000),
        });
        isOnline = res.ok;
    } catch {
        isOnline = false;
    }
    botStatus.textContent = isOnline ? 'Online · Voice & Text' : 'Offline · API not reachable';
}

checkConnectivity();
setInterval(checkConnectivity, 30000);

// ── Scroll ─────────────────────────────────────────────────────
function scrollToBottom() {
    chatArea.scrollTop = chatArea.scrollHeight;
}

// ── Time ───────────────────────────────────────────────────────
function formatTime() {
    const d = new Date();
    let h = d.getHours(), m = d.getMinutes();
    const ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    return `${h}:${m < 10 ? '0' + m : m} ${ampm}`;
}

// ── Messages ───────────────────────────────────────────────────
function addMessage(text, sender, cssClass) {
    const wrapper = document.createElement('div');
    wrapper.className = `message-wrapper ${sender}`;

    const msg = document.createElement('div');
    msg.className = `message ${cssClass || sender + '-text'}`;

    const p = document.createElement('p');
    p.textContent = text;
    msg.appendChild(p);
    wrapper.appendChild(msg);

    const time = document.createElement('div');
    time.className = `message-time ${sender}-time`;
    time.textContent = formatTime();

    chatArea.appendChild(wrapper);
    chatArea.appendChild(time);
    scrollToBottom();
}

// ── Typing indicator ───────────────────────────────────────────
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
    typingEl?.remove();
    typingEl = null;
}

// ── Send via API ───────────────────────────────────────────────
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
        const res = await fetch(`${API_BASE}/api/v1/agent/ask`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: text }),
        });

        hideTyping();

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            addMessage(err.error || `Server error (${res.status})`, 'bot', 'bot-error');
            return;
        }

        const data = await res.json();
        const reply = data.raw_output || data.output || data.result
            || JSON.stringify(data, null, 2);
        addMessage(reply, 'bot');

    } catch (err) {
        hideTyping();
        addMessage(
            isOnline
                ? `Connection error: ${err.message}`
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
messageInput.addEventListener('keypress', e => { if (e.key === 'Enter') sendMessage(); });

// ══════════════════════════════════════════════════════════════
// Voice mode — Web Audio API drives real frequency visualization
// ══════════════════════════════════════════════════════════════

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
    analyser.fftSize = 64;            // 32 frequency bins
    analyser.smoothingTimeConstant = 0.75;

    const src = audioCtx.createMediaStreamSource(micStream);
    src.connect(analyser);

    voiceActive = true;
    aiOrb.setAttribute('data-live', 'true');
    setOrbState('speaking');
    micBtn.classList.add('active');
    botStatus.textContent = 'Listening…';

    driveFrequency();
}

function stopVoice() {
    voiceActive = false;
    if (rafId) cancelAnimationFrame(rafId);
    micStream?.getTracks().forEach(t => t.stop());
    audioCtx?.close();
    audioCtx = null; analyser = null; micStream = null; rafId = null;

    // Reset live transforms
    mainOrb.style.transform = '';
    bars.forEach(b => { b.style.height = ''; });

    aiOrb.removeAttribute('data-live');
    setOrbState('idle');
    micBtn.classList.remove('active');
    checkConnectivity();
}

function driveFrequency() {
    if (!voiceActive || !analyser) return;

    const data = new Uint8Array(analyser.frequencyBinCount); // 32 values
    analyser.getByteFrequencyData(data);

    // Overall energy → orb scale (1.0 – 1.18)
    const avg = data.reduce((s, v) => s + v, 0) / data.length;
    const scale = 1 + (avg / 255) * 0.18;
    mainOrb.style.transform = `scale(${scale.toFixed(4)})`;

    // Per-band energy → bar heights (4 px – 38 px)
    const step = Math.max(1, Math.floor(data.length / bars.length));
    bars.forEach((bar, i) => {
        const val = data[i * step] ?? 0;
        const h = 4 + (val / 255) * 34;
        bar.style.height = `${h.toFixed(1)}px`;
    });

    rafId = requestAnimationFrame(driveFrequency);
}

micBtn.addEventListener('click', () => {
    if (voiceActive) stopVoice();
    else startVoice();
});
