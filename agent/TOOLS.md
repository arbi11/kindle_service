# Quib27 publication agent tools

These are two separate Pi runs. A run never receives all four tools together.
Tool names, questions, evidence fields, endpoints and call limits are defined in
reviewed app code. There is no general-purpose Decisions, HTTP, browser or shell tool.

## Choose articles

| Tool | Arguments | Effect |
| --- | --- | --- |
| `list_articles` | `{}` | Return up to 40 publisher-discovered IDs, titles, descriptions and URLs. |
| `select_articles` | `{selected: [{id, reason}]}` | Return up to 10 distinct supplied IDs, within the user's article limit. |

Exactly one call to each tool, in that order. At most three model turns and 90
seconds. Article bodies, sessions and credentials are absent from this transcript.

## Check one fetched copy

| Tool | Arguments | Fixed OpenAI Decisions question |
| --- | --- | --- |
| `classify_article_text` | `{candidate_id}` | Choose `article_text`, `excerpt`, `podcast_description`, `login_page`, `unrelated` or `uncertain`. |
| `verify_article_match` | `{candidate_id}` | Choose `same_article`, `related_reporting`, `different_article` or `uncertain`. |

The ID must be the sole host-supplied ID for that copy. No other arguments are
accepted: Pi cannot submit a URL, replacement text, prompt, model or approval.
The host retains the evidence; the tool sends only whitelisted fields to the fixed
`https://api.openai.com/v1/decisions` endpoint with `gpt-6-luna`.

Evidence includes original headline, public URL, byline, known date and up to 700
characters of publisher excerpt, plus fetched-copy metadata and up to 24,000
characters of plain text. A flag indicates truncation. Private source sessions,
cookies, passwords and local paths are not evidence. The API key is supplied over
stdin and used for HTTP authentication, never included in model input. Pi's
orchestrating model sees only the copy ID and fixed tool-result categories and scores.

Classification runs once first. It stops the review unless `article_text` has
selected-option probability at least 0.80. Only then may identity verification
run once; acceptance requires `same_article` probability at least 0.90. These are
initial conservative thresholds, not calibrated guarantees. Existing word-count,
headline and provenance checks still apply. A classification cannot establish that
every original paragraph is present.

At most two tool calls, one per model turn, three model turns and 90 seconds per
copy. Each Decisions request has a 15-second timeout, disallows redirects and has
bounded response size. The host caps reviews at four copies per selected article;
DDGS's remaining retrieval budget also limits its reviews. Repeats, wrong order,
unknown tools, extra arguments, fabricated IDs, refusals, malformed results and API
failures cannot approve a copy. The host independently checks the receipt and
recomputes acceptance from the two tool answers; Pi's prose cannot approve it.

Rejected copies leave retrieval free to try another provider or DDGS result.
With OpenAI configured, checks run before saving or summarizing Publications
articles. Other AI providers and reading without an OpenAI key retain existing
deterministic checks. Sol (or the user's configured OpenAI model) continues to run
Pi and summaries; Luna is confined to Decisions. Existing libraries are not rechecked.

Receipts contain categories, selected-option probabilities, tool sequence,
models, usage, source URLs and a text hash. They are stored in owner-only
`publication-checks/` files in the reader profile, with successful-job receipts
also included in `jobs.result.text_checks`. They contain no article bodies or keys.

API contract: https://developers.openai.com/api/docs/guides/decisions
