"""Parse clear device requests into proposals, never into permission to execute.

This deliberately small grammar needs no model or OS access. Unsupported wording
can still use the model, but device claims must then be backed by a tool result.
"""
import re
import shlex


def _match(pattern, text):
    return re.fullmatch(pattern, text, re.I)


def _command(*parts):
    return {"command": shlex.join(parts)}


def _clarify():
    return {"reply": "Nothing has run. Please give one exact app, path, process ID, or absolute percentage for the action. You can use /control for the supported commands."}


def _unquote(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _path(text):
    text = _unquote(text)
    if text.lower() in {"documents", "downloads", "desktop", "pictures", "music", "videos"}:
        return "~/" + text.capitalize()
    if text.startswith(("~/", "/")) or re.match(r"^[A-Za-z]:[\\/]", text):
        return text
    return None


def _request_text(text):
    text = text.strip().replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"^(?:okay|ok)[, ]+leave it[, ]+", "", text, flags=re.I)
    text = re.sub(r"^(?:can|could)(?: you)? help me (?:to )?", "", text, flags=re.I)
    text = re.sub(r"[.!?]$", "", text).strip()
    text = re.sub(r"^(?:(?:now|please)\s+|(?:can|could|would)\s+you\s+)+", "", text, flags=re.I)
    return re.sub(r"\s+(?:please|now)$", "", text, flags=re.I)


def needs_control_evidence(text):
    """Whether an unmatched request needs current device evidence, not prose."""
    text = _request_text(text)
    if re.match(r"^(?:how\s+(?:do|can|would|should)|explain|teach|write a story|translate|what is a)\b", text, re.I):
        return False
    if re.match(r"^(?:open|launch)\b", text, re.I) and not re.search(r"(?:https?://|www\.)", text, re.I):
        return True
    if _match(r"(?:you )?do (?:it|that|and tell me|it and tell me)", text):
        return True
    subject = r"\b(?:volume|brightness|audio|screenshot|apps?|applications?|files?|folders?|directories|process(?:es)?|restart|reboot|shutdown|computer|device|mac|trash)\b"
    intent = r"\b(?:open|launch|list|show|check|count|many|set|change|adjust(?:ed)?|increase|decrease|raise|lower|reduce|turn|mute|unmute|create|make|move|rename|trash|stop|kill|take|capture|restart|reboot|shutdown|shut|lock|sleep|current|installed|running)\b"
    return bool(re.search(subject, text, re.I) and re.search(intent, text, re.I))


