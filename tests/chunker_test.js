/**
 * Tests for the browser-side speech chunker (gui/chunker.js).
 *
 *   node tests/chunker_test.js
 *
 * Also checks it agrees with lib/speech.py, since both ends have to mean the
 * same thing by "a sentence" — a drift between them shows up as the browser
 * speaking different phrases from the terminal.
 */
'use strict';

const path = require('path');
const { execFileSync } = require('child_process');
const { splitSpeech, cleanForSpeech } =
    require(path.join(__dirname, '..', 'gui', 'chunker.js'));

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

function test_basic() {
    console.log('\nsplitting a reply');

    check('a single short sentence stays whole',
          splitSpeech('Sure, done.'), ['Sure, done.']);

    // The first chunk may be short — that is the entire point, since it is
    // what the user waits for.
    const two = splitSpeech(
        'Sure. It is thirty two degrees and clear in Delhi right now.');
    check('the first chunk is the short opener', two[0], 'Sure.');
    ok('and the rest follows', two.length === 2);

    // Later chunks are held to the minimum so the reply is not a stutter.
    const many = splitSpeech('Yes. No. Maybe. Possibly. Who knows.');
    ok('short sentences after the first are merged', many.length <= 2);

    check('everything is consumed',
          splitSpeech('One. Two. Three.').join(' ').replace(/\s+/g, ' '),
          'One. Two. Three.');
}

function test_not_sentence_ends() {
    console.log('\nthings that look like sentence ends but are not');
    const t = 'Pi is 3.14 and the file is gathm.sh, which matters here.';
    const chunks = splitSpeech(t);
    check('no split inside 3.14 or gathm.sh', chunks.length, 1);
    ok('nothing was lost', chunks[0].indexOf('3.14') !== -1 &&
                           chunks[0].indexOf('gathm.sh') !== -1);
}

function test_markdown() {
    console.log('\nmarkdown is not read out loud');
    check('emphasis stripped', cleanForSpeech('**bold** and _italic_'),
          'bold and italic');
    check('inline code unwrapped', cleanForSpeech('run `gathm status` now'),
          'run gathm status now');
    check('links become their label',
          cleanForSpeech('see [the docs](https://example.com/x)'),
          'see the docs');
    check('bare urls become a phrase',
          cleanForSpeech('go to https://example.com/a/b now'),
          'go to a link now');
    ok('code fences are summarised',
       cleanForSpeech('before ```\nx = 1\n``` after')
           .indexOf('code block omitted') !== -1);
}

function test_unpunctuated() {
    console.log('\ntext that never punctuates');
    const wall = 'word '.repeat(200).trim();
    const chunks = splitSpeech(wall, { max: 60 });
    ok('broken into several', chunks.length > 2);
    ok('each within the limit plus a word',
       chunks.every(c => c.length <= 70));
    ok('never mid-word', chunks.every(c => !/\bwor$/.test(c)));
    check('nothing lost',
          chunks.join(' ').replace(/\s+/g, ' ').trim(), wall);
}

function test_edges() {
    console.log('\nedges');
    check('empty', splitSpeech(''), []);
    check('null', splitSpeech(null), []);
    check('whitespace only', splitSpeech('   \n  '), []);
    check('markdown that cleans to nothing', splitSpeech('***'), []);
}

function test_agrees_with_python() {
    console.log('\nit agrees with lib/speech.py');
    const cases = [
        'Sure. It is thirty two degrees and clear in Delhi right now.',
        'Pi is 3.14 and the file is gathm.sh, which matters here.',
        'One sentence that is comfortably past the minimum length. And a second one, also long enough.',
        'Yes. No. Maybe so, on balance, probably not today.',
    ];

    let python;
    try {
        python = execFileSync('python3', ['-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(path.join(__dirname, '..'))})
from lib.speech import split_speech_chunks
out = []
for t in json.load(sys.stdin):
    chunks, rest = split_speech_chunks(t, final=True, first_min=1)
    out.append(chunks)
print(json.dumps(out))
`], { input: JSON.stringify(cases), encoding: 'utf8' });
    } catch (err) {
        console.log('  SKIP python3 or lib/speech.py unavailable');
        return;
    }

    const fromPython = JSON.parse(python);
    cases.forEach(function (text, i) {
        check('same split as python: ' + text.slice(0, 34) + '…',
              splitSpeech(text), fromPython[i]);
    });
}

console.log('Browser speech chunker tests');
console.log('='.repeat(60));
test_basic();
test_not_sentence_ends();
test_markdown();
test_unpunctuated();
test_edges();
test_agrees_with_python();
console.log('='.repeat(60));
console.log(PASS + ' passed, ' + FAIL + ' failed');
process.exit(FAIL ? 1 : 0);
