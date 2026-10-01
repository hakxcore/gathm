# GUI and terminal compatibility

Last checked: 2026-10-01. These results distinguish native execution from
simulated platform checks. They do not certify every device or operating system.

## Verified locally

| Area | Result | Scope |
|---|---|---|
| macOS terminal | Passed | Actual launcher, local llama.cpp reply, `/speak` status, clean exit |
| macOS API and model | Passed | Identity, writing help, and conversation context using the existing Llama 3.1 8B GGUF |
| macOS live GUI | Passed | Typed message sent through the GUI, API, and local model; reply displayed and input controls restored |
| GUI browser integration | 130 checks passed in each of two Chromium builds | Google Chrome 154 and Chrome for Testing 151 on macOS; synthetic microphone and stub API |
| Mobile layout | Passed | Emulated widths 320, 375, 412, 768 and landscape 812×375; desktop 1024 and 1440; touch input and usable controls |
| Python suites | 124 tests passed | Includes assistant, API, terminal rendering, and platform decision regressions |
| Installer and launcher | Passed | 61 launcher checks, 14 venv path fixtures, 18 reuse cases, 55 llama.cpp installer checks, 98 packaging checks |

The GUI checks cover chat persistence, reset, microphone denial, unavailable
voice, keyboard composition, multiline input, speech interruption, and the
return to listening. Terminal layouts fit 32, 40, 80, and 120 columns.
Actual microphone capture remains unverified; the live browser reported a
microphone permission denial and kept text chat available.

## Remaining native checks

| Platform | Current evidence and limits |
|---|---|
| Android / Termux | No phone was attached during this pass. Android recognizer and TTS decisions have regression coverage, but the changed build still needs a physical phone test. |
| Linux | No running local Linux container/VM was available. Linux branches are exercised by fixtures; native GUI/TUI execution remains to be run. |
| Windows through WSL | Uses the Linux setup path. A native WSL session was unavailable for this pass. |
| Windows through Git Bash / MSYS2 | Windows venv selection, paths with spaces, and process cancellation were fixed and checked with fixtures. These are not native Windows execution results. Native microphone capture has no supported Windows recorder backend. |
| iOS / Safari / Firefox | No native mobile device or separate WebKit/Firefox test engine was available. Chromium mobile emulation is not a substitute for these checks. No native iOS terminal setup is documented. |
| Docker | Missing assistant dependencies were added with a build-time import check. Intended targets are amd64 and arm64; ARMv7 is unsupported by the bundled Playwright distribution. The image was not built because the Docker daemon was unavailable. A configured model server is still required. |

The most recent [GitHub test run](https://github.com/hakxcore/gathm/actions/runs/36260042560)
could not start its Linux/macOS jobs because of an account billing lock. CI
cannot supply native verification until that is resolved. The new venv fixture
is included in the existing workflow; it does not introduce a Windows runner.

## Fixes from this pass

- Short browser windows use a scrolling layout so landscape chat and headings
  remain visible.
- Android `/listen` allows the built-in recognizer to capture audio without a
  separate recorder.
- `/speak` reports the selected system voice instead of requiring audio.cpp for
  every platform.
- Windows speech cancellation does not rely on the POSIX-only `SIGKILL` constant.
- Startup, installation, verification, and shortcuts recognize both POSIX
  `bin/python` and Windows `Scripts/python.exe` virtual environments.
- Launcher tests use their own model probe port, preserving any live host model.

## Completing a native device check

On each target, start `gathm tui` and `gathm gui` with a configured model. Confirm
a typed reply and a follow-up, `/speak` and `/listen` availability, microphone
permission denial and recovery, stopping a conversation, and a clean exit.
On phones, check portrait, landscape, the on-screen keyboard, and backgrounding
the browser. Speech depends on the installed runtime and OS permissions; text
chat should remain usable when speech is unavailable.

Browser microphone access requires HTTPS or localhost. A phone opening a Mac's
plain HTTP LAN address will not have the same microphone access as a GUI served
locally inside Termux. Do not expose the API to a network without configuring
its access controls.
