"""Run the bundled Pi selection agent with credentials passed only over stdin."""
from __future__ import annotations

import json
import math
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

from .content import plain_text
from .feeds import public_url

TEXT_TOOLS = ['classify_article_text', 'verify_article_match']
TEXT_CATEGORIES = {'article_text', 'excerpt', 'podcast_description', 'login_page', 'unrelated', 'uncertain'}
MATCH_CATEGORIES = {'same_article', 'related_reporting', 'different_article', 'uncertain'}


def checked_text_review(data):
    """Recompute the verdict: Pi's prose or claimed status cannot approve a copy."""
    def answer(value, choices):
        if not isinstance(value, dict) or value.get('choice') not in choices:
            raise ValueError('Invalid publication text-check answer')
        for field in ('probability', 'confidence'):
            score = value.get(field)
            if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError('Invalid publication text-check probability')
        if type(value.get('input_tokens')) is not int or value['input_tokens'] < 0:
            raise ValueError('Invalid publication text-check usage')
        return value
    if not isinstance(data, dict):
        raise ValueError('Invalid publication text-check result')
    classification = answer(data.get('classification'), TEXT_CATEGORIES)
    article = classification['choice'] == 'article_text' and classification['probability'] >= .8
    match = answer(data.get('match'), MATCH_CATEGORIES) if article else None
    if not article and data.get('match') is not None:
        raise ValueError('Pi verified text before classification passed')
    expected = TEXT_TOOLS if article else TEXT_TOOLS[:1]
    turns, usage, policy = data.get('model_turns'), data.get('usage', {}), data.get('policy', {})
    if (data.get('runtime') != 'pi-agent-core' or data.get('phase') != 'text_review'
            or data.get('tools_used') != expected or type(turns) is not int or not len(expected) <= turns <= 3
            or data.get('decisions_model') != 'gpt-6-luna' or not isinstance(policy, dict)
            or type(policy.get('version')) is not int or policy['version'] != 1 or policy.get('tools') != TEXT_TOOLS
            or type(policy.get('max_tool_calls')) is not int or policy['max_tool_calls'] != 2
            or policy.get('article_probability') != .8 or policy.get('match_probability') != .9
            or not isinstance(usage, dict) or type(usage.get('requests')) is not int
            or usage['requests'] != len(expected) or type(usage.get('output_tokens')) is not int or usage['output_tokens'] != 0
            or type(usage.get('input_tokens')) is not int
            or usage.get('input_tokens') != classification['input_tokens'] + (match['input_tokens'] if match else 0)):
        raise ValueError('Pi did not follow the restricted text-check protocol')
    accepted = article and match['choice'] == 'same_article' and match['probability'] >= .9
    if data.get('status') != ('accepted' if accepted else 'rejected'):
        raise ValueError('Pi text-check verdict disagrees with tool evidence')
    return data


class PiSelection:
    def __init__(self):
        self.folder = Path(__file__).resolve().parent.parent / "agent"

    def select(self, request, *, provider, model, key):
        runtime = self.folder / ('runtime/node.exe' if os.name == 'nt' else 'runtime/node')
        node = str(runtime) if runtime.is_file() else shutil.which("node")
        script = self.folder / "dist/reader-agent.mjs"
        if not node or not script.is_file():
            raise ValueError("The Pi reading agent is missing from this build. Rebuild the desktop app with its agent runtime.")
        try:
            result = subprocess.run([node, str(script)], input=json.dumps({**request, "provider": provider,
                                    "model": model, "api_key": key}), text=True, capture_output=True,
                                    timeout=105, check=False)
            if len(result.stdout) > 64000:
                raise ValueError("Pi returned an oversized article selection")
            data = json.loads(result.stdout)
            if not isinstance(data, dict):
                raise ValueError("Pi returned an invalid article selection")
            if result.returncode or data.get("error"):
                message = str(data.get("error") or "Pi could not select articles")
                raise ValueError(message.replace(key, "[credential hidden]")[:700] if key else message[:700])
            expected = ["list_articles", "select_articles"] if request.get("articles") else []
            turns = data.get("model_turns")
            if (data.get("runtime") != "pi-agent-core" or data.get("tools_used") != expected
                    or type(turns) is not int or not (1 <= turns <= 3 if expected else turns == 0)
                    or not isinstance(data.get("selected"), list)):
                raise ValueError("Pi did not follow the restricted article-selection protocol. Nothing was retrieved.")
            return data
        except subprocess.TimeoutExpired:
            raise ValueError("Pi article selection timed out. Try again with a shorter preference.") from None
        except (json.JSONDecodeError, OSError):
            raise ValueError("The Pi reading agent could not start. Check this desktop build.") from None


class PiTextReview(PiSelection):
    def review(self, candidate, article, *, model, key, timeout=105):
        runtime = self.folder / ('runtime/node.exe' if os.name == 'nt' else 'runtime/node')
        node = str(runtime) if runtime.is_file() else shutil.which('node')
        script = self.folder / 'dist/reader-agent.mjs'
        if not node or not script.is_file():
            raise ValueError('The Pi text-check tools are missing from this build')
        text = plain_text(article.get('body_html', ''))
        detail = candidate.detail or {}
        payload = {'phase': 'text_review', 'provider': 'openai', 'model': model, 'api_key': key,
                   'review': {'id': hashlib.sha256((candidate.url + '\n' + text).encode()).hexdigest()[:32], 'original': {
                       'title': candidate.title[:1000], 'url': public_url(candidate.url),
                       'author': candidate.author[:300], 'published': candidate.published[:80],
                       'description': (detail.get('description') or plain_text(detail.get('feed_html', '')))[:700],
                       'date_known': detail.get('date_known') is not False},
                   'copy': {'title': article.get('title', '')[:1000],
                            'url': public_url(article.get('content_url') or article.get('url') or candidate.url),
                            'author': article.get('author', '')[:300], 'published': article.get('published', '')[:80],
                            'text': text[:24000], 'text_truncated': len(text) > 24000}}}
        try:
            process = subprocess.run([node, str(script)], input=json.dumps(payload), text=True, capture_output=True,
                                     timeout=min(105, timeout), check=False)
            if len(process.stdout) > 32000:
                raise ValueError('Pi returned an oversized text-check result')
            data = json.loads(process.stdout)
            if not isinstance(data, dict) or process.returncode or data.get('error'):
                message = str(data.get('error') or 'Pi could not check the article text') if isinstance(data, dict) else 'Invalid Pi text-check result'
                raise ValueError(message.replace(key, '[credential hidden]')[:400] if key else message[:400])
            if data.get('candidate_id') != payload['review']['id']:
                raise ValueError('Pi returned text checks for another copy')
            return checked_text_review(data)
        except subprocess.TimeoutExpired:
            raise ValueError('Pi article text checks timed out; this copy was not imported') from None
        except (OSError, json.JSONDecodeError):
            raise ValueError('The Pi article text checks could not start in this build') from None
