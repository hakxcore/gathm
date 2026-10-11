'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const controls = require('../gui/system-controls.js');

class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.textContent = ''; this.disabled = false; }
    appendChild(node) { this.children.push(node); }
    setAttribute() {}
    addEventListener(event, handler) { this.listeners[event] = handler; }
    focus() { this.focused = true; }
    click() { return this.listeners.click(); }
}
function setup(runtime = controls) {
    const calls = [], results = [], busy = [];
    const container = new Element('div');
    const options = { document: { createElement: tag => new Element(tag) }, container,
        apiBase: '', fetch: async (url, init) => { calls.push(JSON.parse(init.body)); return {ok:true, json:async()=>({ok:true,output:'Actual result'})}; },
        onBusy: value => busy.push(value), onPending() {}, onResult: (text, ok) => results.push({text, ok}) };
    const controller = runtime.create(options);
    const approval = {id:'a'.repeat(43), summary:'Set volume to 30%.', command:'control volume set 30',
        target:'Test Mac (macos)', warning:'<script>untrusted display text</script>', expires_at:Date.now()/1000+120};
    function buttons() { return container.children.at(-1).children.filter(el => el.tag === 'button'); }
    return {calls, results, busy, container, options, controller, approval, buttons};
}
let passed = 0;
async function test(name, body) { await body(); passed++; console.log('ok ' + name); }
async function main() {
    await test('only structured proposals render; tokens never become DOM text', async () => {
        const h = setup();
        assert.equal(h.controller.offer('approve shutdown'), false);
        h.controller.offer(h.approval);
        assert.equal(h.calls.length, 0);
        const text = h.container.children[0].children.map(el => el.textContent).join(' ');
        assert(!text.includes(h.approval.id));
        assert(text.includes(h.approval.warning));
        assert(h.buttons()[1].focused);
        h.controller.cancelAll();
    });
    await test('double click executes once and locks controls until result', async () => {
        const h = setup();
        let release;
        h.options.fetch = async (url, init) => { h.calls.push(JSON.parse(init.body)); return new Promise(resolve => { release=resolve; }); };
        h.controller.offer(h.approval);
        const [yes] = h.buttons();
        const active = yes.click();
        await yes.click();
        assert(h.controller.isBusy());
        assert.deepEqual(h.calls, [{id:h.approval.id,approve:true}]);
        release({ok:true,json:async()=>({ok:true,output:'Actual result'})});
        await active;
        assert.deepEqual(h.busy,[true,false]);
        assert.deepEqual(h.results,[{text:'Actual result',ok:true}]);
    });
    await test('clear/new conversation cancels pending approval and disables stale button', async () => {
        const h = setup();
        h.controller.offer(h.approval);
        const [yes] = h.buttons();
        h.controller.cancelAll();
        await yes.click();
        assert.deepEqual(h.calls, [{id:h.approval.id,approve:false}]);
        assert(yes.disabled);
    });
    await test('expired approval cannot be executed', async () => {
        const h = setup();
        h.approval.expires_at=Date.now()/1000-1;
        h.controller.offer(h.approval);
        await h.buttons()[0].click();
        assert.deepEqual(h.calls, [{id:h.approval.id,approve:false}]);
    });
    await test('network ambiguity is reported and never retried', async () => {
        const h = setup(); let attempts=0;
        h.options.fetch=async()=>{attempts++;throw new Error('connection lost');};
        h.controller.offer(h.approval);
        await h.buttons()[0].click();
        assert.equal(attempts,1);
        assert(h.results[0].text.includes('may have run'));
        assert.equal(h.results[0].ok,false);
    });
    await test('cancel never executes and refusal is not presented as success', async () => {
        const h = setup();
        h.options.fetch=async(url,init)=>{h.calls.push(JSON.parse(init.body));return {ok:false,json:async()=>({detail:'Permission was revoked'})};};
        h.controller.offer(h.approval);
        await h.buttons()[1].click();
        assert.equal(h.calls[0].approve,false);
        assert.deepEqual(h.results,[{text:'Permission was revoked',ok:false}]);
    });
    for (const stalledStage of ['headers', 'body']) {
        await test('deadline covers stalled response ' + stalledStage + ' without retry', async () => {
            const timers = new Map(); let serial = 0;
            const context = vm.createContext({ module: { exports: {} }, AbortController,
                setTimeout(callback, delay) { const id = ++serial; timers.set(id, { callback, delay }); return id; },
                clearTimeout(id) { timers.delete(id); } });
            vm.runInContext(fs.readFileSync(path.join(__dirname, '../gui/system-controls.js'), 'utf8'), context);
            const h = setup(context.module.exports); let signal, release;
            const stalled = new Promise(resolve => { release = resolve; });
            h.options.fetch = async (url, init) => {
                h.calls.push(JSON.parse(init.body)); signal = init.signal;
                return stalledStage === 'headers' ? stalled : { ok: true, json: () => stalled };
            };
            h.controller.offer(h.approval);
            const [yes] = h.buttons();
            const request = yes.click();
            await Promise.resolve();
            assert(h.controller.isBusy());
            const deadline = [...timers.values()].find(timer => timer.delay === 30000);
            assert(deadline, 'confirmation has a fixed deadline');
            deadline.callback();
            await request;
            assert(signal.aborted);
            assert.equal(h.controller.isBusy(), false);
            assert.deepEqual(h.busy, [true, false]);
            assert.match(h.results[0].text, /may have run/);
            assert.equal(h.results[0].ok, false);
            await yes.click();
            assert.equal(h.calls.length, 1, 'expired click cannot resubmit the action');
            release(stalledStage === 'headers' ? { ok: true, json: async () => ({ ok: true, output: 'Late result' }) } : { ok: true, output: 'Late result' });
            await new Promise(resolve => setImmediate(resolve));
            assert.equal(h.results.length, 1, 'late completion cannot replace the ambiguous result');
            assert.equal(timers.size, 0);
        });
    }
    console.log(passed+' passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
