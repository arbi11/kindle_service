# Kindle Service

The **Quib27 desktop app** adds a local reading library, source setup,
AI preferences, and email/Kindle delivery controls. See [desktop installation and
usage](DESKTOP.md). The original command-line services are documented below.

Desktop v0.2 adds **Your connections / Browse connections**, a reviewed connection
catalogue, and Freedium's public reading feed alongside YouTube and Raindrop.
See [community connection development](connections/README.md) to contribute a
feed connection through GitHub review.

Two services that run on a Linux laptop and email EPUB digests to Kindle:

- **Raindrop to Kindle** collects newly saved bookmarks, extracts articles and images, and builds a book with a short keyword for each article.
- **YouTube to Kindle** watches selected channels, collects captions or transcribes audio, writes summaries around the reader's interests, and combines them into a book.

Both use systemd user timers. Every five minutes the timers check whether a scheduled digest is due. The services catch up after sleep or missed slots and record delivered items to avoid repeating them. Kindle delivery uses SMTP and Amazon's Send-to-Kindle email address.

## Requirements

- Linux with a systemd user session.
- Python 3.11 or later, including `venv` and `pip` support.
- FFmpeg (`ffmpeg` and `ffprobe`).
- A Raindrop test token, an SMTP account, and an approved Send-to-Kindle address.
- An OpenAI project API key for summaries and cloud transcription, or an OpenRouter key when that provider is selected.

For Ubuntu/Debian, install the system dependencies with:

```bash
sudo apt-get update
sudo apt-get install python3-venv ffmpeg
```

## Install

```bash
git clone https://github.com/arbi11/kindle_service.git
cd kindle_service
python3 install.py
```

The installer creates a virtual environment, installs the pinned dependencies, copies the runtime to `~/.local/share/raindrop-kindle/`, and installs launchers in `~/.local/bin/`. Add `~/.local/bin` to your `PATH` if necessary.

Existing configuration, credentials, channels, and delivery history are preserved. Changed installed files are backed up under `~/.local/share/raindrop-kindle/backups/`. The installer pauses active timers during an update and resumes them afterward; it asks you to wait if a batch is running. Fresh installations leave timers stopped until setup is complete.

Use `python3 install.py --help` for custom installation paths or file-only installation on a machine without a systemd user session.

## Configure

Run the interactive setup from a terminal:

```bash
raindrop-kindle setup --provider openai
youtube-kindle setup
youtube-kindle add @ExampleChannel
```

The first command asks for your Raindrop test token, Kindle email, SMTP details, and OpenAI key. Secret input is hidden. Gmail needs an App Password, and the sender must be on Amazon's approved personal document email list. The YouTube setup records your interests; `add` accepts channel names, handles, or URLs.

Settings live outside the checkout:

| Path | Contents |
| --- | --- |
| `~/.config/raindrop-kindle/config.toml` | Raindrop, email, schedule, and shared AI settings |
| `~/.config/raindrop-kindle/openai_api_key` | Private OpenAI key file |
| `~/.config/youtube-kindle/config.toml` | Summary length, interests, schedule, and transcription limits |
| `~/.config/youtube-kindle/channels.json` | Followed channels |
| `~/.local/state/raindrop-kindle/` | Delivery history, books, and previews |
| `~/.local/state/youtube-kindle/` | Video state, transcripts, books, and previews |
| `~/.cache/youtube-kindle/` | Temporary audio and local Whisper models |

See [the blank configuration examples](config/) for editable fields. The applications also honor `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, and, for YouTube's cache, `XDG_CACHE_HOME`.

### AI provider

The shared `[ai]` section selects `provider = "openai"` or `provider = "openrouter"`. OpenAI settings use `gpt-6-luna` for text and `gpt-transcribe` for audio. Separate summary, editor, candidate-writer, and decision models can be configured. The original OpenRouter model settings are retained when switching providers.

To change only the provider and its key:

```bash
raindrop-kindle setup --ai --provider openai
```

OpenAI keys are loaded from a configured private key file, an explicit inline setting, or `OPENAI_API_KEY` when no key file is configured. A terminal's environment is not automatically inherited by systemd services, so use the private file for scheduled runs. The setup command stores that file with permissions `0600`.

OpenAI API credits apply to the organization holding the credit. Create a project key in that organization. See the [official credit documentation](https://developers.openai.com/api/docs/guides/chatgpt-api-credits), [text model](https://developers.openai.com/api/docs/models/gpt-6-luna), and [transcription model](https://developers.openai.com/api/docs/models/gpt-transcribe).

Selecting OpenAI never automatically sends a failed request to OpenRouter. Keyword selection can fall back to the local picker, length planning can fall back to transcript length, and transcription can use local Whisper under the existing fallback rules. Failed summaries are retried on a later run.

### Transcription and schedules

YouTube captions are preferred. If unavailable, cloud transcription is bounded by `cloud_max_minutes_per_video` and `cloud_minutes_per_run` (defaults: 30 and 90 minutes). Native OpenAI transcription uses 30-second clips to anchor timestamps to the audio. Whisper is the local backup and can download its model on first use.

Digest times are configured separately from the five-minute timer check. Raindrop's slots use its configured timezone. YouTube slots include an IANA timezone per slot, for example `"10:00 Asia/Kolkata"`. Edit the TOML files to change them.

## Preview before enabling delivery

These commands build local previews without sending mail:

```bash
raindrop-kindle preview --hours 24
youtube-kindle preview --hours 24
```

Preview commands can fetch source content and incur AI charges. To test OpenAI with a saved SRT without fetching a video or touching delivery history, run from the checkout:

```bash
~/.local/share/raindrop-kindle/.venv/bin/python preview_openai.py --transcript /path/to/saved.srt
```

This writes `summary.txt` and a request/usage report under the ignored `artifacts/openai-preview/` directory. Kindle delivery is disabled. It uses your locally configured key; `--key-file` can override it. Check billed usage in the [OpenAI dashboard](https://platform.openai.com/usage).

When configuration and previews look right, enable both timers:

```bash
systemctl --user enable --now raindrop-kindle.timer youtube-kindle.timer
```

## Operate and update

```bash
raindrop-kindle status
youtube-kindle status
youtube-kindle channels
journalctl --user -u raindrop-kindle.service --since today
journalctl --user -u youtube-kindle.service --since today
```

`send-now`, `send-video`, and `backfill` can deliver real emails; read their `--help` before running them. `youtube-kindle backfill --dry-run` builds historical books without delivery. To pause scheduled checks:

```bash
systemctl --user stop raindrop-kindle.timer youtube-kindle.timer
```

To install a later revision:

```bash
git pull --ff-only
python3 install.py
```

Updating the checkout alone does not replace installed runtime files; run the installer afterward. Keep configuration and delivery state when moving machines so that previously delivered content is not resent.

## Tests

Tests use synthetic data and mocked AI responses. They build EPUBs, check provider routing, exercise failure handling and transcription boundaries, and install into temporary directories. They make no API calls and send no emails.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python -B -m unittest discover -s tests -v
```

FFmpeg is needed for the audio and installation checks. GitHub Actions runs the same tests on pushes and pull requests.

## Repository data

Commit source code and blank examples only. Real configuration, keys, email addresses, subscriptions, transcripts, EPUBs, audio, logs, and generated reports belong in the local paths above. `.gitignore` excludes common secret and output locations. No live credentials or saved personal content are required to run the tests.
