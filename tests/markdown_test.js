#!/usr/bin/env node
/* Tests for gui/markdown.js.
 *
 * Two halves. The first checks the parse: what a model actually emits — lists,
 * fences, tables — has to come out as the right blocks. The second checks the
 * render, through a fake document, because the security property is about HOW
 * text reaches the page and that is only visible at that level.
 *
 * The fake document has no innerHTML. If the renderer ever reaches for one it
 * fails here rather than in a browser.
 *
 *   node tests/markdown_test.js
 */

'use strict';

const md = require('../gui/markdown.js');

let PASS = 0, FAIL = 0;

function check(name, got, want) {
    const a = JSON.stringify(got), b = JSON.stringify(want);
    if (a === b) { PASS++; console.log('  ok   ' + name); }
    else { FAIL++; console.log('  FAIL ' + name + '\n         got:  ' + a + '\n         want: ' + b); }
}

function ok(name, cond) { check(name, !!cond, true); }

/* ---- a fake document ---------------------------------------------------- */
// Deliberately minimal, and deliberately without innerHTML.

function FakeNode(tag) {
    this.tag = tag;
    this.children = [];
    this.attrs = {};
    this.className = '';
    this._text = null;
    this.listeners = {};
}
FakeNode.prototype.appendChild = function (child) {
    this.children.push(child);
    return child;
};
FakeNode.prototype.setAttribute = function (k, v) { this.attrs[k] = v; };
FakeNode.prototype.addEventListener = function (evt, fn) { this.listeners[evt] = fn; };
Object.defineProperty(FakeNode.prototype, 'textContent', {
    get: function () {
        if (this._text !== null) return this._text;
        return this.children.map(c => c.textContent).join('');
    },
    set: function (v) { this._text = String(v); this.children = []; }
});

function FakeText(text) { this.tag = '#text'; this._text = String(text); this.children = []; }
Object.defineProperty(FakeText.prototype, 'textContent', {
    get: function () { return this._text; },
    set: function (v) { this._text = String(v); }
});

const doc = {
    createElement: (tag) => new FakeNode(tag),
    createTextNode: (t) => new FakeText(t),
    createDocumentFragment: () => new FakeNode('#fragment')
};

// The whole tree as tags, for asserting structure.
function shape(node) {
    if (node.tag === '#text') return '"' + node.textContent + '"';
    // Text set through textContent is not a child node, but it is content.
    if (node._text !== null && node._text !== undefined) {
        return node.tag + '["' + node._text + '"]';
    }
    const kids = node.children.map(shape).join(',');
    return node.tag + (kids ? '[' + kids + ']' : '');
}

function tagsIn(node, out) {
    out = out || [];
    out.push(node.tag);
    node.children.forEach(c => tagsIn(c, out));
    return out;
}

/* ---- parsing ------------------------------------------------------------ */

function testParagraphs() {
    console.log('\nparagraphs');
    check('a plain line is a paragraph',
          md.parse('hello there').map(b => b.type), ['p']);
    check('a blank line splits paragraphs',
          md.parse('one\n\ntwo').map(b => b.type), ['p', 'p']);
    check('a soft wrap does not',
          md.parse('one\ntwo').length, 1);
    check('empty input is no blocks', md.parse('').length, 0);
    check('null is no blocks', md.parse(null).length, 0);
    check('CRLF is handled',
          md.parse('one\r\n\r\ntwo').map(b => b.type), ['p', 'p']);
}

function testLists() {
    console.log('\nlists — the thing that was rendering as one flat paragraph');
    let blocks = md.parse('- one\n- two\n- three');
    check('a dash list is a list', blocks.map(b => b.type), ['list']);
    check('with all three items', blocks[0].items, ['one', 'two', 'three']);
    ok('and is not ordered', !blocks[0].ordered);

    check('asterisks work too', md.parse('* a\n* b')[0].items, ['a', 'b']);
    check('and pluses', md.parse('+ a\n+ b')[0].items, ['a', 'b']);

    // The Desktop listing from the real trace.
    blocks = md.parse('1 Hakxcore LMS Redesign.pdf');
    check('a bare number is not a list', blocks.map(b => b.type), ['p']);

    blocks = md.parse('1. first\n2. second\n3. third');
    ok('a numbered list is ordered', blocks[0].ordered);
    check('with its items', blocks[0].items, ['first', 'second', 'third']);
    check('starting where it says', blocks[0].start, 1);
    check('a list starting at 4 keeps that', md.parse('4. d\n5. e')[0].start, 4);
    check('close-paren numbering works', md.parse('1) a\n2) b')[0].items, ['a', 'b']);

    blocks = md.parse('- a very long item that\n  wraps onto another line\n- second');
    check('a wrapped item stays one item', blocks[0].items.length, 2);
    check('and is joined', blocks[0].items[0],
          'a very long item that wraps onto another line');
}

