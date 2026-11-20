# Community connections

The app owns reading, AI summaries, storage, scheduling, newsletters and EPUB
delivery. Connections supply content candidates through connection API version 1.
YouTube, Raindrop, Substack, Websites & blogs and Freedium use that same discovery interface.

The first catalogue is bundled with the app and works offline. Only manifest
versions listed and hash-pinned in `catalogue.json` are loaded. An unlisted file,
Python entry point, or arbitrary downloaded package is never activated.

Community RSS/Atom connections are declarative JSON. They need no access to the
application's source, database, email password or model API key. New service
adapters require reviewed application code and an app release. We do not claim
that the trusted built-in Python adapters execute in an OS security sandbox.

## Add a feed connection

1. Create `connections/<service-id>/connection.json`. Use Freedium's manifest as
   a working example, or start from `template/connection.example.json`.
2. Give the connection a stable ID, semantic version, maintainer, description,
   setup type, access description, HTTPS feed URL and explicit allowed domains.
3. Run `python -m connection_sdk --pin <service-id>` to stage its version/hash.
   This stages a PR change; it does not approve or publish the connection.
4. Add small RSS/Atom and HTML fixtures and tests for that service's edge cases.
5. Submit a GitHub pull request following [CONTRIBUTING.md](CONTRIBUTING.md).

`python -m connection_sdk` validates the complete catalogue without dependencies.
CI also runs the connection and existing application tests.

## Connection contract

`connection_sdk.Candidate` contains a stable external ID, title, reading URL,
UTC publication time, attribution and content kind. Built-in adapters implement
`discover(session, source, since, token="")`. Only the Raindrop adapter receives
its Raindrop token. Summarization, transcription credentials and sending remain
host responsibilities.

The generic feed adapter reads RSS 2.0 or Atom, rejects XML entity declarations,
enforces response size limits and HTTPS domains, checks each redirect, drops
duplicate feed URLs, and ignores links outside declared hosts. The host extracts
and sanitizes the article, optionally summarizes it using the user's settings,
and preserves its connection identity even after disconnection.

Adding another reviewed feed definition requires no change to the reader, sidebar,
connection setup, collection engine or delivery code.

## Freedium v1

Freedium reads `https://freedium-mirror.cfd/rss`, the public Recently Unlocked
feed. It reads the linked article pages on that same host. No account is needed.
The feed dates describe recent unlocks and may differ from the original Medium
publication date. Availability and the latest feed entries depend on Freedium.

This version does not automatically redirect Raindrop links through Freedium.
Reading content for personal use does not transfer its author's ownership.

## User-selected publications

Substack and Websites & blogs are reviewed built-in adapters. They discover RSS
or Atom from a reader-selected public address and keep each subscription as its
own source. These are not arbitrary-domain permissions for declarative community
feed manifests: those still require explicit reviewed domains and a fixed feed URL.

The public transport blocks local/private DNS answers, pins the checked address
while retaining TLS hostname verification, and bounds response bytes and redirects.
Publisher content is sanitized and can be read without an AI key. RSS descriptions
and Atom summaries are treated as previews and use article extraction; full
content:encoded or Atom content can be read directly. Explicit paid previews are
not represented as complete articles. Community contributors can propose more
adapters through the existing GitHub review process.

## Review and releases

The catalogue is shipped with reviewed app releases. The index pins exact
manifest bytes and API compatibility. Future remote catalogue/package updates
need an explicit authenticity and compatibility design; this release never
installs arbitrary executable code from GitHub.

The core application's copyright terms remain in `desktop/COPYRIGHT.txt`.
