# Grounded replies and recovery

Gathm must distinguish its model's knowledge from a tool result. A fluent reply
is not evidence that it searched a website, read Desktop files or launched an app.

## What changed

- Explicit public searches, such as `find who is hakxcore?`, route to the web tool
  before the model can guess. Common requests for latest/current facts also need
  fresh evidence. Queries keep the requested name rather than substituting a
  similar spelling. Search replies show actual snippets and source URLs directly;
  they are not proof that every result identifies the same person or account.
- An uncertain public answer can still trigger one bounded fallback search.
  Known local answers need no extra network request or model call. Explicit
  searches need no model call to plan or rewrite the result.
- Offline detection blocks internet tools. Failed searches pause for 30 seconds
  in that worker, explain that the information could not be verified, and do not
  invent a biography or claim that a lookup succeeded. General local knowledge
  and approved memory remain usable with a prepared local model. A hosted model
  cannot work without its provider connection.
- Recognized action-success claims without current-turn tool evidence are
  withheld. Device requests use typed controls and actual outputs. Tool errors
  end the turn with their real message, preventing generated package-install
  advice from being mistaken for a tested repair.
- `How many files are there on my Desktop?` uses `control files count ~/Desktop`.
  Counts cover all direct entries, including hidden entries, without following
  symlinks or recursing into folders. Files, folders and symlinks are separate.
  `You do and tell me` can retain that immediately preceding read-only target.
  It cannot approve app launches, deletion or another mutation.
- On macOS, `Apple Music`, `email`, `settings` and `safari browser` are aliases
  for installed applications, not invented paths. Inventory must contain one
  exact target. Launches still require confirmation. `Open music` chooses the
  app; `open folder music` chooses `~/Music`.
- Writing inside Notes and controlling local playback are unsupported here.
  Gathm reports that limit instead of inventing a Notes file or music-library
  workflow. The old `jukebox` tool is an interactive telnet demo, not control of
  the local Music app.

Instructions now use the system-message role. Chat sampling defaults to
`temperature=0.2`; `GATHM_LLM_TEMPERATURE` remains available for an explicit
preference. This reduces sampling variation; it cannot make a model truthful.

## After updating

Restart Gathm so the assistant worker loads the updated code, then start a fresh
chat. Earlier fabricated assistant messages in the same conversation can keep
priming its replies. Chat history and old learning records are not automatically
deleted. Review pending lessons before approval and retire any mistaken approved
lesson with `gathm harness archive LESSON_ID`.

Try a public exact-name search, a Desktop file-count request, a short read-only
follow-up, and an app launch. A search should show source URLs; a file count
should show a real directory result; a launch should show its approval and actual
tool outcome. With internet unavailable, a search should state the gap rather
than report guessed online results.

## Limits

English routing, uncertainty and privacy checks are conservative heuristics.
They do not detect every unsupported or confidently wrong statement. Search
snippets can be outdated, irrelevant or malicious, and matching handles do not
prove identity. Stable model answers can still be wrong. Supplied private
context is excluded from automatic queries, but no heuristic detects all PII.
Tool permissions, exact target validation and approvals remain necessary.