function testCode() {
    console.log('\nfenced code');
    let blocks = md.parse('```bash\nls ~/Desktop\n```');
    check('a fence is a code block', blocks.map(b => b.type), ['code']);
    check('the language is kept', blocks[0].lang, 'bash');
    check('the body is verbatim', blocks[0].text, 'ls ~/Desktop');

    check('tildes fence too', md.parse('~~~\nx\n~~~')[0].type, 'code');
    check('no language is fine', md.parse('```\nx\n```')[0].lang, '');

    blocks = md.parse('```\n- not a list\n# not a heading\n**not bold**\n```');
    check('nothing inside a fence is parsed', blocks.length, 1);
    check('and it is kept exactly', blocks[0].text,
          '- not a list\n# not a heading\n**not bold**');

    // A model that forgets the closing fence is common.
    blocks = md.parse('```python\nprint(1)');
    check('an unclosed fence still ends', blocks.map(b => b.type), ['code']);
    check('...with what it had', blocks[0].text, 'print(1)');

    check('a blank line inside a fence is kept',
          md.parse('```\na\n\nb\n```')[0].text, 'a\n\nb');
}

function testHeadingsAndRules() {
    console.log('\nheadings, quotes and rules');
    check('h1', md.parse('# Title')[0].level, 1);
    check('h3', md.parse('### Sub')[0].level, 3);
    check('the text loses the hashes', md.parse('## Hi there')[0].text, 'Hi there');
    check('seven hashes is not a heading', md.parse('####### x')[0].type, 'p');
    check('a hash without a space is not either', md.parse('#tag')[0].type, 'p');

    check('a rule', md.parse('---').map(b => b.type), ['hr']);
    check('stars rule too', md.parse('***').map(b => b.type), ['hr']);

    let blocks = md.parse('> quoted line\n> and more');
    check('a blockquote', blocks.map(b => b.type), ['quote']);
    check('joined', blocks[0].text, 'quoted line\nand more');
}

function testTables() {
    console.log('\ntables');
    let blocks = md.parse('| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |');
    check('a table', blocks.map(b => b.type), ['table']);
    check('the header', blocks[0].head, ['a', 'b']);
    check('the rows', blocks[0].rows, [['1', '2'], ['3', '4']]);

    check('alignment markers are accepted',
          md.parse('| a | b |\n|:--|--:|\n| 1 | 2 |')[0].type, 'table');
    check('a pipe in prose is not a table',
          md.parse('use ls | grep foo to filter')[0].type, 'p');
    check('a header with no divider is not a table',
          md.parse('| a | b |\n| 1 | 2 |')[0].type, 'p');
}

function testInline() {
    console.log('\ninline');
    check('bold', md.parseInline('a **b** c').map(t => t.type),
          ['text', 'strong', 'text']);
    check('underscore bold', md.parseInline('__b__').map(t => t.type), ['strong']);
    check('italic', md.parseInline('*i*').map(t => t.type), ['em']);
    check('code span', md.parseInline('`x`').map(t => t.type), ['code']);
    check('strikethrough', md.parseInline('~~x~~').map(t => t.type), ['del']);

    check('code beats bold inside it',
          md.parseInline('`**not bold**`').map(t => [t.type, t.text]),
          [['code', '**not bold**']]);

    check('a link', md.parseInline('[text](https://example.com)')
          .map(t => [t.type, t.text, t.href]),
          [['link', 'text', 'https://example.com']]);
    check('a bare url autolinks',
          md.parseInline('see https://example.com now').map(t => t.type),
          ['text', 'link', 'text']);

    check('plain text is one token',
          md.parseInline('nothing special here').map(t => t.type), ['text']);
    check('a lone asterisk is text', md.parseInline('2 * 3 = 6')[0].type, 'text');
}

function testUnsafeHrefs() {
    console.log('\nunsafe link schemes are not links');
    check('http is fine', md.safeHref('http://x.tld'), 'http://x.tld');
    check('https is fine', md.safeHref('https://x.tld'), 'https://x.tld');
    check('mailto is fine', md.safeHref('mailto:a@b.tld'), 'mailto:a@b.tld');
    for (const bad of ['javascript:alert(1)', 'JavaScript:alert(1)',
                       'data:text/html,<script>x</script>', 'vbscript:x',
                       'file:///etc/passwd', 'about:blank']) {
        check('refused: ' + bad, md.safeHref(bad), '');
    }
    // A refused scheme is shown as text, not silently dropped — the user still
    // sees what the model wrote.
    const tokens = md.parseInline('[click](javascript:alert(1))');
    ok('and none of it becomes a link', tokens.every(t => t.type === 'text'));
    ok('with the original visible',
       tokens.map(t => t.text).join('').indexOf('javascript') !== -1);
}

/* ---- rendering ---------------------------------------------------------- */

