/* Gathm — a small Markdown renderer for chat replies.
 *
 * The GUI used to set `p.textContent = reply`, so every numbered list, table
 * and code fence a model produced arrived as one flat paragraph. This renders
 * the subset that actually shows up in chat: fenced code, headings, lists,
 * tables, quotes, rules, and inline code/bold/italic/links.
 *
 * It builds DOM NODES. It never assembles an HTML string, and nothing here
 * touches innerHTML.
 *
 * That is a security decision, not a stylistic one. Replies carry tool output,
 * and `browser fetch <url>` puts arbitrary web-page text into a reply — on a
 * page that talks to a local API able to run shell commands. A renderer that
 * concatenated HTML would turn any fetched page into script execution. Text
 * only ever reaches the document through createTextNode/textContent, so a
 * `<script>` in a reply is characters on screen and can never be an element.
 *
 * Link hrefs are the one place a string becomes a live attribute, so the
 * scheme is allowlisted (http, https, mailto). Anything else — javascript:,
 * data: — renders as plain text.
 *
 *   parse(text)          -> block tokens, for tests and for reuse
 *   parseInline(text)    -> inline tokens
 *   render(text, doc)    -> DocumentFragment
 *
 * No dependencies, and no CDN: the page already has to survive being opened
 * with no internet, on a phone.
 */