def route_control_request(text):
    """Return one typed command, a clarification, or None for other requests."""
    explicit = _match(r"/?control(?:\s+([^\r\n]+))?", text.strip())
    if explicit:
        return {"command": explicit[1] or "status"}
    text = _request_text(text)
    if not text or "\n" in text or re.search(r"[;|]", text):
        return None
    if _match(r"(?:play|pleay|pause|resume)(?: (?:some|my|the))? music(?: on (?:my|the) (?:system|mac|computer))?", text):
        return {"reply": "Music playback control is not supported by this connector. Nothing played. I can open an installed music app when you ask, but cannot select songs or type inside it."}
    if re.match(r"^(?:open|launch)\b.+\band\s+(?:write|type|enter)\b", text, re.I):
        return {"reply": "Writing or typing inside apps is not supported by this connector. Nothing has run. You can ask to open the app, then enter the text yourself."}
    # Avoid promoting conditions, explanations, negation, or multiple targets
    # into a single executable proposal. Exact quoted names are still allowed.
    if re.search(r"\b(?:not|don't|never|unless|if|or|when|after|then)\b", text, re.I):
        return _clarify() if re.match(r"(?:move|rename|trash|open|create)\b", text, re.I) else None
    if re.search(r"\band\b", text, re.I) and not _match(r"(?:take|capture)(?: a)? screenshot and (?:tell|show) me where you (?:saved|save) it", text):
        return _clarify() if needs_control_evidence(text) else None
    if _match(r"(?:what|which|show|check|list)\b.*\b(?:system|device) controls\b.*", text):
        return _command("status")
    for pattern, parts in (
        (r"(?:list|show)(?: me)?(?: my| the)?(?: installed)? (?:apps|applications)", ("apps", "list")),
        (r"(?:list|show)(?: me)?(?: my| the)?(?: running)? processes", ("processes", "list")),
        (r"(?:what is|what's|show|check|read)(?: my| the)?(?: current)?(?: output)? volume", ("volume", "get")),
        (r"(?:what is|what's|show|check|read)(?: my| the)?(?: current)?(?: screen)? brightness", ("brightness", "get")),
        (r"mute(?: (?:my|the))?(?: (?:audio|sound|volume))?", ("volume", "mute")),
        (r"unmute(?: (?:my|the))?(?: (?:audio|sound|volume))?", ("volume", "unmute")),
        (r"(?:take|capture)(?: a)? screenshot(?: and (?:tell|show) me where you (?:saved|save) it)?", ("screenshot",)),
    ):
        if _match(pattern, text):
            return _command(*parts)
    level = _match(r"(?:set|change|increase|decrease|raise|lower)(?: my| the)? (volume|brightness) to (\d{1,3})\s*(?:percent|%)?", text)
    if level:
        return _command(level[1].lower(), "set", level[2])
    if _match(r"(?:increase|decrease|raise|lower|turn)\b.*\b(?:volume|brightness)\b.*", text):
        return {"reply": "Nothing has run. Give an absolute percentage, for example: Set volume to 40 percent."}
    power = _match(r"(restart|reboot|shut\s+down|shutdown|power\s+off|lock)(?: my| this| the)? (?:computer|device|phone|laptop|mac|system)", text)
    if power:
        kind = power[1].lower()
        return _command("power", "restart" if kind in {"restart", "reboot"} else "lock" if kind == "lock" else "shutdown")
    if _match(r"put(?: my| this| the)? (?:computer|device|laptop|mac|system) to sleep", text):
        return _command("power", "sleep")
    counting = _match(r"(?:find(?: out)? )?(?:how many|count(?: the)?|number of) (?:files|items)(?: (?:are there|are))?(?: (?:on|in))? (.+?)(?: folder)?", text)
    if counting:
        path = _path(re.sub(r"^(?:my|the) ", "", counting[1], flags=re.I))
        return _command("files", "count", path) if path else _clarify()
    listing = _match(r"(?:list|show)(?: me)?(?: the)? (?:files|contents) in (.+?)(?: folder)?", text)
    if listing:
        path = _path(re.sub(r"^(?:my|the) ", "", listing[1], flags=re.I))
        return _command("files", "list", path) if path else _clarify()
    mkdir = _match(r"(?:create|make)(?: a| another| the)? (?:folder|directory) (?:at )?(.+)", text)
    if mkdir:
        named = _match(r"(.+?) in (.+)", mkdir[1])
        if named:
            name, parent = _unquote(named[1]), _path(named[2])
            if parent and name not in {".", ".."} and not re.search(r"[/\\]", name):
                return _command("files", "mkdir", parent.rstrip("/") + "/" + name)
            return _clarify()
        path = _path(mkdir[1])
        return _command("files", "mkdir", path) if path else _clarify()
    trash = _match(r"(?:move|send) (.+?) to (?:gathm(?:'s)? recoverable |(?:the )?)trash", text)
    if not trash:
        trash = _match(r"trash (.+)", text)
    if trash:
        path = _path(trash[1])
        return _command("files", "trash", path) if path else _clarify()
    move = _match(r"(?:rename|move) (.+?) to (.+)", text)
    if move:
        source, target = _path(move[1]), _path(move[2])
        return _command("files", "move", source, target) if source and target else _clarify()
    process = _match(r"(?:stop|terminate)(?: the)? process(?: with)?(?: pid)? (\d+)", text)
    if process:
        return _command("processes", "stop", process[1])
    opened = _match(r"(?:open|launch) (.+)", text)
    if opened:
        target = re.sub(r"^(?:the )?(?:app|application|file|folder) ", "", opened[1], flags=re.I)
        if re.search(r"(?:[a-z]+://|\bwww\.)", target, re.I):
            return None  # URL navigation belongs to the browser tool.
        path = _path(target)
        if target.casefold() in {"music", "apple music"} and not re.match(r"(?:the )?(?:file|folder) ", opened[1], re.I):
            path = None  # "open music" means the app; an explicit folder stays a path
        return _command("files", "open", path) if path else _command("apps", "open", _unquote(target))
    return None


def read_only_followup(messages):
    """Resolve a short 'you do it' only to the immediately preceding read request.

    This is never an approval for a mutation or a search through old actions.
    """
    humans = [str(m.content) for m in messages if getattr(m, "type", None) == "human"
              and not str(m.content).lstrip().startswith(("Observation:", "Error:"))]
    if len(humans) < 2 or not _match(r"(?:you )?do (?:it|that|and tell me|it and tell me)", _request_text(humans[-1])):
        return ""
    plan = route_control_request(humans[-2]) or {}
    command = " ".join(shlex.split(plan.get("command", ""))[:2])
    return humans[-2] if command in {"status", "volume get", "brightness get", "apps list",
                                       "processes list", "files list", "files count"} else ""
