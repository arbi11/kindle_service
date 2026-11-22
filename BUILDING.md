# Native desktop builds

Build each operating system on its own runner. PyInstaller is not used as a
cross-compiler. The manually dispatched **Desktop installers** workflow builds
Linux x64, Windows x64, macOS Apple Silicon and macOS Intel from the same commit.
Only `main` is used for development; releases preserve versions with tags.

## Build

Use Python 3.12 and install `requirements-build.txt` in a dedicated virtual
environment, then run:

```sh
python build_desktop.py
python scripts/verify_installer.py
python scripts/smoke_agent.py
```

Linux needs `dpkg-deb`, Qt's desktop system libraries and a modern glibc base.
Windows needs the Inno Setup 6 compiler. macOS needs the Xcode command-line tools,
`codesign`, `ditto` and `hdiutil`. Python, Qt, the Pi agent, the pinned native Node
runtime and third-party notices are bundled. FFmpeg is optional and not bundled.

`build_agent.py` chooses the runtime for the host architecture and verifies its
pinned SHA-256 before extraction. It builds Pi from the npm lockfile and runs its
offline tests. Both the operating-system lock and the packaged Pi binary are
checked on their actual target system.

`--skip-agent` reuses an agent already built for that exact host. `--package-only`
finishes archives/installers from an existing native bundle, without recompiling.
Neither flag should be used to mix versions, architectures or unreviewed assets.

## Outputs and installation checks

| Target | Artifacts |
| --- | --- |
| Linux x64 | `.deb`, `.tar.gz`, SHA-256 list, build information |
| Windows x64 | Per-user setup `.exe`, SHA-256 list, build information |
| macOS arm64 / x64 | Architecture-specific `.dmg`, SHA-256 list, build information |

Installer verification extracts the Debian package, installs and uninstalls the
Windows setup, or mounts and copies the macOS app from its disk image. It starts
the packaged service against a temporary profile containing sample reading,
checks private-session API access, zero configured credentials, single-instance
behavior, helper-process stdio and graph filtering, and retains a reading-data
sentinel after uninstall. The Pi check uses an empty candidate list and a synthetic
placeholder key; it makes no network or model request.

These are automated service and installer checks. They do not assert that native
window behavior, every connector or Kindle delivery has been manually validated
on Windows and macOS. Never bundle the maintainer's local profile, screenshots of
personal reading, credentials, browser sessions or generated EPUBs. The build uses
explicit source/data paths rather than copying the checkout into an installer.

## Signing and release

Windows installers are currently unsigned. macOS bundles use ad-hoc signatures;
they are not Apple-notarized. Do not describe these as publisher-signed releases.
Developer ID signing/notarization and Windows publisher signing require the
owner's signing credentials and a separate signing setup.

Build workflows upload artifacts for review, not an automatic public release.
After all four native builds and the offline test workflow pass, prepare a draft
GitHub release targeting their exact source commit. The manually dispatched
**Publish desktop preview** workflow takes that build run ID, full commit and
version tag. It downloads the four artifacts on a GitHub runner, checks every
package's SHA-256 and build record, attaches the 13 expected files, and publishes
the draft as a prerelease. It refuses to overwrite a published release. Keep the
experimental platform/signing status in the notes. Do not move an existing tag.

References: [GitHub runner platforms](https://docs.github.com/en/actions/reference/runners/github-hosted-runners),
[PyInstaller usage](https://pyinstaller.org/en/stable/usage.html),
[Inno Setup compiler](https://jrsoftware.org/inno-setup/documentation/topic_compilercmdline.htm).
