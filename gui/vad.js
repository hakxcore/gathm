/**
 * Endpointing for hands-free conversation.
 *
 * The browser hands us audio frames; this decides where one spoken turn starts
 * and stops so the user never has to press anything. Kept free of DOM and Web
 * Audio on purpose — it takes a loudness number and a timestamp, returns an
 * event — which is what makes it testable without a microphone (see
 * tests/vad_test.js).
 *
 * Events returned from push():
 *   ''          nothing changed
 *   'start'     a turn began — start keeping audio (and stop talking over them)
 *   'end'       the turn finished — send what was captured
 *   'discard'   too short to be speech; a cough, a door, a keyboard
 *   'timeout'   the turn ran past maxTurnMs, so it is being cut off and sent
 *
 * The noise floor is measured rather than assumed: a quiet room and a café have
 * wildly different baselines, and a fixed threshold works in exactly one of
 * them. It keeps adapting while nobody is speaking, so a fan that starts up
 * mid-conversation does not turn into a permanent false trigger.
 */
(function (root) {
    'use strict';

    var DEFAULTS = {
        startMs:      120,     // speech must persist this long to open a turn
        silenceMs:    900,     // silence this long closes it
        minTurnMs:    400,     // anything shorter is not a sentence
        maxTurnMs:    20000,   // hard stop, so a stuck mic cannot run forever
        calibrateMs:  400,     // listen to the room before trusting a threshold
        margin:       3.0,     // speech is this many times the noise floor
        marginWhileSpeaking: 6.0,  // a higher bar while OUR audio is playing:
                                   // echo cancellation is good, not perfect,
                                   // and a reply that interrupts itself is
                                   // worse than one that misses a barge-in
        floorMin:     0.006,   // absolute minimum, for a silent room where the
                               // measured floor would otherwise be ~0
        floorDecay:   0.97     // how fast the floor follows the room
    };

    function VAD(options) {
        this.opt = {};
        for (var k in DEFAULTS) {
            if (Object.prototype.hasOwnProperty.call(DEFAULTS, k)) {
                this.opt[k] = (options && options[k] !== undefined)
                    ? options[k] : DEFAULTS[k];
            }
        }
        this.reset(0);
    }

    VAD.prototype.reset = function (now) {
        this.state      = 'calibrating';
        this.t0         = now || 0;
        this.floor      = 0;
        this.floorSeen  = 0;
        this.voiceSince = null;   // when the current run of loud frames began
        this.lastVoice  = 0;      // last frame that counted as speech
        this.turnStart  = 0;
        this.threshold  = this.opt.floorMin;
    };

    /**
     * Ready for the next turn, keeping what has been learned about the room.
     *
     * Not reset(): recalibrating between turns would sample whatever is audible
     * at that moment, and after a barge-in that is the user mid-sentence. The
     * floor would jump to speech level and the next thing they said would be
     * inaudible to the endpointer. The room is measured once, at the start.
     */
    VAD.prototype.rearm = function () {
        this.state      = 'idle';
        this.voiceSince = null;
        return this;
    };

    /** The level a frame must beat to count as speech right now. */
    VAD.prototype.thresholdFor = function (speaking) {
        var margin = speaking ? this.opt.marginWhileSpeaking : this.opt.margin;
        return Math.max(this.floor * margin, this.opt.floorMin);
    };

    /**
     * Feed one frame.
     *   rms      loudness of the frame, 0..1
     *   now      milliseconds, monotonic
     *   speaking true while our own reply is being played back
     */
    VAD.prototype.push = function (rms, now, speaking) {
        if (this.state === 'calibrating') {
            // Average what the room sounds like with nobody talking.
            this.floor = this.floorSeen
                ? (this.floor * this.floorSeen + rms) / (this.floorSeen + 1)
                : rms;
            this.floorSeen++;
            if (now - this.t0 >= this.opt.calibrateMs) this.state = 'idle';
            this.threshold = this.thresholdFor(speaking);
            return '';
        }

        this.threshold = this.thresholdFor(speaking);
        var loud = rms > this.threshold;

        if (this.state === 'idle') {
            if (!loud) {
                // Let the floor drift toward the room while it is quiet, but
                // never upward fast enough that speech raises its own bar.
                this.floor = this.floor * this.opt.floorDecay
                           + rms * (1 - this.opt.floorDecay);
                this.voiceSince = null;
                return '';
            }
            if (this.voiceSince === null) this.voiceSince = now;
            if (now - this.voiceSince >= this.opt.startMs) {
                this.state     = 'speech';
                this.turnStart = this.voiceSince;   // credit the whole run
                this.lastVoice = now;
                return 'start';
            }
            return '';
        }

        // state === 'speech'
        if (loud) this.lastVoice = now;

        if (now - this.turnStart >= this.opt.maxTurnMs) {
            this.state = 'idle';
            this.voiceSince = null;
            return 'timeout';
        }
        if (now - this.lastVoice >= this.opt.silenceMs) {
            var length = this.lastVoice - this.turnStart;
            this.state = 'idle';
            this.voiceSince = null;
            return length < this.opt.minTurnMs ? 'discard' : 'end';
        }
        return '';
    };

    /** Loudness of one frame of samples, 0..1. */
    function rmsOf(samples) {
        var sum = 0;
        for (var i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
        return Math.sqrt(sum / (samples.length || 1));
    }

    var api = { VAD: VAD, rmsOf: rmsOf, DEFAULTS: DEFAULTS };

    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.GathmVAD = api;
}(typeof self !== 'undefined' ? self : this));
