/**
 * Cutting a reply into utterances, for the browser.
 *
 * The server renders whatever text it is given in full before returning a
 * single byte, so asking it for a whole answer means waiting for the whole
 * answer to be synthesised. Asking for one sentence at a time means audio
 * starts after the first sentence and the rest is rendered while it plays.
 *
 * This mirrors split_speech_chunks() in lib/speech.py — same rules, same
 * defaults — because both ends have to agree on what "a sentence" is. It is a
 * deliberate duplicate: the browser cannot call Python, and a chunker is small
 * enough that sharing it through the server would cost a round trip per reply.
 *
 * No DOM, so it is testable without a browser (tests/chunker_test.js).
 */
(function (root) {
    'use strict';

    var DEFAULTS = {
        min: 40,     // shortest utterance, so a reply is not a stutter
        max: 240,    // forced break for text that never punctuates
        first: 1     // the FIRST chunk may be short: starting fast is the point
    };

    // .!?… then optional closing quote/bracket, then whitespace or the end.
    // Requiring what follows is what keeps "3.14" and "gathm.sh" intact.
    var SENTENCE_END = /[.!?…]['")\]]*(?=\s|$)/g;

    // A model asked for code does not always fence it — small models routinely
    // emit a whole C++ program as plain text, and then it gets read out loud,
    // `#include` included. Mirrors _CODE_LINE_RE in lib/speech.py; the two are
    // asserted to agree in tests/chunker_test.js.
    var CODE_LINE = new RegExp([
        '^\\s*#\\s*(include|define|pragma|ifndef|endif|import)\\b',
        '^\\s*(using|namespace|template|typedef|struct|enum|impl|trait)\\b',
        '^\\s*(public|private|protected|static|final|async)\\s+\\S',
        '^\\s*(def|class|import|from|package|func|fn|var|let|const)\\s+\\S',
        '^\\s*(if|for|while|switch|catch|elif)\\s*\\(',
        '^\\s*(return|throw|break|continue)\\b[^.!?]*;\\s*(//[^\\n]*)?$',
        '^\\s*[{}\\[\\]()]+\\s*;?\\s*$',
        ';\\s*(//[^\\n]*)?$',
        '\\{\\s*(//[^\\n]*)?$',
        '^\\s*(//|/\\*|\\*/|\\*\\s)',
        '^\\s*</?[a-zA-Z][^>]*>\\s*$',
        '\\w+\\s*\\([^)]*\\)\\s*(const\\s*)?\\{\\s*$',
        '^\\s{2,}\\S+.*[;{}]\\s*(//[^\\n]*)?$'
    ].join('|'));

    var MIN_CODE_LINES = 3;

    /** Replace runs of code-looking lines with a note, fences or not. */
    function stripUnfencedCode(text) {
        var lines = String(text || '').split('\n');
        var out = [];
        var i = 0;
        while (i < lines.length) {
            if (!CODE_LINE.test(lines[i])) { out.push(lines[i]); i++; continue; }
            // Blank lines inside a run belong to the code, but do not count
            // towards it — a program has paragraphs too.
            var scan = i, counted = 0, lastCode = i;
            while (scan < lines.length) {
                if (CODE_LINE.test(lines[scan])) { counted++; lastCode = scan; scan++; }
                else if (!lines[scan].trim()) { scan++; }
                else break;
            }
            if (counted >= MIN_CODE_LINES) out.push(' code block omitted. ');
            else out = out.concat(lines.slice(i, lastCode + 1));
            i = lastCode + 1;
        }
        return out.join('\n');
    }

    /** Strip markdown that should not be read out loud. */
    function cleanForSpeech(text) {
        var stripped = String(text || '')
            .replace(/```[\s\S]*?```/g, ' code block omitted. ');
        // Before the markdown markers go, or `#include` becomes the word
        // "include" and stops looking like code at all.
        return stripUnfencedCode(stripped)
            .replace(/`([^`]*)`/g, '$1')
            .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
            .replace(/https?:\/\/\S+/g, ' a link ')
            .replace(/^\s*[#>*\-+|]+\s*/gm, '')
            .replace(/[*_~|]+/g, ' ')
            .replace(/\s+/g, ' ')
            .trim()
            // "code block omitted. code block omitted." helps nobody.
            .replace(/(code block omitted\.\s*){2,}/g, 'code block omitted. ')
            .trim();
    }

    /**
     * Cut `text` into utterances, cleaned and ready to be spoken.
     * Always consumes everything: this is a finished reply, not a stream.
     */
    function splitSpeech(text, options) {
        var opt = {};
        for (var k in DEFAULTS) {
            if (Object.prototype.hasOwnProperty.call(DEFAULTS, k)) {
                opt[k] = (options && options[k] !== undefined)
                    ? options[k] : DEFAULTS[k];
            }
        }

        var raw = String(text || '');
        var chunks = [];
        var pos = 0;

        while (pos < raw.length) {
            var need = chunks.length ? opt.min : opt.first;
            var cut = -1;

            SENTENCE_END.lastIndex = pos;
            var m;
            while ((m = SENTENCE_END.exec(raw)) !== null) {
                if (m.index + m[0].length - pos >= need) {
                    cut = m.index + m[0].length;
                    break;
                }
            }

            if (cut < 0) {
                // No sentence end left. Take the rest, unless it is long
                // enough to be worth breaking on a word boundary first.
                if (opt.max && raw.length - pos > opt.max) {
                    var window = raw.slice(pos, pos + opt.max);
                    var space = window.lastIndexOf(' ');
                    cut = pos + (space > opt.max / 3 ? space + 1 : opt.max);
                } else {
                    cut = raw.length;
                }
            }

            var piece = cleanForSpeech(raw.slice(pos, cut));
            if (piece) chunks.push(piece);
            pos = cut;
        }

        return chunks;
    }

    var api = { splitSpeech: splitSpeech, cleanForSpeech: cleanForSpeech,
                stripUnfencedCode: stripUnfencedCode,
                DEFAULTS: DEFAULTS };

    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.GathmChunker = api;
}(typeof self !== 'undefined' ? self : this));
