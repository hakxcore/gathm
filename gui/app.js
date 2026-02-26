// Initialize Lucide icons
lucide.createIcons();

// Update clock
function updateClock() {
    const now = new Date();
    let hours = now.getHours();
    let minutes = now.getMinutes();
    const ampm = hours >= 12 ? 'PM' : 'AM';
    hours = hours % 12;
    hours = hours ? hours : 12;
    minutes = minutes < 10 ? '0' + minutes : minutes;
    document.getElementById('clock').textContent = hours + ':' + minutes;
}
setInterval(updateClock, 1000);
updateClock();

// Scroll to bottom of chat
function scrollToBottom() {
    const scrollArea = document.getElementById('chatScroll');
    // Ensure padding inside the scroll takes input area into account (it's absolute positioned).
    scrollArea.scrollTop = scrollArea.scrollHeight;
}

// Adjust padding of chatArea so it's not hidden behind absolute input-area
document.addEventListener("DOMContentLoaded", () => {
    const inputAreaHeight = document.querySelector('.input-area').offsetHeight;
    document.getElementById('chatArea').style.paddingBottom = `${inputAreaHeight + 20}px`;
    scrollToBottom();
});

// Handling Send Button logic
const messageInput = document.getElementById('messageInput');
const sendBtn = document.getElementById('sendBtn');
const chatArea = document.getElementById('chatArea');

function addMessage(text, sender) {
    const now = new Date();
    let hours = now.getHours();
    let minutes = now.getMinutes();
    const ampm = hours >= 12 ? 'PM' : 'AM';
    hours = hours % 12;
    hours = hours ? hours : 12;
    minutes = minutes < 10 ? '0' + minutes : minutes;
    const timeString = `${hours}:${minutes} ${ampm}`;

    const messageWrapper = document.createElement('div');
    messageWrapper.className = `message-wrapper ${sender}`;

    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}-text`;
    
    const p = document.createElement('p');
    p.textContent = text;
    messageDiv.appendChild(p);
    
    messageWrapper.appendChild(messageDiv);
    
    const timeDiv = document.createElement('div');
    timeDiv.className = `message-time ${sender}-time`;
    timeDiv.textContent = timeString;

    chatArea.appendChild(messageWrapper);
    chatArea.appendChild(timeDiv);
    scrollToBottom();

    // Re-initialize Lucide icons in case we added new icons
    lucide.createIcons();
}

async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text) return;
    
    // Add User message
    addMessage(text, 'user');
    messageInput.value = '';
    
    // Mock response from Gathm AI (We can connect to ../api/server.py here in a real scenario)
    setTimeout(() => {
        addMessage(`I received your message: "${text}". I am the Gathm AI Agent.`, 'bot');
    }, 1000);
}

sendBtn.addEventListener('click', sendMessage);
messageInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        sendMessage();
    }
});
