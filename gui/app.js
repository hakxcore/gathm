// Gathm AI GUI — app.js
// Connects to the Gathm API server (default: http://127.0.0.1:8080)

const API_BASE = window.GATHM_API_URL || 'http://127.0.0.1:8080';

// Initialize Lucide icons
lucide.createIcons();

// ── Clock ──────────────────────────────────────────────────────────
function updateClock() {
    const now = new Date();
    let hours = now.getHours();
    let minutes = now.getMinutes();
    const ampm = hours >= 12 ? 'PM' : 'AM';
    hours = hours % 12 || 12;
    minutes = minutes < 10 ? '0' + minutes : minutes;
    document.getElementById('clock').textContent = hours + ':' + minutes;
}
setInterval(updateClock, 1000);
updateClock();

// ── Connectivity check ─────────────────────────────────────────────
const onlineDot = document.getElementById('onlineDot');
const botStatus = document.getElementById('botStatus');
let isOnline = false;

async function checkConnectivity() {
    try {
        const res = await fetch(`${API_BASE}/api/v1/health`, {
            method: 'GET',
            signal: AbortSignal.timeout(5000),
        });
        isOnline = res.ok;
    } catch {
        isOnline = false;
    }
    updateStatusUI();
}

function updateStatusUI() {
    if (isOnline) {
        onlineDot.classList.remove('offline');
        botStatus.textContent = 'Online · Voice & Text';
    } else {
        onlineDot.classList.add('offline');
        botStatus.textContent = 'Offline · API not reachable';
    }
}

// Check on load and every 30 seconds
checkConnectivity();
setInterval(checkConnectivity, 30000);

// ── Scroll helper ──────────────────────────────────────────────────
function scrollToBottom() {
    const scrollArea = document.getElementById('chatScroll');
    scrollArea.scrollTop = scrollArea.scrollHeight;
}

// ── Layout padding for fixed input area ────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    const inputAreaHeight = document.querySelector('.input-area').offsetHeight;
    document.getElementById('chatArea').style.paddingBottom = `${inputAreaHeight + 20}px`;
    scrollToBottom();
});

// ── Time formatting ────────────────────────────────────────────────
function formatTime() {
    const now = new Date();
    let hours = now.getHours();
    let minutes = now.getMinutes();
    const ampm = hours >= 12 ? 'PM' : 'AM';
    hours = hours % 12 || 12;
    minutes = minutes < 10 ? '0' + minutes : minutes;
    return `${hours}:${minutes} ${ampm}`;
}

// ── Message rendering ──────────────────────────────────────────────
const messageInput = document.getElementById('messageInput');
const sendBtn = document.getElementById('sendBtn');
const chatArea = document.getElementById('chatArea');

function addMessage(text, sender, cssClass) {
    const messageWrapper = document.createElement('div');
    messageWrapper.className = `message-wrapper ${sender}`;

    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${cssClass || sender + '-text'}`;

    const p = document.createElement('p');
    p.textContent = text;
    messageDiv.appendChild(p);

    messageWrapper.appendChild(messageDiv);

    const timeDiv = document.createElement('div');
    timeDiv.className = `message-time ${sender}-time`;
    timeDiv.textContent = formatTime();

    chatArea.appendChild(messageWrapper);
    chatArea.appendChild(timeDiv);
    scrollToBottom();

    lucide.createIcons();
}

// ── Typing indicator ───────────────────────────────────────────────
let typingEl = null;

function showTyping() {
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
    if (typingEl) {
        typingEl.remove();
        typingEl = null;
    }
}

// ── Send message via API ───────────────────────────────────────────
let isSending = false;

async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text || isSending) return;

    // Add user message
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

        // The agent returns either raw_output or structured response
        const reply = data.raw_output
            || data.output
            || data.result
            || JSON.stringify(data, null, 2);

        addMessage(reply, 'bot');
    } catch (err) {
        hideTyping();
        if (!isOnline) {
            addMessage('Cannot reach the Gathm API. Start the server: gathm-api --port 8080', 'bot', 'bot-error');
        } else {
            addMessage(`Connection error: ${err.message}`, 'bot', 'bot-error');
        }
    } finally {
        isSending = false;
        sendBtn.disabled = false;
        messageInput.focus();
    }
}

sendBtn.addEventListener('click', sendMessage);
messageInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        sendMessage();
    }
});
