"""Public-copy search must identify the article, bound work and keep provenance."""
from dataclasses import replace
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from connection_sdk import Candidate
from desktop.connections import Catalogue
from desktop.publication_search import PublicSearch, checked_results, run_worker
from desktop.publications import (checked_article_text, page_candidates, publication_values,
                                  MAX_COPY_PAGES, MAX_SEARCH_QUERIES)
from test_connections import response
from test_publications import SITE, STORY, TEXT, SOURCE_ID

COPY = 'https://syndication.example/news/the-patient-investor'
LEAD = 'Patient investors distinguish long-term company earnings from daily stock price movements.'


def copy_page(*, original=True, title='The patient investor and economic policy', short=False,
              author='A. Writer', published='2026-11-20'):
    canonical = f'<link rel="canonical" href="{STORY}">' if original else ''
    text = 'Only a short teaser.' if short else TEXT
    return (f'<html><head><title>{title}</title>{canonical}'
            f'<meta name="author" content="{author}"><meta property="article:published_time" content="{published}">'
            '</head><body><article><h1>' + title + '</h1><p>' + text + '</p></article></body></html>').encode()


class PublicSearchBridgeTests(unittest.TestCase):
    def test_only_query_enters_worker_and_ambient_credentials_are_removed(self):
        process = Mock(returncode=0, stdout=json.dumps({'results': [{'url': COPY, 'title': 'Patient investing'}]}))
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic-private-key', 'HTTP_PROXY': 'http://private-proxy'}), \
             patch('subprocess.run', return_value=process) as run:
            rows = PublicSearch().search('"Patient investing"')
        self.assertEqual(rows[0]['url'], COPY)
        self.assertEqual(json.loads(run.call_args.kwargs['input']), {'query': '"Patient investing"'})
        self.assertNotIn('OPENAI_API_KEY', run.call_args.kwargs['env'])
        self.assertNotIn('HTTP_PROXY', run.call_args.kwargs['env'])
        self.assertEqual(run.call_args.kwargs['timeout'], 12)
        self.assertIn('--publication-search', run.call_args.args[0])

    def test_unresponsive_search_worker_is_stopped(self):
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('worker', 12)), \
             self.assertRaisesRegex(ValueError, 'timed out'):
            PublicSearch().search('A public headline')

    def test_worker_output_limits_and_private_links(self):
        self.assertEqual(checked_results([{'url': 'https://127.0.0.1/private', 'title': 'Private'},
                                         {'url': 'https://host.local/file', 'title': 'Private'},
                                         {'url': 'https://name:password@journal.example/article', 'title': 'Private'}]), [])
        with self.assertRaises(ValueError): checked_results([{}] * 6)
        for query in ('', 'x' * 1201, 'Headline\nsecret'):
            with patch('subprocess.run') as run, self.assertRaises(ValueError): PublicSearch().search(query)
            run.assert_not_called()
        with patch('subprocess.run', return_value=Mock(stdout='x' * 32001)), self.assertRaisesRegex(ValueError, 'too much'):
            PublicSearch().search('Headline')

    def test_worker_runs_search_only_and_normalizes_public_results(self):
        from ddgs.ddgs import DDGS
        with patch('sys.stdin', io.StringIO(json.dumps({'query': 'Patient investing'}))), \
             patch('sys.stdout', new_callable=io.StringIO) as output, \
             patch.object(DDGS, 'text', return_value=[{'title': 'Patient investing', 'href': COPY, 'body': 'A description'}]) as search:
            code = run_worker()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['results'][0]['description'], 'A description')
        self.assertEqual(search.call_args.kwargs['max_results'], 5)


