/**
 * Tests for the conversation endpointer (gui/vad.js).
 *
 * Frames are synthesised, so this needs no microphone and no browser:
 *   node tests/vad_test.js
 */
'use strict';

const path = require('path');
const { VAD, rmsOf } = require(path.join(__dirname, '..', 'gui', 'vad.js'));

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

const FRAME_MS = 25;   // roughly a 1024-sample frame at 44.1 kHz

/**
 * Drive the VAD through a script of [loudness, milliseconds] pairs and collect
 * the events, each tagged with the time it fired.
 */
function run(vad, script, opts) {
    const events = [];
    let now = (opts && opts.startAt) || 0;
    const speaking = (opts && opts.speaking) || function () { return false; };
    for (const [level, ms] of script) {
        for (let t = 0; t < ms; t += FRAME_MS) {
            const ev = vad.push(level, now, speaking(now));
            if (ev) events.push([ev, now]);
            now += FRAME_MS;
        }
    }
    return { events, now, names: events.map(e => e[0]) };
}

const QUIET = 0.002;
const SPEECH = 0.08;

function test_rms() {
    console.log('\nrmsOf');
    check('silence is zero', rmsOf(new Float32Array(128)), 0);
    const ones = new Float32Array(64).fill(1);
    check('full scale is one', rmsOf(ones), 1);
    const half = new Float32Array(64).fill(0.5);
    ok('half amplitude is about 0.5', Math.abs(rmsOf(half) - 0.5) < 1e-9);
    check('empty frame does not divide by zero', rmsOf(new Float32Array(0)), 0);
}

function test_basic_turn() {
    console.log('\none spoken turn, start to finish');
    const vad = new VAD();
    const r = run(vad, [
        [QUIET, 600],      // calibration plus a little quiet
        [SPEECH, 1500],    // someone speaks
        [QUIET, 1500],     // and stops
    ]);
    check('a start and an end, once each', r.names, ['start', 'end']);

    const [, startAt] = r.events[0];
    const [, endAt] = r.events[1];
    ok('the turn opens shortly after speech begins',
       startAt >= 600 && startAt <= 600 + 200);
    ok('the turn closes about silenceMs after speech stops',
       endAt >= 2100 + 800 && endAt <= 2100 + 1100);
}

function test_pause_mid_sentence() {
    console.log('\na pause for breath does not end the turn');
    const vad = new VAD();
    const r = run(vad, [
        [QUIET, 600],
        [SPEECH, 800],
        [QUIET, 400],      // a breath: shorter than silenceMs
        [SPEECH, 800],
        [QUIET, 1500],
    ]);
    check('still one turn, not two', r.names, ['start', 'end']);
}

function test_short_noise_discarded() {
    console.log('\na cough is not a sentence');
    const vad = new VAD();
    const r = run(vad, [
        [QUIET, 600],
        [SPEECH, 200],     // long enough to open, too short to keep
        [QUIET, 1500],
    ]);
    check('opened then discarded', r.names, ['start', 'discard']);
}

function test_blip_never_opens() {
    console.log('\na single loud frame never opens a turn');
    const vad = new VAD();
    const r = run(vad, [
        [QUIET, 600],
        [SPEECH, 25],      // one frame — below startMs
        [QUIET, 1500],
    ]);
    check('nothing fired', r.names, []);
}

function test_noisy_room() {
    console.log('\na noisy room raises the bar instead of firing constantly');
    const NOISE = 0.02;                 // ten times the quiet room
    const vad = new VAD();
    const r = run(vad, [
        [NOISE, 600],                   // calibrate against the noise itself
        [NOISE, 2000],                  // ...and keep hearing it
    ]);
    check('room noise alone is not speech', r.names, []);
    ok('the threshold sits above the noise floor', vad.threshold > NOISE);

    // Speech still gets through, because it is well above that floor.
    const r2 = run(vad, [[NOISE * 6, 1000], [NOISE, 1500]], { startAt: r.now });
    check('speech over the noise still registers', r2.names, ['start', 'end']);
}

function test_quiet_room_floor() {
    console.log('\na silent room does not make everything speech');
    const vad = new VAD();
    // Near-perfect silence: the measured floor is ~0, so a pure multiple would
    // make any breath "speech". floorMin is what stops that.
    const r = run(vad, [
        [0.0000001, 600],
        [0.003, 2000],       // a whisper of room tone, below floorMin
    ]);
    check('room tone under the absolute floor stays quiet', r.names, []);
}

