# System controls

Gathm can inspect and control the device it runs on through chat, voice requests,
and the terminal. Read-only requests run directly. Changes, opening files/apps,
and screenshots require a one-time human confirmation. The GUI shows a trusted
card; the terminal asks a default-no question. The assistant cannot approve its
own proposal, and “yes” in chat/voice is not an approval.

This is separate from the existing opt-in `system <shell command>` tool. Typed
controls use fixed programs and validated arguments. They never install helper
software, grant permissions, invoke sudo/root, or bypass an OS restriction.

## Commands and examples

In conversation, ask “Set volume to 40 percent”, “Open Calculator”, “Create a
folder at ~/Documents/Notes”, or “Restart my computer”. Common requests route
directly to the typed control without model inference. Provide exact paths or
clarify the target when asked; relative volume changes need an absolute percentage.
In the TUI, use `/control` plus the command below, for example
`/control volume set 40`.

Control results are displayed directly, including cancellations and OS errors.
Lists retain their line breaks and do not pass through the model's shorter
observation limit. An unrecognized device request cannot substitute a model's
success or permission claim for a missing tool result. A correction or a chat
“yes” does not retry a previous action; restate the action and use its confirmation.

| Command after `control` | Behavior |
|---|---|
| `status` | Available controls, dependencies, and platform limits |
| `volume get` / `volume set 40` | Read volume or set a percentage (0–100) |
| `volume mute` / `volume unmute` | Desktop output mute state |
| `brightness get` / `brightness set 60` | Read/set display brightness where supported |
| `apps list` / `apps open "Calculator"` | Discover and open an installed app; paths resolve ambiguity |
| `files list "~/Downloads"` | List a home-directory folder |
| `files open "~/Documents/note.pdf"` | Open a document, media file, or folder; scripts/executables refused |
| `files mkdir "~/Documents/Notes"` | Create a new folder under an existing parent |
| `files move "~/old.txt" "~/Documents/new.txt"` | Move/rename without overwriting; same filesystem only |
| `files trash "~/old.txt"` | Move an item to Gathm's recoverable trash |
| `processes list` / `processes stop 1234` | List processes or request one owned process to stop |
| `screenshot` | Save a screen capture locally under `~/.gathm/screenshots` |
| `power lock` / `power sleep` | Lock or suspend this device |
| `power restart` / `power shutdown` | Request OS restart/shutdown after a warning and approval |

Quote app names and paths containing spaces. File controls require absolute or
`~/` paths and stay within the home directory. They reject symlinks, home-root
changes, credential/configuration directories, repository metadata, and Gathm's
own code. The narrow document-opening extension list intentionally excludes
scripts, executable files, and macro-enabled office formats.

Process controls reject PID 0/1, Gathm and its ancestors, other users' processes,
and targets whose identity changed after the proposal. POSIX sends SIGTERM;
Windows requests an ordinary GUI-window close. Neither force-kills a process.

## Platform support

`control status` reports the availability on the actual host; this table is the
implemented adapter coverage, not a claim that every platform was tested on
physical hardware.

| Control | macOS | Linux desktop | Windows | Rootless Termux |
|---|---|---|---|---|
| Volume | `osascript` | PulseAudio/PipeWire `pactl` | Unavailable | `termux-volume`; media stream |
| Brightness | Optional `brightness` utility | `brightnessctl` | WMI-supported displays | `termux-brightness` set only |
| Apps | Installed `.app` bundles | Installed `.desktop` entries through `gio` | Start-menu shortcuts | Use Android launcher |
| Files | Home directory | Home directory | Home directory | Termux home directory |
| Processes | Current user's processes | Current user's processes | Owned GUI apps with a closeable window | Subject to Android process visibility |
| Screenshot | OS screen-recording permission | `gnome-screenshot`, `grim`, or `scrot` | PowerShell/.NET screen capture | Unavailable |
| Power | OS permission / lock-helper availability | `systemctl` / `loginctl`, OS policy | Native OS requests | Unavailable; use Android controls |

Termux volume and brightness need both the Termux:API Android app and its command
package, with permissions granted by the user. Gathm does not silently install
them or enable Android debugging/root. iOS terminal sandboxes cannot control
the host device; file operations may work inside their own home directory.
Missing utilities, unsupported displays, and OS permission failures are shown
as errors. No alternate shell command is tried automatically.

## Confirmation, permissions, and recovery

The action applies to the **API host**, not necessarily the device displaying
the browser. Always read the device name and exact target in the approval card.
Restart, shutdown, and sleep can interrupt the connection and running work.
Save files first. A successful OS request is reported as requested, not as
proof that every application has finished closing.

The API requires `system:control` and `tool:execute`, both included in the default
admin role. Other roles do not receive system control automatically. Explicit
policy denials take priority. Approvals are memory-only, expire after two
minutes, and are consumed before execution. A new chat, cancellation, or restart
invalidates them. The plan and file/app/process identities are checked again
before execution; changes require a fresh proposal. Failed or disconnected
requests are never automatically replayed.

Gathm logs attempted changes in `~/.gathm/control.log`; inability to write the
initial audit record prevents a change. Logs can contain requested paths.
Screenshots stay local and may contain sensitive information visible on screen.

Trash items are stored under `~/.gathm/trash/<id>/item`, alongside
`original-location.json`. The result gives both paths and the original location.
To recover an item, move `item` back to that original location with your file
manager and restore its original name. Gathm does not permanently delete it or
empty the trash. Cross-filesystem moves fail rather than making a partial copy.

## Verification

The automated suites use disposable file fixtures and mocked OS adapters for
mutations. They cover input validation, platform commands, file/app/process
identity, no-overwrite moves, protected paths, default-no terminal confirmation,
owner-bound expiry, replay, permissions, JSON/SSE proposals, and GUI cancellation.

```bash
python -m unittest discover -s tests -p 'test_system_control*.py'
node tests/system_control_gui_test.js
node tests/streaming_browser_test.js
```

The browser suite uses a local stub; it does not restart, shut down, or change
the host. Physical-device testing and OS permissions are still required for
native Windows, Linux desktop, and Android behavior.
