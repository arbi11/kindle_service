# Quib27 desktop

Quib27 is a free proprietary desktop reader. This 0.2.12 build runs on
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

Substack and Websites & blogs follow individual publications chosen by the reader.
Paste a Substack publication address (custom domains work), a website address, or
a direct RSS/Atom URL. Setup discovers the feed in a background task. Subscriptions
can be fetched, paused, resumed or disconnected in the right Connections panel.
The first fetch includes up to three latest public posts, subject to the existing
per-run cap, even if the publication has not posted inside the lookback window.
Later fetches follow that window and skip reading already in the library.

Publisher-supplied article content is preferred; shorter feeds use webpage
extraction. Explicit paid previews are skipped, and account-only content is not
supported. Feed and page HTML is sanitized before storage. User-selected feeds use
bounded HTTPS requests with redirect checks and public DNS addresses pinned for
each TLS connection. They cannot read the laptop's private network or provider keys.
There is no Substack account signup or subscription charge.

The first catalogue ships with the app and works offline. Public feed connections
are JSON definitions with bounded HTTPS access and an exact hash/version in the
reviewed index. Custom executable adapters require reviewed app code and a release.
See [the contributor guide](connections/README.md) and run `python -m connection_sdk`
to validate the catalogue. The core app's copyright terms are unchanged.

## Publications prototype (v0.2.11)

The right Connections panel now includes Publications. Add a publication or section
URL, describe what to read (and exclude), set an article limit, and choose whether
to try Freedium. Use **Sign in / reconnect** for subscriber access: complete login
in the local publication browser, then click **Finish sign-in**. **Fetch articles**
collects matching reading into the library. **Preferences** edits a saved source.

Retrieval is an ordered cascade: full feed content, publisher page, local
publication browser, optional Freedium, then optional **Search for public copies
if needed** using DDGS. Providers stop after a usable result; additional reviewed
retrievers can join this cascade later. The task status names the current stage.
Video and audio entries are excluded before AI selection.

DDGS 9.16.1 runs in a separate, bounded worker without reader credentials in its
input or environment. It searches through Yahoo, Mojeek and Startpage. The host
permits at most three queries and four candidate-page fetches within a 45-second
search/retrieval budget, with each search worker capped at 12 seconds. Only the
article's public title, URL-derived headline and optional byline enter search;
preferences, article bodies, local sessions and API keys do not. The search option
can be disabled independently of Freedium in a publication's Preferences.

Search snippets are never saved as articles. Public copies need readable body
text and matching identity evidence: a link to the original, a matching publisher
excerpt, or matching byline/date. An explicit wire credit with a close headline
and date can identify syndication. Related reporting is not silently substituted.
The reader labels a **Public copy**, links to the actual copy and retains the
original source link; duplicate detection still uses the original article URL.

Written preferences use the bundled Pi agent runtime and your configured AI key.
Pi lists candidates and selects matching IDs with reasons; no matches is a valid
result. Blank preferences fetch recent articles without AI selection. OpenAI agent
calls use Responses; existing summarization behavior stays under Settings.

Pi's selection run has exactly two tools: `list_articles` exposes only the candidate metadata
already collected by Quib27, and `select_articles` returns supplied IDs and reasons.
The runtime enforces one call to each tool, in that order, with at most 40
candidates, 10 selections, three model turns and 90 seconds per selection run. Invalid IDs,
unknown tools, repeated calls and oversized batches stop the run before retrieval.
The Python host also checks the completed tool sequence before accepting a result.
Full article bodies, cookies, passwords and the API key are not included in the model's
selection transcript. Shell, filesystem, browser, login, MCP and delivery tools
are not registered with Pi.

With OpenAI configured, each usable Publications copy also goes through a separate
Pi text-review run. Its only tools are `classify_article_text(candidate_id)` and
`verify_article_match(candidate_id)`. Each wraps a fixed question at OpenAI's native
Decisions endpoint using `gpt-6-luna`; the configured model still drives Pi and writes
summaries. Classification runs first. Only article text with probability at least
0.80 proceeds to identity verification, which needs `same_article` probability at
least 0.90. These initial thresholds supplement existing readability and identity
checks and do not guarantee completeness.

Pi cannot supply text, URLs, prompts, models or verdicts to these tools. Their
evidence comes from the host: public metadata, publisher excerpt and up to 24,000
characters of fetched plain text. The agent transcript contains only the copy ID
and tool results. Each review permits at most two calls, one per turn, three model
turns and 90 seconds; each Decisions request has a 15-second timeout. At most four
copies per selected article are reviewed, within DDGS's remaining budget when used.
Rejected copies allow retrieval to continue; an API failure cannot approve one.
Checks run before saving and summarization. Other providers retain existing checks.

Owner-only `publication-checks/` receipts in the profile record tool names,
categories, probabilities, models, token usage and text hashes, without body text
or credentials. Successful prepare jobs also include `text_checks` in their result.
See [the exact tool contracts](agent/TOOLS.md). The existing library is preserved
without a retroactive AI scan.