function testRender() {
    console.log('\nrendering builds nodes');
    let frag = md.render('hello **world**', doc);
    check('a paragraph with a bold run',
          shape(frag), '#fragment[p["hello ",strong["world"]]]');

    frag = md.render('- a\n- b', doc);
    check('a list becomes ul/li', shape(frag), '#fragment[ul[li["a"],li["b"]]]');

    frag = md.render('1. a\n2. b', doc);
    check('an ordered list becomes ol', shape(frag), '#fragment[ol[li["a"],li["b"]]]');

    frag = md.render('```sh\nls\n```', doc);
    ok('a fence produces a pre', tagsIn(frag).indexOf('pre') !== -1);
    ok('with a code inside', tagsIn(frag).indexOf('code') !== -1);
    ok('and a copy button', tagsIn(frag).indexOf('button') !== -1);

    frag = md.render('| a |\n|---|\n| 1 |', doc);
    const tags = tagsIn(frag);
    ok('a table produces table/thead/tbody',
       tags.indexOf('table') !== -1 && tags.indexOf('thead') !== -1 &&
       tags.indexOf('tbody') !== -1);
    ok('with a th and a td',
       tags.indexOf('th') !== -1 && tags.indexOf('td') !== -1);

    frag = md.render('# Title', doc);
    ok('a heading level becomes the tag', tagsIn(frag).indexOf('h1') !== -1);

    frag = md.render('[go](https://example.com)', doc);
    const link = frag.children[0].children[0];
    check('a link carries its href', link.attrs.href, 'https://example.com');
    check('opens in a new tab', link.attrs.target, '_blank');
    ok('with noopener', (link.attrs.rel || '').indexOf('noopener') !== -1);
}

function testNoHtmlInjection() {
    console.log('\nHTML in a reply is text, never an element');
    // This is the case that matters: `browser fetch` puts arbitrary page text
    // into a reply, on a page whose API can run shell commands.
    const hostile = [
        '<script>alert(1)</script>',
        '<img src=x onerror=alert(1)>',
        'text with <b>tags</b> in it',
        '<iframe src="http://evil.tld"></iframe>',
        '**bold with <script>alert(1)</script> inside**',
        '`<script>alert(1)</script>`',
        '| <script>x</script> |\n|---|\n| <img onerror=x> |',
        '- <script>alert(1)</script>',
        '# <script>alert(1)</script>'
    ];
    for (const src of hostile) {
        const frag = md.render(src, doc);
        const tags = tagsIn(frag);
        const bad = tags.filter(t => ['script', 'img', 'iframe', 'b', 'svg',
                                      'object', 'embed'].indexOf(t) !== -1);
        check('no element created for: ' + src.slice(0, 34), bad, []);
        // And the characters survive, so nothing is silently swallowed: the
        // angle brackets are on screen as text rather than parsed away.
        ok('...but the markup is still visible as text',
           frag.textContent.indexOf('<') !== -1);
    }

    // The renderer must never reach for innerHTML: the fake document does not
    // have one, so this is enforced by the tests above passing at all. Assert
    // the intent explicitly too, against the source.
    // Comments stripped first: the header explains WHY innerHTML is avoided,
    // and naming it there is not a use of it.
    const raw = require('fs').readFileSync(__dirname + '/../gui/markdown.js', 'utf8');
    const code = raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');
    for (const sink of ['innerHTML', 'outerHTML', 'insertAdjacentHTML',
                        'document.write', 'eval(']) {
        ok('the code never uses ' + sink, code.indexOf(sink) === -1);
    }
}

function testRealReplies() {
    console.log('\nreplies from the real traces');
    // The Desktop listing, as llama3.2:3b actually produced it.
    const desktop = 'You have the following files on your Desktop:\n\n' +
        '1. Hakxcore LMS Redesign.pdf\n2. Hakxcore Wireframes.pdf\n' +
        '3. WoodleDesign.pdf\n\nNo files are missing.';
    const blocks = md.parse(desktop);
    check('intro, list, outro', blocks.map(b => b.type), ['p', 'list', 'p']);
    check('three files', blocks[1].items.length, 3);

    const withCode = 'Run this:\n\n```bash\ndf -h\n```\n\nThe `Avail` column is free space.';
    check('prose, fence, prose',
          md.parse(withCode).map(b => b.type), ['p', 'code', 'p']);

    // Tool output pasted verbatim: a df table, which is not markdown at all.
    const df = 'Filesystem  Size  Used Avail Use%\n/dev/disk1  460G  448G   12G  98%';
    check('plain columns stay one paragraph', md.parse(df).map(b => b.type), ['p']);
}

console.log('Markdown renderer tests');
console.log('='.repeat(60));
testParagraphs();
testLists();
testCode();
testHeadingsAndRules();
testTables();
testInline();
testUnsafeHrefs();
testRender();
testNoHtmlInjection();
testRealReplies();
console.log('='.repeat(60));
console.log(PASS + ' passed, ' + FAIL + ' failed');
process.exit(FAIL ? 1 : 0);
