# Daily Edition desktop

Daily Edition is a free proprietary desktop reader. This 0.2.2 build runs on
64-bit Linux and keeps its library on your computer. It has no company account,
analytics, cloud library, or hosted service.

## Community connections: agreed direction

The platform supports community-contributed feed connections while the core app
remains proprietary. Contributors submit new connections and code updates through
GitHub pull requests. Review is required before a connection or new version is
listed in the catalogue. Review can later be delegated to trusted maintainers.

**Sources → Manage connections** has Your connections and Browse connections tabs. Browse
lists reviewed versions, descriptions and maintainers. Connect opens service setup
and displays access information for public feeds. Connected services and services
with saved reading appear in the left panel. Sources can be fetched, paused,
resumed or disconnected; disconnection retains their collected reading.

Freedium supplies its public Recently Unlocked feed without an account. Fetch
articles collects full article text and uses the app's existing summary settings.
It does not automatically redirect Raindrop links. Feed dates are unlock dates,
not necessarily the original article publication dates.

The first catalogue ships with the app and works offline. Public feed connections
are JSON definitions with bounded HTTPS access and an exact hash/version in the
reviewed index. Custom executable adapters require reviewed app code and a release.
See [the contributor guide](connections/README.md) and run `python -m connection_sdk`
to validate the catalogue. The core app's copyright terms are unchanged.

## Readers Digest design preview

The app contains the agreed three-column layout:

- **Navigation** on the left: Today, Library, Sources, Delivery and Settings, with
  a separate Filter stories section underneath. A connection filters the digest
  or library; All connections restores the combined reading list.
- **Readers Digest** in the middle, with the date, a compact article list, and
  Most recent ordering. Selecting an article opens a preview beneath its title.
  Expand opens the existing reader; Back restores the feed and its open preview.
- **Connections** on the right: every reviewed connection is listed. Selecting
  one expands its settings in place, including channel or collection setup and
  fetch, pause, resume and disconnect controls. The panel stays available while
  navigating the app. The expanded reader uses the full window.

Most relevant appears as an inactive sort option. Its ranking rules must be
discussed before implementation. Configuration screens and Library retain their
existing functions.

On the development laptop, open **Daily Edition Preview** from the applications
menu or run `~/.local/bin/daily-edition-preview`. This uses a separate preview
library with scheduling disabled. The installed v0.1 app is still **Daily Edition**.

To run the preview from a prepared development checkout:

```sh
.desktop-venv/bin/python daily_edition.py --data-dir artifacts/redesign-preview --no-keyring --no-scheduler
```

## Install the packaged app

Extract the release archive, open its DailyEdition folder, and run `install.sh`.
This installs the app into `~/.local/opt/daily-edition`, adds it to the application
menu, and launches it. Alternatively, run the `DailyEdition` executable directly.
The packaged application includes Python and the desktop dependencies.

The app is currently built and tested on this Linux laptop. Windows and macOS
installers are separate future builds. Mobile apps and paid hosting are outside
this release.

## First reading

1. Open Daily Edition. Clearly labelled sample articles let you try the reader.
2. In **Settings**, optionally add your AI key and reading interests.
3. Select YouTube, Raindrop or Freedium in the right-hand **Connections** panel.
   Add channel URLs or @handles for YouTube; Raindrop uses a personal token and
   selected collections. Freedium needs no account. **Sources → Browse connections**
   also shows the reviewed catalogue; Your connections retains Add an article by URL.
4. Choose **Fetch articles** beside a source, or **Prepare edition** in Library
   to check all enabled sources. Reading is collected, summarized when applicable,
   and saved locally. Your reading budget and per-run cap keep editions manageable.
5. Select an article for an inline preview and Expand for the full reader. Use the
   summary/full article or transcript tabs, text-size controls, theme button,
   bookmark button, and **Mark as read**. Preferences and reading state survive
   app restarts. The `/` shortcut opens library search; Escape returns from the reader.

If this laptop already has the original Kindle services, **Import existing setup**
copies source subscriptions, credentials, known delivery identifiers and up to eight
recent books. It never edits the original configuration or timers. Imported editions
are labelled imported. Automatic preparation and sending remain paused in the app.
Pause the original timers yourself before enabling equivalent app deliveries, so
the two independent services do not deliver overlapping future content.

For a **one-time reading-only archive import**, close the desktop reader and run:

```sh
.desktop-venv/bin/python import_reading_archive.py --data-dir ~/.local/share/daily-edition-preview --dry-run
.desktop-venv/bin/python import_reading_archive.py --data-dir ~/.local/share/daily-edition-preview
```

This separate utility reads retained sent books and delivery metadata from
`~/.local/state/raindrop-kindle` and `~/.local/state/youtube-kindle`. It backs up the
reader database, copies the original EPUBs into the reader's `reading-archive`
folder, and imports distinct stories with their connection, attribution and dates.
YouTube edition overviews are excluded from the story list; retained takeaways are
kept with their video. Missing saved summary text is labelled explicitly. Books
older than the services' capped delivery logs are retained as archive copies;
recent files absent from delivery history are excluded. Repeating the import
preserves reading state and bookmarks and does not duplicate imported editions.

The utility copies no credentials, settings or subscriptions, makes no AI or mail
calls, and installs no synchronization or watcher. After the import, the reader
works from its own copies and generates future reading through its own configured
connections. The original services continue their existing Kindle deliveries.
The reading list loads additional local pages so histories larger than 200 or 500
stories remain accessible.

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

The earlier `desktop-app` and `desktop-redesign` branches preserve the v0.1 and
approved reading-layout checkpoints. Connection development is on `community-connections`.


For the connection-specific browser checks, run an isolated fixture server with
`python tests/connection_ui_server.py --data-dir /temporary/connection-ui`, then
`node tests/connections_ui.cjs /temporary/connection-ui/runtime.json`. It tests
catalogue setup, fetching, pause/resume, disconnection, filters and EPUB export
using synthetic service responses. It blocks real mail and external network.

With another fresh fixture profile, `node tests/panels_ui.cjs /temporary/panel-ui/runtime.json`
checks the three-panel layout, inline connection setup, credential checks, story
filters, keyboard controls, and preservation of unsaved forms during connection jobs.
