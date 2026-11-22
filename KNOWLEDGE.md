# Knowledge graph and concept pilot

The Knowledge → Graph view is an interactive, read-only map of the local library.
It includes all saved readings and their source connections. A smaller concept
pilot adds topics, shared concepts and suggested insights backed by saved passages.
Automatic concept extraction is not implemented. The pattern
adapts the owner's engineering second brain: reusable knowledge has attached
evidence and explicit applicability, rather than duplicating each source's ideas.

## What can be reviewed

**Knowledge** is always visible in the app's main navigation; Graph and Concepts are views within it. Nodes can be
dragged, the background pans, and scroll or the zoom buttons changes scale. Fit
graph restores the full view. Search finds nodes by name or description; selecting
one focuses it and shows its details, linked nodes and any supporting passages.
Readings open in the existing full reader. Returning preserves the selected node,
graph positions, viewport and active filters.

The left Filter stories selector supplies one shared connection scope for reading,
Graph and Concepts. The graph retains inclusive Published from / Published through
filters. Saved publication times are interpreted in the reader's configured
timezone. Apply filters rebuilds the graph from matching readings, retaining only
concepts, insights, topics and citations supported by those readings. Reset dates clears the date range while retaining the selected connection.
Empty ranges show an explicit empty state; invalid dates produce a clear error.

Connection edges mean "collected through this connection". They do not imply a
semantic relationship. Coverage counts distinguish readings with mapped concepts
from readings with source links only. Unmapped readings remain accessible and
never receive invented concepts. Changed or missing citations remove their
semantic edges rather than supporting a stale claim.

The renderer is the locally bundled vis-network 10.1.5 standalone build, with
checksums and license texts under `desktop/ui/vendor/vis-network`. The app makes
no CDN requests or calls to a graph service. The right panel remains Connections.

Knowledge → Concepts opens topics, then concepts, then suggested insights with
saved source passages when a local pilot is present.
Concepts can belong to multiple topics while retaining one identity. Different
perspectives remain separate insights under a shared concept.

Search covers pilot concept descriptions, topic names, insights, cited titles and
quoted passages. It does not imply semantic search or full-library indexing.
The left connection filter scopes the evidence and concepts; the right panel
retains connection settings. Narrow Knowledge windows place Connections beneath
the main content.

Read this passage opens the existing reader, selects the cited article or summary
and highlights its paragraph. Returning restores the concept, scroll position and
source button's keyboard focus. Summary evidence is labelled separately and never
presented as an original transcript.

All insights in this pilot are AI drafts describing source perspectives. Cited
text is locatable evidence, not an independent verification of the author's claim.
The prototype makes no provider calls and has no background extraction process.

## Local data

The optional file `knowledge/pilot.json` lives under the reader's data directory,
beside its private SQLite library. No personal pilot data ships with the app or
belongs in the source repository. Developer profiles and screenshots belong in
the ignored `artifacts/` directory. A fresh profile can still display a graph of
its readings and source connections without a concept pilot file.

Version 1 has four record collections:

| Collection | Required fields |
| --- | --- |
| topics | id, title, question, concept_ids |
| concepts | id, title, description, insight_ids |
| insights | id, text, origin (`ai_draft`), citation_ids |
| citations | id, item_id, content (`original` or `summary`), content_hash, paragraph, quote |

Evidence attaches to insights rather than forming another hierarchy layer.
Citation paragraphs use the sanitized leaf reading blocks defined in
`desktop.knowledge.passages`. The SHA-256 fingerprint identifies the exact saved
HTML version, and the quote must occur in the designated block. Changed or missing
sources retain their insight but disable the passage link with an explanation.
The reader also checks the text version immediately before opening a citation.

The existing private-session authentication covers `/api/knowledge` and
`/api/knowledge/graph?connection=all&from=YYYY-MM-DD&to=YYYY-MM-DD`. Reading the
pilot never updates reading state, delivery history, settings, or source content.
Invalid pilot data leaves ordinary reading available. There is no pilot write API.

## Validation

`python -B -m unittest discover -s tests -v` includes synthetic knowledge fixtures
for shared concepts, labelled summaries, explicit coverage, unchanged reading
state, version mismatches, missing sources, false quotes, malformed graphs and
authenticated API access. Browser review should additionally exercise topic and
concept navigation, shared concepts, search, connection filters, exact passage
opening, back navigation, and the existing reading list.

Automatic extraction, editable notes and highlights, acceptance of suggestions,
revision history, semantic retrieval and library questions remain future work
to discuss with the owner. This pilot does not claim those features are complete.
