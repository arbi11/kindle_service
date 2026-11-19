# Daily Edition desktop

Daily Edition is a free proprietary desktop reader. This 0.1.0 build runs on
64-bit Linux and keeps its library on your computer. It has no company account,
analytics, cloud library, or hosted service.

## Readers Digest design preview

The `desktop-redesign` branch contains the agreed three-column layout:

- **Connections** on the left: All articles, YouTube, and Raindrop. A connection
  filters the middle feed; All articles restores the combined feed.
- **Readers Digest** in the middle, with the date, a compact article list, and
  Most recent ordering. Selecting an article opens a preview beneath its title.
  Expand opens the existing reader; Back restores the feed and its open preview.
- The existing reading budget, followed-source count, unread count, delivery
  status, routine link, and quote on the right.

Most relevant appears as an inactive sort option. Its ranking rules must be
discussed before implementation. Other configuration screens and Library are
retained from v0.1 and have not been redesigned in this step.

On the development laptop, open **Daily Edition Preview** from the applications
menu or run `~/.local/bin/daily-edition-preview`. This uses a separate sample
library with scheduling disabled. The installed v0.1 app is still **Daily Edition**.

To run the preview from a prepared development checkout:

```sh
.desktop-venv/bin/python daily_edition.py --data-dir artifacts/redesign-preview --no-keyring --no-scheduler
```

The instructions below describe the v0.1 setup and delivery flows.

## Install the packaged app

Extract the release archive, open its DailyEdition folder, and run `install.sh`.
This installs the app into `~/.local/opt/daily-edition`, adds it to the application
menu, and launches it. Alternatively, run the `DailyEdition` executable directly.
The packaged application includes Python and the desktop dependencies.

The app is currently built and tested on this Linux laptop. Windows and macOS
installers are separate future builds. Mobile apps and paid hosting are outside
this release.

## First reading

1. Open Daily Edition. The clearly labelled sample edition lets you try the reader.
2. Choose **Make it yours** and optionally add your AI key and reading interests.
3. In **Sources**, add YouTube channel URLs or @handles, Raindrop collections, or
   individual article URLs. Raindrop uses a personal token created in its own
   integrations settings. Check connection loads your available collections.
4. Choose **Prepare edition**. New reading is collected, summarized when applicable,
   and saved locally. Your reading budget and per-run cap keep editions manageable.
5. Open a piece to read. Use the summary/full article or transcript tabs, text-size
   controls, theme button, bookmark button, and **Mark as read**. Preferences and
   reading state survive app restarts. The `/` shortcut opens library search;
   Escape returns from the reader.

If this laptop already has the original Kindle services, **Import existing setup**
copies source subscriptions, credentials, known delivery identifiers and up to eight
recent books. It never edits the original configuration or timers. Imported editions
are labelled imported. Automatic preparation and sending remain paused in the app.
Pause the original timers yourself before enabling equivalent app deliveries, so
the two independent services do not deliver overlapping future content.

## Email and Kindle delivery

In Delivery, choose newsletter email and/or Kindle, add recipients, and configure
your SMTP server. Gmail requires an App Password. For Kindle, add the sender to
Amazon's Approved Personal Document E-mail List.

**Check connection** signs in without sending mail. **Preview & send** displays the
edition and recipients before sending. Newsletters contain the complete reading
edition in their body. Kindle messages attach an EPUB. The app records acceptance
by the mail server; Amazon and inbox delivery happen afterwards.

Successfully sent edition/recipient pairs are skipped on subsequent sends. If a
connection fails during submission, a delivery is marked uncertain and is not
automatically repeated. Check your mailbox before explicitly resolving that entry.

Choose preparation times and a timezone to enable automatic editions. Sending is
a separate switch. Keep the app running and the laptop awake and online. With a
schedule enabled, closing the window minimizes to the system tray. Quit from the
tray menu to stop it. Launch at login is optional. Missed slots collapse into one
catch-up edition rather than a backlog of sends.

## Local data and provider access

The library is `~/.local/share/daily-edition/library.sqlite3`, with private editions
and transcripts alongside it. The directory allows owner access only. Credentials
use the operating system credential store when available, otherwise an owner-only
local file; Settings reports the actual storage mode. Keys are never returned by
the local API or included in exports. The local interface is bound to loopback and
requires a private session and same-origin mutations.

AI text goes directly to the selected provider using the user's key. Provider usage
is billed independently; it is not included in the free app. OpenAI and OpenRouter
are supported with editable model IDs. There is no paid-provider fallback. Articles
can be read without AI. Source permissions, paywalls, and YouTube caption blocking
can prevent extraction; failures appear in Sources and are eligible for later retry.

Video captions are preferred. Optional cloud audio transcription starts disabled,
has a per-run minutes budget, and requires FFmpeg/ffprobe on the computer. Local
Whisper is not included in this desktop build. EPUB and HTML export work offline
for reading already in the library.

## Develop and verify

Create a Python 3.11+ virtual environment and install `requirements-build.txt`.
Run `python daily_edition.py` for the desktop shell, or `--headless` for the local
service. `--data-dir /temporary/path --no-keyring --no-scheduler` creates an isolated
test instance. The session details are stored owner-only in that directory.

`python -B -m unittest discover -s tests -v` runs the offline Python checks; the
legacy keyword tests additionally need wordfreq from requirements-test.txt. FFmpeg
is used by transcription boundary tests. No tests send actual mail.

With Playwright and Chrome installed, `node tests/desktop_ui.cjs /temporary/path/runtime.json`
exercises reading controls, state, search, source management, real local article
extraction, edition preparation, download, settings and delivery preview against an
isolated running service. It uses a synthetic local article and makes no AI or SMTP
calls. Optional environment variables: PLAYWRIGHT_MODULE and CHROME_EXECUTABLE.

Run `python build_desktop.py` to create the bundled executable and release archive
under dist/. The build explicitly includes UI assets and application code, never
the local library, transcripts, credentials or test artifacts. It replaces EbookLib
with a ZIP/XML EPUB writer. License notices are included in the release; review
third-party distribution obligations before a wider public release.

All application changes for this release are local to the desktop-app branch.
No product source has been published from that branch.
