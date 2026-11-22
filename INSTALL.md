# Install Quib27

Download the installer for your computer from
[GitHub Releases](https://github.com/arbi11/kindle_service/releases/tag/v0.4.0).
Quib27 is free to use. You do not need a Quib27 account, a Kindle, Python, or Node
to run the packaged app. AI usage is billed separately by your chosen provider.

## Linux

On Ubuntu or Debian, download the `.deb`, then open it in your software installer,
or run `sudo apt install ./quib27-0.4.0-linux-x64.deb`. Open **Quib27** from the app menu.

For the portable `.tar.gz`, extract it, open the **Quib27** folder and run
`./Quib27`. To add a launcher for your user, run `sh install.sh`; this small
launcher installer needs the system's `python3`. No administrator access is needed.
The x64 build targets modern Linux distributions; the `.deb` is built on Ubuntu 22.04.

## Windows

Download `quib27-0.4.0-windows-x64-setup.exe`, close any running Quib27 window and
follow the setup wizard. It installs for your Windows user and adds a Start menu
shortcut. Uninstall through **Settings → Apps**. Your library is retained.

This preview installer is not publisher-signed. Windows may show an unknown
publisher warning or block it. Signed Windows distribution is not included yet.

## macOS

Choose **arm64** for Apple Silicon (M-series) or **x64** for an Intel Mac.
Open the `.dmg`, drag **Quib27** into **Applications**, then eject the disk image.

These are preview bundles with ad-hoc signatures, without Apple Developer ID
signing or notarization. macOS may block downloaded builds. They need signing and
notarization before a broad public launch; no security settings should be disabled.

## First setup

1. Open Quib27 and try the three labelled sample stories.
2. In the right **Connections** panel, expand **Websites & blogs** or **Substack**
   and add a publication or RSS feed. Public articles work without an AI key.
3. In **Settings → Preferences**, choose OpenAI or OpenRouter, enter your own API
   key and model ID, then save. **Check model** makes a small billable request.
4. Add YouTube channels, a Raindrop collection or another connection. Choose
   **Fetch articles** for the source.
5. Read **Today**, browse **Library**, or open **Library → Editions** to prepare
   an edition. Email and Kindle delivery are optional, under **Settings → Delivery**.

For Kindle, add your SMTP sender to Amazon's approved personal document sender
list. Your laptop must be awake, online and running Quib27 for scheduled work.
Launch at login is currently available in the Linux build only.

## Data, updates and removal

The app and your reading data live in different directories. Updating or removing
the application does not erase your library. Close Quib27 before installing an
update. Keep a backup of the data directory shown in **Settings → About**.

| System | Default reading data directory |
| --- | --- |
| Linux | `~/.local/share/daily-edition` |
| Windows | `%LOCALAPPDATA%\Daily Edition` |
| macOS | `~/Library/Application Support/Daily Edition` |

The older directory name preserves existing libraries. Custom `--data-dir`
locations, including the owner's preview installation, keep their existing path.
Credentials use the system credential store when available, otherwise a local
file. Backups can therefore contain private credentials and article text.

FFmpeg is optional and not bundled. It is needed only if you enable the YouTube
audio transcription fallback. AI processing sends the selected source text to
your chosen API provider; article retrieval contacts the relevant websites.

Checksums accompany every release. On Linux use `sha256sum -c SHA256SUMS-linux-x64.txt`;
on macOS use `shasum -a 256 -c SHA256SUMS-darwin-arm64.txt` (or the x64 file);
on Windows compare `Get-FileHash` against `SHA256SUMS-windows-x64.txt`.
