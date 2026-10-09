/* Human approval cards. Tokens live only in memory, never in model history. */
(function (root) {
    'use strict';
    function create(options) {
        var pending = new Set(), busy = false;
        async function send(id, approve) {
            var controller = new AbortController(), timer;
            // Include the response body in the deadline: a server can send
            // headers and then stall. A timed-out action is never replayed.
            var deadline = new Promise(function (_, reject) {
                timer = setTimeout(function () {
                    controller.abort();
                    reject(new Error('System confirmation timed out'));
                }, 30000);
            });
            try {
                return await Promise.race([deadline, (async function () {
                    var response = await options.fetch(options.apiBase + '/api/v1/system/confirm', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ id: id, approve: approve }), signal: controller.signal
                    });
                    return { ok: response.ok, data: await response.json() };
                }())]);
            } finally {
                clearTimeout(timer);
            }
        }
        function offer(approval) {
            if (!approval || typeof approval.id !== 'string' || !/^[\w-]{43}$/.test(approval.id) ||
                typeof approval.summary !== 'string' || !Number.isFinite(approval.expires_at)) return false;
            cancelAll();
            var card = options.document.createElement('section');
            card.className = 'system-approval';
            card.setAttribute('aria-label', 'Confirm system action');
            function text(tag, value, className) {
                var el = options.document.createElement(tag);
                el.textContent = String(value || '');
                if (className) el.className = className;
                card.appendChild(el);
                return el;
            }
            text('h3', 'Confirm system action');
            text('p', approval.target || 'Device running Gathm', 'approval-target');
            text('p', approval.summary);
            if (approval.command) text('code', approval.command);
            if (approval.warning) text('p', approval.warning, 'approval-warning');
            var status = text('p', 'Nothing has run yet. Approval expires in two minutes.', 'approval-status');
            status.setAttribute('role', 'status');
            var yes = text('button', 'Approve once'), no = text('button', 'Cancel');
            yes.type = no.type = 'button';
            yes.className = 'approval-yes';
            var active = true, timer;
            function retire(message) {
                active = false;
                yes.disabled = no.disabled = true;
                clearTimeout(timer);
                pending.delete(cancel);
                status.textContent = message;
            }
            function cancel() {
                if (!active) return;
                retire('Cancelled. This action will not run.');
                // Invalidate the server record too; no retry on network failure.
                send(approval.id, false).catch(function () {});
            }
            async function decide(approve) {
                if (!active || busy) return;
                if (Date.now() / 1000 >= approval.expires_at) {
                    cancel();
                    status.textContent = 'Expired. Ask Gathm again to propose a new action.';
                    return;
                }
                retire(approve ? 'Requesting the action…' : 'Cancelling…');
                busy = true;
                options.onBusy(true);
                try {
                    var response = await send(approval.id, approve);
                    var data = response.data;
                    var output = response.ok && typeof data.output === 'string' ? data.output :
                        (typeof data.detail === 'string' ? data.detail : 'The system action was refused.');
                    status.textContent = output;
                    options.onResult(output, response.ok && data.ok === true);
                } catch (_) {
                    var message = 'Connection ended before the result was confirmed. The action may have run; check the device before requesting it again.';
                    status.textContent = message;
                    options.onResult(message, false);
                } finally {
                    busy = false;
                    options.onBusy(false);
                }
            }
            yes.addEventListener('click', function () { return decide(true); });
            no.addEventListener('click', function () { return decide(false); });
            pending.add(cancel);
            timer = setTimeout(function () {
                cancel();
                status.textContent = 'Expired. Ask Gathm again to propose a new action.';
            }, Math.max(0, Math.min(120000, approval.expires_at * 1000 - Date.now())));
            options.container.appendChild(card);
            options.onPending();
            no.focus(); // default focus is cancel, never approval
            return true;
        }
        function cancelAll() { if (!busy) Array.from(pending).forEach(function (cancel) { cancel(); }); }
        return { offer: offer, cancelAll: cancelAll, isBusy: function () { return busy; } };
    }
    var api = { create: create };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.GathmControls = api;
}(typeof self !== 'undefined' ? self : this));