(function (root) {
    'use strict';

    var FENCE = /^(```|~~~)\s*([A-Za-z0-9_+-]*)\s*$/;
    var HEADING = /^(#{1,6})\s+(.*)$/;
    var RULE = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
    var UL_ITEM = /^\s{0,3}[-*+]\s+(.*)$/;
    var OL_ITEM = /^\s{0,3}(\d{1,9})[.)]\s+(.*)$/;
    var QUOTE = /^\s{0,3}>\s?(.*)$/;
    var TABLE_DIVIDER = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/;

    var SAFE_SCHEME = /^(https?:|mailto:)/i;

    function isTableRow(line) {
        return line.indexOf('|') !== -1 && /\S/.test(line);
    }

    function splitRow(line) {
        var trimmed = line.trim();
        if (trimmed.charAt(0) === '|') trimmed = trimmed.slice(1);
        if (trimmed.charAt(trimmed.length - 1) === '|') trimmed = trimmed.slice(0, -1);
        return trimmed.split('|').map(function (cell) { return cell.trim(); });
    }

    /* ---- blocks ---------------------------------------------------------- */

    function parse(text) {
        var lines = String(text == null ? '' : text).replace(/\r\n?/g, '\n').split('\n');
        var blocks = [];
        var i = 0;

        while (i < lines.length) {
            var line = lines[i];

            // Fenced code. Everything inside is literal — no inline parsing,
            // which is the whole point of a fence.
            var fence = line.match(FENCE);
            if (fence) {
                var marker = fence[1];
                var lang = fence[2] || '';
                var body = [];
                i++;
                while (i < lines.length) {
                    var closing = lines[i].match(FENCE);
                    if (closing && closing[1] === marker) { i++; break; }
                    body.push(lines[i]);
                    i++;
                }
                blocks.push({ type: 'code', lang: lang, text: body.join('\n') });
                continue;
            }

            if (!line.trim()) { i++; continue; }

            if (RULE.test(line)) { blocks.push({ type: 'hr' }); i++; continue; }

            var heading = line.match(HEADING);
            if (heading) {
                blocks.push({
                    type: 'heading',
                    level: heading[1].length,
                    text: heading[2].trim()
                });
                i++;
                continue;
            }

            // A table needs a divider on the second line, or it is a paragraph
            // that happens to contain a pipe.
            if (isTableRow(line) && i + 1 < lines.length &&
                TABLE_DIVIDER.test(lines[i + 1]) && lines[i + 1].indexOf('|') !== -1) {
                var head = splitRow(line);
                var rows = [];
                i += 2;
                while (i < lines.length && isTableRow(lines[i])) {
                    rows.push(splitRow(lines[i]));
                    i++;
                }
                blocks.push({ type: 'table', head: head, rows: rows });
                continue;
            }

            if (QUOTE.test(line)) {
                var quoted = [];
                while (i < lines.length && QUOTE.test(lines[i])) {
                    quoted.push(lines[i].match(QUOTE)[1]);
                    i++;
                }
                blocks.push({ type: 'quote', text: quoted.join('\n').trim() });
                continue;
            }

            if (UL_ITEM.test(line) || OL_ITEM.test(line)) {
                var ordered = OL_ITEM.test(line);
                var items = [];
                var start = ordered ? parseInt(line.match(OL_ITEM)[1], 10) : 1;
                while (i < lines.length) {
                    var ul = lines[i].match(UL_ITEM);
                    var ol = lines[i].match(OL_ITEM);
                    if (ordered && ol) items.push(ol[2]);
                    else if (!ordered && ul) items.push(ul[1]);
                    else if (items.length && /^\s+\S/.test(lines[i])) {
                        // A wrapped continuation line belongs to the item above.
                        items[items.length - 1] += ' ' + lines[i].trim();
                    } else break;
                    i++;
                }
                blocks.push({
                    type: 'list', ordered: ordered, start: start, items: items
                });
                continue;
            }

            // Paragraph: everything up to a blank line or the start of another
            // block.
            var para = [];
            while (i < lines.length && lines[i].trim() &&
                   !FENCE.test(lines[i]) && !HEADING.test(lines[i]) &&
                   !RULE.test(lines[i]) && !QUOTE.test(lines[i]) &&
                   !UL_ITEM.test(lines[i]) && !OL_ITEM.test(lines[i])) {
                para.push(lines[i]);
                i++;
            }
            if (para.length) blocks.push({ type: 'p', text: para.join('\n') });
            else i++;   // nothing consumed: do not spin
        }

        return blocks;
    }

    /* ---- inline ---------------------------------------------------------- */

    // Order matters: code spans win over everything, so `**not bold**` inside
    // backticks stays literal.
    // The backreferences here are numbered against the WHOLE pattern, not each
    // alternative. Writing \1 in the bold branch pointed it at the backtick
    // group, which never participates in that branch, so it matched the empty
    // string and `**b**` came out as bold("**b") followed by italic("** ").
    var INLINE = new RegExp([
        '(`+)([\\s\\S]*?)\\1',                       // 1,2  code
        '\\[([^\\]]*)\\]\\(([^)\\s]+)[^)]*\\)',      // 3,4  [text](href)
        '(\\*\\*|__)(?=\\S)([\\s\\S]*?\\S)\\5',      // 5,6  bold
        '(\\*|_)(?=\\S)([\\s\\S]*?\\S)\\7',          // 7,8  italic
        '~~(?=\\S)([\\s\\S]*?\\S)~~',                // 9    strikethrough
        '(https?://[^\\s<>()]+)'                     // 10   bare URL
    ].join('|'), 'g');

    function safeHref(href) {
        var trimmed = String(href || '').trim();
        return SAFE_SCHEME.test(trimmed) ? trimmed : '';
    }

    function parseInline(text) {
        var src = String(text == null ? '' : text);
        var tokens = [];
        var last = 0;
        var match;

        INLINE.lastIndex = 0;
        while ((match = INLINE.exec(src)) !== null) {
            if (match.index > last) {
                tokens.push({ type: 'text', text: src.slice(last, match.index) });
            }
            if (match[2] !== undefined) {
                tokens.push({ type: 'code', text: match[2].trim() });
            } else if (match[4] !== undefined) {
                var href = safeHref(match[4]);
                if (href) tokens.push({ type: 'link', text: match[3] || href, href: href });
                // An unsafe scheme is not a link and is not silently dropped:
                // the user still sees what the model wrote.
                else tokens.push({ type: 'text', text: match[0] });
            } else if (match[6] !== undefined) {
                tokens.push({ type: 'strong', text: match[6] });
            } else if (match[8] !== undefined) {
                tokens.push({ type: 'em', text: match[8] });
            } else if (match[9] !== undefined) {
                tokens.push({ type: 'del', text: match[9] });
            } else if (match[10] !== undefined) {
                tokens.push({ type: 'link', text: match[10], href: match[10] });
            }
            last = match.index + match[0].length;
        }
        if (last < src.length) tokens.push({ type: 'text', text: src.slice(last) });
        return tokens;
    }

    /* ---- rendering ------------------------------------------------------- */

    function renderInline(text, doc, into) {
        parseInline(text).forEach(function (token) {
            var node;
            switch (token.type) {
                case 'code':
                    node = doc.createElement('code');
                    node.className = 'md-code';
                    node.textContent = token.text;
                    break;
                case 'strong':
                    node = doc.createElement('strong');
                    node.textContent = token.text;
                    break;
                case 'em':
                    node = doc.createElement('em');
                    node.textContent = token.text;
                    break;
                case 'del':
                    node = doc.createElement('del');
                    node.textContent = token.text;
                    break;
                case 'link':
                    node = doc.createElement('a');
                    node.setAttribute('href', token.href);
                    node.setAttribute('target', '_blank');
                    // Without noopener the opened page gets a handle on this
                    // one through window.opener.
                    node.setAttribute('rel', 'noopener noreferrer');
                    node.textContent = token.text;
                    break;
                default:
                    node = doc.createTextNode(token.text);
            }
            into.appendChild(node);
        });
        return into;
    }

    function renderCode(block, doc) {
        var wrap = doc.createElement('div');
        wrap.className = 'md-pre-wrap';

        var pre = doc.createElement('pre');
        pre.className = 'md-pre';
        var code = doc.createElement('code');
        if (block.lang) code.className = 'md-lang-' + block.lang.toLowerCase();
        code.textContent = block.text;
        pre.appendChild(code);

        // Copy is most of what a code block is for in a chat window.
        var button = doc.createElement('button');
        button.className = 'md-copy';
        button.setAttribute('type', 'button');
        button.textContent = 'copy';
        if (button.addEventListener) {
            button.addEventListener('click', function () {
                var done = function () {
                    button.textContent = 'copied';
                    setTimeout(function () { button.textContent = 'copy'; }, 1200);
                };
                try {
                    if (root.navigator && root.navigator.clipboard) {
                        root.navigator.clipboard.writeText(block.text).then(done, function () {});
                    }
                } catch (err) { /* a refused clipboard is not worth an error */ }
            });
        }

        wrap.appendChild(button);
        wrap.appendChild(pre);
        return wrap;
    }

    function renderTable(block, doc) {
        var wrap = doc.createElement('div');
        wrap.className = 'md-table-wrap';
        var table = doc.createElement('table');
        table.className = 'md-table';

        var thead = doc.createElement('thead');
        var headRow = doc.createElement('tr');
        block.head.forEach(function (cell) {
            var th = doc.createElement('th');
            renderInline(cell, doc, th);
            headRow.appendChild(th);
        });
        thead.appendChild(headRow);
        table.appendChild(thead);

        var tbody = doc.createElement('tbody');
        block.rows.forEach(function (row) {
            var tr = doc.createElement('tr');
            row.forEach(function (cell) {
                var td = doc.createElement('td');
                renderInline(cell, doc, td);
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);

        wrap.appendChild(table);
        return wrap;
    }

    function render(text, doc) {
        doc = doc || (typeof document !== 'undefined' ? document : null);
        if (!doc) throw new Error('render() needs a document');

        var fragment = doc.createDocumentFragment();

        parse(text).forEach(function (block) {
            var node;
            switch (block.type) {
                case 'code':
                    node = renderCode(block, doc);
                    break;
                case 'heading':
                    node = doc.createElement('h' + block.level);
                    node.className = 'md-h';
                    renderInline(block.text, doc, node);
                    break;
                case 'hr':
                    node = doc.createElement('hr');
                    node.className = 'md-hr';
                    break;
                case 'quote':
                    node = doc.createElement('blockquote');
                    node.className = 'md-quote';
                    renderInline(block.text, doc, node);
                    break;
                case 'list':
                    node = doc.createElement(block.ordered ? 'ol' : 'ul');
                    node.className = 'md-list';
                    if (block.ordered && block.start !== 1) {
                        node.setAttribute('start', String(block.start));
                    }
                    block.items.forEach(function (item) {
                        var li = doc.createElement('li');
                        renderInline(item, doc, li);
                        node.appendChild(li);
                    });
                    break;
                case 'table':
                    node = renderTable(block, doc);
                    break;
                default:
                    node = doc.createElement('p');
                    node.className = 'md-p';
                    renderInline(block.text, doc, node);
            }
            fragment.appendChild(node);
        });

        return fragment;
    }

    var api = {
        parse: parse,
        parseInline: parseInline,
        render: render,
        safeHref: safeHref
    };

    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.GathmMarkdown = api;
}(typeof self !== 'undefined' ? self : this));
