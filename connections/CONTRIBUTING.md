# Contribute a connection

Anyone can submit a GitHub pull request. A maintainer reviews each new connection
and each update before its version is included in the shipped catalogue.

For a public feed, contribute JSON and fixtures using the instructions in
[README.md](README.md). Keep service-specific fetching out of the reader and
delivery modules. Include a maintainer name and accurate access descriptions.

Run these checks from the repository root:

```sh
python -m connection_sdk
python -B -m unittest discover -s tests -v
```

Describe what the connection imports, its stable identity/deduplication rules,
the feed's date semantics, any limits, and how it behaves if the service is down.
Use synthetic fixtures. Never submit tokens, personal reading, account details
or captured authenticated responses.

Custom adapters are reviewed as trusted application code and ship in an app
release. Their discovery interface may receive only the service credential it
needs. The app retains model keys, email passwords and delivery control.

`CODEOWNERS` requests review from `@arbi11`. The repository's protected release
branch must also require a pull request and code-owner approval; CODEOWNERS
alone does not enforce that policy. Hashes pin content, not reviewer identity.

While this feature is being tested, target the `community-connections` branch.
After its integration, target the repository's maintained release branch.