The host retrieves article text from the feed/public page, the local saved browser
session, or optional Freedium. Each publication has a separate local profile.
Short RSS content is fetched from the original page instead of being treated as
a complete article. Publications accepts extracted text only when it has a matching
headline and at least 200 words, as in the seven-day readability audit. A changed
headline can match the original article's URL slug when publisher excerpt or
source-link evidence corroborates it. Short or
mismatched results are reported for review. A known feed/index publication date
takes precedence over Freedium's displayed date.
Profiles currently have owner-only access without an added encryption layer.
Real subscriber logins, two-factor flows and site layouts can vary.

Freedium's direct import accepts original publication URLs as well as Medium links.
Availability depends on Freedium; unsupported pages are not saved as full articles.
No sending or scheduling is enabled by adding a publication. Existing reading and
Kindle services remain independent.

Public feed examples for preference-based selection include Bloomberg Markets
(`https://feeds.bloomberg.com/markets/news.rss`), NYT
(`https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml`) and Washington Post
Business (`https://feeds.washingtonpost.com/rss/business`). Add one in Publications
and enter your topics and exclusions. Feed discovery and full-text availability
are separate steps; an unavailable story is reported rather than delegated to an
unrestricted browsing agent. These are readability and identity checks, not a
guarantee of complete original-text coverage or future availability. A live DDGS
search hit is not counted as a successful import until article text passes the
checks and reaches the library.

For a development checkout, run `python build_agent.py` once before testing written
preferences. This builds pinned Pi packages and a private Node runtime; desktop
releases include both, so readers need no separate Node installation. See
[connection details](connections/README.md#publications-preferences-and-local-subscriber-sessions).

## The Daily Quib design preview

The **Medium** connection follows a list of creators supplied as handles or
profile links. Fetch articles per creator or use the existing reading schedule.
Medium excerpts automatically try Freedium. The **Freedium** connection also
accepts individual article links and saves readable text inside Quib27, with no
need to open its website. The reader identifies text provided through Freedium.
These paths work without an AI key; an unavailable article is reported and retried.

The **Knowledge graph** tab shows an interactive map of saved readings and their
connections. Drag nodes, pan, zoom, search and inspect linked source passages.
Filter by connection and an inclusive range of saved publication dates. Dates
use the reader's configured timezone. An optional local [concept pilot](KNOWLEDGE.md)
adds topics, shared concepts and suggested insights. Coverage counts distinguish
these mapped readings from readings with source links only. The graph makes no AI
calls and does not automatically extract concepts from the remaining library.

The app contains the agreed three-column layout:

- **Navigation** on the left: Today, Library, Sources, Delivery and Settings, with
  a separate Filter stories section underneath. A connection filters the digest
  or library; All connections restores the combined reading list.
- **The Daily Quib** in the middle, with the date, a compact article list, and
  Most recent ordering. Each entry shows its title, source, excerpt and reading
  time. Selecting it opens the full reader directly. Back restores the feed,
  connection filter, keyboard focus and scroll position.
- **Connections** on the right: every reviewed connection is listed. Selecting
  one expands its settings in place, including channel or collection setup and
  fetch, pause, resume and disconnect controls. The panel stays available while
  navigating the app. The expanded reader uses the full window.

Most relevant appears as an inactive sort option. Its ranking rules must be
discussed before implementation. Configuration screens and Library retain their
existing functions.

On the development laptop, open **Quib27** from the applications
menu or run `~/.local/bin/quib27`. This uses a separate preview
library with scheduling disabled. The installed v0.1 app is still **Daily Edition**.

To run the preview from a prepared development checkout:

```sh
.desktop-venv/bin/python daily_edition.py --data-dir artifacts/redesign-preview --no-keyring --no-scheduler
```

## Install the packaged app

Extract the release archive, open its Quib27 folder, and run `install.sh`.
This installs the app into `~/.local/opt/quib27`, adds it to the application
menu, and launches it. Alternatively, run the `Quib27` executable directly.
The packaged application includes Python and the desktop dependencies.

The app is currently built and tested on this Linux laptop. Windows and macOS
installers are separate future builds. Mobile apps and paid hosting are outside
this release.

## First reading

Use **Dark mode** at the bottom of the left panel or the sun/moon button in the
reader to switch the whole app between light and dark. **Settings → Appearance**
also offers a sepia reader. Appearance changes save immediately, survive restarts,
and preserve your current reading position and unsaved form entries.

1. Open Quib27. Clearly labelled sample articles let you try the reader.
2. In **Settings**, optionally add your AI key and reading interests.
3. Select a connection in the right-hand **Connections** panel.
   Add channel URLs or @handles for YouTube; Raindrop uses a personal token and
   selected collections. Substack takes a publication address; Websites & blogs
   accepts a site or feed address. These feeds and Freedium need no account.
   **Sources → Browse connections**
   also shows the reviewed catalogue; Your connections retains Add an article by URL.
4. Choose **Fetch articles** beside a source, or **Prepare edition** in Library
   to check all enabled sources. Reading is collected, summarized when applicable,
   and saved locally. Your reading budget and per-run cap keep editions manageable.
5. Select an article to open the full reader directly. Use the
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

Existing library and credential locations keep their original `daily-edition` names
so upgrading preserves your reading and keys. The default library is
`~/.local/share/daily-edition/library.sqlite3`, with private editions
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

`tests/subscriptions_ui.cjs` uses a fresh instance of the same fixture server to
check custom-domain Substack setup, website feed discovery, RSS/Atom full text,
sanitization, duplicate fetching, pause/resume and retained reading after disconnect.
