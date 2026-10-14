"""Conservative, local policy for automatic public-knowledge lookups."""
import json
import re
from urllib.parse import urlsplit


_UNKNOWN = re.compile(
    r"\b(?:i (?:do not|don't) know|i(?:'m| am) (?:not sure|unsure|uncertain)|"
    r"i (?:do not|don't) have (?:enough |reliable |current |that |any |the )*"
    r"(?:information|knowledge|data|details|access)|"
    r"(?:cannot|can't|unable to) (?:verify|confirm|find|answer)|"
    r"knowledge (?:cutoff|cut-off))\b", re.I)
_PRIVATE = re.compile(
    r"\b(?:my|our|we|me|mine|ours|previous|earlier|conversation|history|"
    r"password|passphrase|secret|token|credential|api[ _-]?key|private|"
    r"confidential|internal|localhost|medical record|account number)\b|"
    r"[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?:^|\s)(?:[/~\\]|[A-Z]:\\)|"
    r"https?://\S*[?@#]|\b\d{5,}\b|\b(?:sk-|ghp_|github_pat_|AKIA)\S+", re.I)
_NO_WEB = re.compile(
    r"\b(?:offline|no (?:internet|web|search)|without (?:internet|web|search)|"
    r"(?:don't|do not|never) (?:search|browse|look online)|"
    r"write|draft|compose|rewrite|translate|summarize|summarise|"
    r"delete|remove|restart|shutdown|execute|run|install)\b", re.I)
_REFUSAL = re.compile(r"\b(?:can't|cannot|won't|will not) (?:help|assist)\b", re.I)
_ACTION_CLAIM = re.compile(
    r"\b(?:i(?:'ve| have)? (?:just |went ahead and )?(?:searched|checked|looked|browsed|opened|"
    r"launched|executed|ran|used the command|created|deleted|moved|installed)|"
    r"(?:launch|open) requested for|after (?:searching|checking|browsing)|"
    r"i(?:'ve| have) done a (?:new )?search)\b", re.I)


def _request_text(question):
    text = " ".join(str(question or "").replace("’", "'").split())
    correction = re.fullmatch(r"no[, ]+i(?:'m| am) referring to ([\w.-]+)(?: not .+)?[.!?]?", text, re.I)
    if correction:
        return "Find information about " + correction[1].rstrip(".!?")
    text = re.sub(r"^(?:(?:yes but|no|please|okay)[, ]+)+", "", text, flags=re.I)
    text = re.sub(r"^(?:i want you to|can you|could you|would you)\s+", "", text, flags=re.I)
    return re.sub(r"^tell me\s+", "Tell ", text, flags=re.I)


def explicit_lookup(question):
    text = _request_text(question)
    return bool(re.match(r"^(?:find|search|look (?:up|for|online)|google|browse|browser)\b", text, re.I))


def requires_lookup(question):
    """Changing facts need fresh evidence even if the model sounds confident."""
    return explicit_lookup(question) or bool(public_query(question) and re.search(
        r"\b(?:latest|breaking news|current (?:price|version|release|president|ceo|exchange rate)|"
        r"(?:weather|forecast|news|price|stocks?|scores?)\b.*\b(?:today|now|tonight|tomorrow)|"
        r"(?:today|now|tonight|tomorrow)\b.*\b(?:weather|forecast|news|price|stocks?|scores?))\b",
        question, re.I))


def unbacked_action_claim(answer, question=""):
    if re.match(r"^(?:write|draft|compose|translate|rewrite|tell a story)\b", _request_text(question), re.I):
        return False  # first-person fiction/quoted text is not an action claim
    return bool(_ACTION_CLAIM.search(str(answer or "").replace("’", "'")))


def public_query(question):
    """Use only a short, standalone current question, never memory/history.

    This deliberately skips contextual and sensitive requests. It is a
    heuristic privacy guard, not a general-purpose PII classifier.
    """
    text = _request_text(question)
    if (not 8 <= len(text) <= 400 or _PRIVATE.search(text) or _NO_WEB.search(text)
            or not re.match(r"(?:what|who|when|where|why|how|which|is|are|does|did|"
                            r"can|tell|explain|find|search|look|google|browse|browser)\b", text, re.I)):
        return ""
    return text


def needs_lookup(answer):
    text = str(answer or "").replace("’", "'")
    return bool(_UNKNOWN.search(text) and not _REFUSAL.search(text))


def search_evidence(raw):
    """Accept bounded HTTP(S) links only from the actual search result."""
    try:
        data = json.loads(raw)
        results = data.get("results", [])[:3]
    except (ValueError, AttributeError, TypeError):
        return "", []
    lines, sources = [], []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", ""))
        try:
            parsed = urlsplit(url)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname
                    or parsed.username or parsed.password or len(url) > 600
                    or any(c.isspace() or ord(c) < 32 for c in url)):
                continue
        except ValueError:
            continue
        if url in sources:
            continue
        sources.append(url)
        title = " ".join(str(item.get("title", "")).split())[:100]
        snippet = " ".join(str(item.get("snippet", "")).split())[:300]
        lines.append(f"{len(sources)}. {title}\n{url}\n{snippet}")
    return "\n".join(lines), sources


def with_sources(answer, sources):
    """Always retain real source links, including for small local models."""
    missing = [url for url in sources if url not in answer]
    if missing:
        # Escape Markdown delimiters in search-provided URLs.
        links = [f"[Source {i}](<{url.replace('>', '%3E').replace('<', '%3C')}>)"
                 for i, url in enumerate(missing, 1)]
        return answer.rstrip() + "\n\nSources: " + ", ".join(links)
    return answer