class PublicationCascadeTests(unittest.TestCase):
    def setUp(self):
        self.connection = Catalogue().get('publications')
        self.candidate = Candidate('id', 'The patient investor and economic policy', STORY, '2026-11-20T00:00:00+00:00',
                                   detail={'publication_id': SOURCE_ID, 'description': LEAD, 'date_known': True})

    def test_rss_media_entries_are_removed_before_model_selection(self):
        feed = (f'<rss><channel><item><title>Written article about patient investing</title><link>{STORY}</link>'
                '<description>A meaningful short description from the publisher.</description></item>'
                '<item><title>Is AI a Bubble? History Offers Some Clues</title>'
                '<link>https://journal.example/news/videos/2026/ai-bubble-video</link></item>'
                '<item><title>A weekly investing podcast</title><link>https://journal.example/weekly-podcast-episode</link>'
                '<enclosure type="audio/mpeg" url="https://journal.example/episode.mp3"/></item></channel></rss>').encode()
        entries = page_candidates(feed, 'https://journal.example/feed')
        self.assertEqual([entry.url for entry in entries], [STORY])
        self.assertIn('meaningful short description', entries[0].detail['description'])
        self.assertFalse(entries[0].detail['date_known'])

    def test_headline_variant_needs_independent_corroboration(self):
        url = 'https://journal.example/news/articles/goldman-s-spacex-pitch-helps-solve-ai-debt-puzzle-credit-weekly'
        lead = 'Goldman Sachs spent more than a year trying to finance new AI chip purchases without damaging corporate balance sheets.'
        candidate = replace(self.candidate, url=url, title='Apollo Prices $32 Billion of Bonds to Fund Chip Purchases',
                            detail={'description': lead})
        article = {'title': 'Goldman’s SpaceX Pitch Helps Solve AI Debt Puzzle: Credit Weekly',
                   'body_html': '<p>' + lead + TEXT + '</p>'}
        self.assertEqual(checked_article_text(article, candidate), article)
        with self.assertRaisesRegex(ValueError, 'different headline'):
            checked_article_text({**article, 'body_html': '<p>' + TEXT + '</p>'}, candidate)
        with self.assertRaisesRegex(ValueError, 'different headline'):
            checked_article_text({**article, 'title': 'An unrelated recipe for sourdough bread'}, candidate)

    def test_cascade_stops_after_first_usable_provider(self):
        trace = []
        def step(name, result=None, error=None):
            def read(candidate):
                trace.append(name)
                if error: raise ValueError(error)
                return result
            return read
        article = {'title': self.candidate.title, 'body_html': '<p>' + TEXT + '</p>', 'content_source': 'freedium'}
        with patch.object(self.connection, '_read_feed', side_effect=step('feed')), \
             patch.object(self.connection, '_read_publisher', side_effect=step('publisher', error='Unavailable')), \
             patch.object(self.connection, '_read_browser', side_effect=step('browser', error='Unavailable')), \
             patch.object(self.connection, '_read_freedium', side_effect=step('freedium', result=article)), \
             patch.object(self.connection, '_read_public_copy') as search:
            result = self.connection.read_article(self.candidate)
        self.assertEqual(trace, ['feed', 'publisher', 'browser', 'freedium'])
        search.assert_not_called()
        self.assertEqual(result['published'], self.candidate.published)

    def test_public_copy_is_reached_only_after_earlier_providers_fail(self):
        article = {'title': self.candidate.title, 'body_html': '<p>' + TEXT + '</p>',
                   'content_source': 'public_copy', 'content_url': COPY, 'url': STORY}
        with patch.object(self.connection, '_read_feed', return_value=None), \
             patch.object(self.connection, '_read_publisher', side_effect=ValueError('Not accessible')), \
             patch.object(self.connection, '_read_browser', side_effect=ValueError('Not accessible')), \
             patch.object(self.connection, '_read_freedium', side_effect=ValueError('Not available')), \
             patch.object(self.connection, '_read_public_copy', return_value=article) as search:
            self.assertEqual(self.connection.read_article(self.candidate)['content_source'], 'public_copy')
        search.assert_called_once()

    def test_readable_copy_preserves_original_identity_and_records_actual_location(self):
        with patch('desktop.publications.PublicSearch.search', return_value=[{'title': self.candidate.title, 'url': COPY}]), \
             patch('desktop.publications.PublicFeedHTTP.get', return_value=response(COPY, copy_page())):
            article = self.connection._read_public_copy(self.candidate)
        self.assertEqual(article['url'], STORY)
        self.assertEqual(article['content_url'], COPY)
        self.assertEqual(article['content_source'], 'public_copy')

    def test_matching_title_alone_cannot_turn_related_reporting_into_the_original(self):
        candidate = replace(self.candidate, detail={})
        for page in (copy_page(original=False), copy_page(short=True)):
            with self.subTest(page=page[:80]), \
                 patch('desktop.publications.PublicSearch.search', return_value=[{'title': candidate.title, 'url': COPY}]), \
                 patch('desktop.publications.PublicFeedHTTP.get', return_value=response(COPY, page)), \
                 self.assertRaisesRegex(ValueError, 'none of'):
                self.connection._read_public_copy(candidate)

    def test_author_and_date_can_corroborate_a_syndicated_copy(self):
        candidate = replace(self.candidate, author='A. Writer', detail={})
        for published, valid in [('2026-11-21', True), ('2021-11-20', False)]:
            with self.subTest(published=published), \
                 patch('desktop.publications.PublicSearch.search', return_value=[{'title': candidate.title, 'url': COPY}]), \
                 patch('desktop.publications.PublicFeedHTTP.get', return_value=response(COPY, copy_page(original=False, published=published))):
                if valid: self.assertEqual(self.connection._read_public_copy(candidate)['content_source'], 'public_copy')
                else:
                    with self.assertRaises(ValueError): self.connection._read_public_copy(candidate)

    def test_wire_credit_needs_matching_headline_and_date(self):
        candidate = replace(self.candidate, url='https://www.bloomberg.com/news/articles/2026-11-20/the-patient-investor', detail={})
        for byline, prefix, date, valid in [('Natalie Writer', '(Bloomberg) -- ', '2026-11-20', True),
                                          ('Another Writer', '', '2026-11-20', False),
                                          ('Natalie Writer', '(Bloomberg) -- ', '2021-11-20', False)]:
            page = copy_page(original=False, author=byline, published=date).replace(TEXT.encode(), (prefix + TEXT).encode())
            with self.subTest(byline=byline, prefix=prefix, date=date), \
                 patch('desktop.publications.PublicSearch.search', return_value=[{'title': candidate.title, 'url': COPY}]), \
                 patch('desktop.publications.PublicFeedHTTP.get', return_value=response(COPY, page)):
                if valid: self.assertEqual(self.connection._read_public_copy(candidate)['content_source'], 'public_copy')
                else:
                    with self.assertRaises(ValueError): self.connection._read_public_copy(candidate)

    def test_search_and_page_budgets_deduplicate_candidates(self):
        batches = [[{'title': self.candidate.title, 'url': f'https://copies.example/story-{i}'} for i in range(5)]] * 3
        with patch('desktop.publications.PublicSearch.search', side_effect=batches) as search, \
             patch('desktop.publications.PublicFeedHTTP.get', side_effect=ValueError('Unreadable')) as fetch, \
             self.assertRaises(ValueError):
            self.connection._read_public_copy(self.candidate)
        self.assertLessEqual(search.call_count, MAX_SEARCH_QUERIES)
        self.assertEqual(fetch.call_count, MAX_COPY_PAGES)
        self.assertEqual(len({call.args[0] for call in fetch.call_args_list}), MAX_COPY_PAGES)

    def test_search_can_be_disabled_independently_of_freedium(self):
        values = publication_values({'url': SITE, 'search_fallback': False, 'freedium_fallback': True})
        candidate = replace(self.candidate, detail=values['detail'])
        steps = [name for name, _ in self.connection.retrieval_steps(candidate)]
        self.assertIn('Freedium', steps)
        self.assertNotIn('Public web search', steps)
        with self.assertRaises(ValueError): publication_values({'url': SITE, 'search_fallback': 'yes'})

    def test_direct_video_attempt_does_not_launch_retrievers(self):
        with patch.object(self.connection, 'retrieval_steps') as steps, self.assertRaisesRegex(ValueError, 'video'):
            self.connection.read_article(replace(self.candidate, url='https://journal.example/news/videos/a-video'))
        steps.assert_not_called()