function test_barge_in_threshold() {
    console.log('\nbarge-in: a higher bar while our own reply is playing');
    const vad = new VAD();
    run(vad, [[QUIET, 600]]);
    const quietBar = vad.thresholdFor(false);
    const speakingBar = vad.thresholdFor(true);
    ok('the bar is higher while we are talking', speakingBar > quietBar);

    // Echo leaking back at a moderate level must not interrupt the reply...
    const leak = quietBar * 2;
    ok('leak is loud enough to trigger normally', leak > quietBar);
    const echo = run(new VADAt(vad.floor), [[leak, 1500]], {
        speaking: function () { return true; },
    });
    check('echo does not interrupt the reply', echo.names, []);

    // ...but the user actually speaking up does.
    const loud = speakingBar * 2;
    const real = run(new VADAt(vad.floor), [[loud, 1500], [QUIET, 1500]], {
        speaking: function (t) { return t < 1500; },
    });
    check('a real interruption gets through', real.names, ['start', 'end']);
}

/** A VAD that skips calibration, standing in for one already warmed up. */
function VADAt(floor) {
    const v = new VAD();
    v.state = 'idle';
    v.floor = floor;
    v.floorSeen = 100;
    return v;
}

function test_max_turn() {
    console.log('\nan endless turn is cut off rather than buffered forever');
    const vad = new VAD({ maxTurnMs: 2000 });
    const r = run(vad, [
        [QUIET, 600],
        [SPEECH, 5000],
    ]);
    check('the first turn started then timed out',
          r.names.slice(0, 2), ['start', 'timeout']);
    const [, at] = r.events[1];
    ok('cut off at about maxTurnMs', at >= 2600 && at <= 3000);
    // Someone still talking after the cut-off gets a NEW turn rather than
    // silence: five seconds of speech becomes segments, not one lost minute.
    ok('speech continuing past the cut-off opens another turn',
       r.names.filter(n => n === 'start').length > 1);
    ok('every segment is a start/timeout pair, in order',
       r.names.every((n, i) => n === (i % 2 === 0 ? 'start' : 'timeout')));
}

function test_back_to_back_turns() {
    console.log('\ntwo turns in a row both register');
    const vad = new VAD();
    const r = run(vad, [
        [QUIET, 600],
        [SPEECH, 1000], [QUIET, 1500],
        [SPEECH, 1000], [QUIET, 1500],
    ]);
    check('start, end, start, end', r.names, ['start', 'end', 'start', 'end']);
}

function test_rearm_keeps_the_room() {
    console.log('\nrearm keeps the measured floor; reset would not');
    const NOISE = 0.02;
    const vad = new VAD();
    run(vad, [[NOISE, 600]]);              // learn a noisy room
    const learned = vad.floor;
    ok('a floor was learned', learned > NOISE * 0.5);

    // Mid-turn, as it would be during a barge-in.
    run(vad, [[NOISE * 8, 300]]);
    ok('in a turn', vad.state === 'speech');

    vad.rearm();
    check('ready for the next turn', vad.state, 'idle');
    ok('the room is remembered', Math.abs(vad.floor - learned) < 1e-9);

    // The very next thing said is heard, with no calibration window first.
    const r = run(vad, [[NOISE * 8, 700], [NOISE, 1500]], { startAt: 5000 });
    check('speech right after a rearm still registers', r.names, ['start', 'end']);

    // reset() is the wrong tool here, and this is why: calibrating against
    // speech raises the floor to speech level.
    const other = new VAD();
    run(other, [[NOISE, 600]]);
    other.reset(0);
    run(other, [[NOISE * 8, 600]]);        // "calibrates" against a voice
    ok('reset mid-speech poisons the floor', other.floor > learned * 4);
}

function test_reset() {
    console.log('\nreset returns it to a cold start');
    const vad = new VAD();
    run(vad, [[QUIET, 600], [SPEECH, 1000]]);
    ok('mid-turn before reset', vad.state === 'speech');
    vad.reset(0);
    check('back to calibrating', vad.state, 'calibrating');
    const r = run(vad, [[SPEECH, 200]]);
    check('calibration silences the first frames', r.names, []);
}

console.log('Conversation endpointer tests');
console.log('='.repeat(60));
test_rms();
test_basic_turn();
test_pause_mid_sentence();
test_short_noise_discarded();
test_blip_never_opens();
test_noisy_room();
test_quiet_room_floor();
test_barge_in_threshold();
test_max_turn();
test_back_to_back_turns();
test_rearm_keeps_the_room();
test_reset();
console.log('='.repeat(60));
console.log(PASS + ' passed, ' + FAIL + ' failed');
process.exit(FAIL ? 1 : 0);
