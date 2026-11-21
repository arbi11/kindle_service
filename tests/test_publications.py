"""Publication selection/session boundaries, using synthetic publisher content."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from connection_sdk import Candidate
from desktop.content import plain_text
from desktop.engine import Engine
from desktop.agent import PiSelection
from desktop.medium import FREEDIUM_ORIGIN, fetch_freedium, freedium_address
from desktop.publication_browser import PublicationBrowser
from desktop.publications import (canonical_url, candidate_url, extract_article, page_candidates,
                                  publication_values, select_articles)
from desktop.secrets import Secrets
from desktop.store import Store, now
from test_connections import response
from test_medium import mirror_page

SITE = "https://journal.example/business"
STORY = "https://journal.example/business/the-patient-investor"
OTHER = "https://journal.example/business/todays-stock-tips"
SOURCE_ID = "a" * 32
TEXT = "Patient investors distinguish long-term company earnings from daily stock price movements. " * 14


def index_page():
    return (f'<html><head><title>A personal journal</title></head><body><main><h1>Business</h1>'
            f'<article><h2><a href="{STORY}?utm_source=email">The patient investor and economic policy</a></h2>'
            '<p>A long-term view of policy and investing.</p><time datetime="2026-11-20T08:00:00Z"></time></article>'
            f'<article><h2><a href="{OTHER}">Today’s five speculative stock tips</a></h2></article>'
            '<a href="/subscribe">Subscribe for unlimited access</a>'
            '<a href="https://unrelated.example/a-long-article">Unrelated article with a long title</a>'
            '<a href="https://127.0.0.1/private">A private network article</a>'
            '</main></body></html>').encode()


def story_page(*, paid=False):
    body = '<p>Subscribe to continue reading this article.</p>' if paid else f'<p>{TEXT}</p><p>The final paragraph is included.</p>'
    return (f'<html><head><title>The patient investor</title><meta property="og:type" content="article">'
            '<meta name="author" content="A. Writer"><meta property="article:published_time" content="2026-11-20T08:00:00Z">'
            '<script type="application/ld+json">{"@type":"NewsArticle","isAccessibleForFree":false}</script>'
            f'</head><body><nav>Do not import navigation</nav><article><h1>The patient investor</h1>{body}'
            '<script>window.untrustedArticle=true</script><iframe src="https://tracking.example"></iframe></article></body></html>').encode()


def selection(records):
    return {"selected": records}


class PublicationInputTests(unittest.TestCase):
    def test_tracking_aliases_keep_article_identity_and_meaningful_query_parameters(self):
        self.assertEqual(canonical_url(STORY + '?utm_source=x#top'), STORY)
        self.assertEqual(candidate_url(STORY + '?utm_source=x'), candidate_url(STORY))
        self.assertNotEqual(candidate_url(STORY + '?id=1'), candidate_url(STORY + '?id=2'))

    def test_values_validate_limits_preferences_and_public_addresses(self):
        values = publication_values({'url': SITE, 'preferences': 'Policy; exclude stock tips', 'max_articles': 3})
        self.assertEqual(values['detail']['max_articles'], 3)
        self.assertTrue(values['detail']['freedium_fallback'])
        for changes in ({'url': 'https://127.0.0.1/'}, {'url': 'file:///tmp/private'},
                        {'url': 'https://name:password@journal.example/business'}, {'max_articles': True},
                        {'max_articles': 11}, {'preferences': ['policy']}, {'freedium_fallback': 'yes'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                publication_values({'url': SITE, **changes})

    def test_non_medium_freedium_urls_are_accepted_and_unwrapped(self):
        address = freedium_address(STORY + '?utm_source=email')
        self.assertEqual(address['url'], STORY)
        self.assertEqual(address['external_id'], candidate_url(STORY))
        self.assertEqual(freedium_address(FREEDIUM_ORIGIN + '/' + STORY), address)
        for value in ('https://journal.example/', 'https://127.0.0.1/article', 'https://medium.com/@alice'):
            with self.subTest(value=value), self.assertRaises(ValueError): freedium_address(value)


class PublicationReadingTests(unittest.TestCase):
    def test_feed_on_a_sibling_subdomain_can_link_to_its_publication(self):
        feed = (f'<rss><channel><title>Journal</title><item><title>A patient investing article</title>'
                f'<link>{STORY}</link></item></channel></rss>').encode()
        entries = page_candidates(feed, 'https://feeds.journal.example/rss')
        self.assertEqual([item.url for item in entries], [STORY])

    def test_index_discovery_drops_navigation_other_sites_and_tracking_aliases(self):
        candidates = page_candidates(index_page(), SITE)
        self.assertEqual([item.url for item in candidates], [STORY, OTHER])
        self.assertTrue(candidates[0].detail['date_known'])
        self.assertFalse(candidates[1].detail['date_known'])

    def test_structured_article_list_has_dates_and_descriptions(self):
        page = ('<script type="application/ld+json">' + json.dumps({'@type': 'ItemList', 'itemListElement': [
            {'item': {'headline': 'A useful investing article', 'url': STORY, 'datePublished': '2026-11-20',
                      'description': 'Long-term investing'}}]}) + '</script>').encode()
        candidates = page_candidates(page, SITE)
        self.assertEqual(candidates[0].url, STORY)
        self.assertEqual(candidates[0].detail['description'], 'Long-term investing')

    def test_subscriber_dom_extracts_the_loaded_article_and_removes_active_content(self):
        article = extract_article(story_page(), STORY, authenticated=True)
        self.assertIn('final paragraph', article['body_html'])
        self.assertEqual(article['content_source'], 'subscriber')
        self.assertNotIn('<script', article['body_html'])
        self.assertNotIn('<iframe', article['body_html'])
        self.assertNotIn('Do not import navigation', article['body_html'])

    def test_login_challenge_and_paid_preview_are_never_saved_as_full_articles(self):
        for body in (story_page(paid=True), b'<html><input type="password"><p>Sign in</p></html>',
                     b'<html><article><p>Verify you are human</p></article></html>'):
            with self.subTest(body=body[:70]), self.assertRaises(ValueError): extract_article(body, STORY)

    def test_freedium_reads_non_medium_article_without_sending_credentials(self):
        with patch('requests.sessions.Session.request', return_value=response(
                FREEDIUM_ORIGIN + '/' + STORY, mirror_page(story=STORY))) as get:
            article = fetch_freedium(STORY)
        self.assertEqual(get.call_args.args[1], FREEDIUM_ORIGIN + '/' + STORY)
        self.assertEqual(article['url'], STORY)
        self.assertEqual(article['content_source'], 'freedium')
        self.assertNotIn('cookies', get.call_args.kwargs)


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.candidates = page_candidates(index_page(), SITE)

    def test_written_preferences_select_ordered_ids_with_reasons(self):
        chat = Mock(return_value=selection([{'id': '0', 'reason': 'Matches patient investing; avoids daily tips.'}]))
        picked = select_articles(self.candidates, 'Policy and investing. Skip stock tips.', 3, chat)
        self.assertEqual([item.url for item in picked], [STORY])
        self.assertIn('patient investing', picked[0].detail['selection_reason'])
        payload = chat.call_args.args[0]
        self.assertIn('Skip stock tips.', payload['preferences'])
        self.assertEqual(len(payload['articles']), 2)

    def test_blank_preferences_fetch_without_any_model_request(self):
        chat = Mock(side_effect=AssertionError('Must not call a model'))
        self.assertEqual(select_articles(self.candidates, '', 1, chat), self.candidates[:1])
        self.assertEqual(select_articles([], 'Investing', 5, chat), [])

    def test_fabricated_article_id_fails_instead_of_becoming_a_url(self):
        for records in ([{'id': '999', 'reason': 'Invented'}], [{'id': 'https://private.example/', 'reason': ''}],
                        [{'id': '-1', 'reason': ''}], [{'id': '0', 'reason': []}]):
            with self.subTest(records=records), self.assertRaisesRegex(ValueError, 'invalid article selection'):
                select_articles(self.candidates, 'Investing', 3, Mock(return_value=selection(records)))

    def test_no_matches_is_successful_empty_selection_and_duplicates_are_bounded(self):
        self.assertEqual(select_articles(self.candidates, 'Only gardening', 3, Mock(return_value=selection([]))), [])
        picks = select_articles(self.candidates, 'Investing', 1, Mock(return_value=selection([
            {'id': '0', 'reason': 'Matches'}, {'id': '0', 'reason': 'Duplicate'}, {'id': '1', 'reason': 'Excess'}])))
        self.assertEqual(len(picks), 1)


class PublicationEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.secrets = Secrets(self.store.root, use_keyring=False)
        self.engine = Engine(self.store, self.secrets)
        self.store.save_settings({'summarize_articles': False})
        self.source_id = self.store.add_source('publications', 'The journal', SITE, {
            'preferences': 'Long-term investing; skip stock tips', 'max_articles': 1, 'freedium_fallback': True})
        self.connection = self.engine.catalogue.get('publications')

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def test_preferences_need_a_configured_key_before_any_publisher_request(self):
        with patch.object(self.connection, 'discover', side_effect=AssertionError('No network')):
            with self.assertRaisesRegex(ValueError, 'AI API key'):
                self.engine.collect_articles(None, self.store.sources()[0], datetime.now(timezone.utc), 5, self.store.create_job('prepare'))

    def test_end_to_end_selection_session_extraction_reason_and_duplicate_skip(self):
        self.secrets.set('openai_key', 'synthetic-test-key')
        candidates = [replace(c, detail={**c.detail, 'publication_id': self.source_id, 'freedium_fallback': True})
                      for c in page_candidates(index_page(), SITE)]
        session_browser = Mock()
        session_browser.request.return_value = {'html': story_page().decode(), 'url': STORY}
        self.connection.browser = session_browser
        with patch.object(self.connection, 'discover', return_value=candidates), \
             patch('requests.sessions.Session.request', return_value=response(STORY, story_page(paid=True))), \
             patch('desktop.engine.PiSelection.select', side_effect=[selection([{'id': '0', 'reason': 'Long-term investing, without stock tips.'}]), selection([])]) as chat:
            result = self.engine.job_prepare(self.store.create_job('prepare'), {'source_id': self.source_id})
            self.assertEqual(result['added'], 1)
            self.assertTrue(self.engine.export_epub(result['edition_id']).is_file())
            item = self.store.items()[0]
            self.assertEqual(item['content_source'], 'subscriber')
            self.assertIn('final paragraph', item['body_html'])
            self.assertIn('without stock tips', item['selection_reason'])
            self.assertEqual(item['connection_id'], 'publications')
            again = self.engine.job_prepare(self.store.create_job('prepare'), {'source_id': self.source_id})
            self.assertEqual(again['added'], 0)
            # Only the unseen stock-tip candidate remains in the second selection.
            self.assertEqual(chat.call_count, 2)
        self.assertEqual(self.store.query('SELECT COUNT(*) n FROM deliveries', one=True)['n'], 0)

    def test_failed_session_tries_freedium_only_when_enabled(self):
        item = replace(page_candidates(index_page(), SITE)[0], detail={'publication_id': self.source_id, 'freedium_fallback': True})
        self.connection.browser = Mock()
        self.connection.browser.request.side_effect = ValueError('Sign-in expired')
        with patch('requests.sessions.Session.request', return_value=response(STORY, story_page(paid=True))), \
             patch('desktop.publications.fetch_freedium', return_value={'body_html': '<p>'+TEXT+'</p>', 'content_source': 'freedium'}) as mirror:
            self.assertEqual(self.connection.read_article(item)['content_source'], 'freedium')
            mirror.assert_called_once_with(STORY)
            mirror.reset_mock()
            item = replace(item, detail={**item.detail, 'freedium_fallback': False})
            with self.assertRaisesRegex(ValueError, 'complete article'): self.connection.read_article(item)
            mirror.assert_not_called()

    def test_signin_saves_only_session_status_and_uses_own_publication_id(self):
        browser = Mock()
        self.connection.browser = browser
        result = self.engine.job_login_publication(self.store.create_job('login_publication'), {'source_id': self.source_id})
        browser.request.assert_called_once_with(self.source_id, SITE, login=True)
        self.assertIn('session_saved_at', self.store.sources()[0]['detail'])
        self.assertIn('Fetch articles', result['message'])
        with self.assertRaisesRegex(ValueError, 'saved publication'):
            self.engine.job_login_publication(self.store.create_job('login_publication'), {'source_id': 'other'})

    def test_browser_request_files_are_cleaned_up_on_failure(self):
        browser = PublicationBrowser(self.store.root)
        with patch('subprocess.run') as run, self.assertRaisesRegex(ValueError, 'browser could not finish'):
            browser.request(self.source_id, STORY)
        self.assertEqual(list((self.store.root/'publication-browser/jobs').iterdir()), [])
        self.assertIn('--publication-browser-job', run.call_args.args[0])
        for source_id in ('../other', 'not-a-source'):
            with self.assertRaises(ValueError): browser.request(source_id, STORY)


class PiBridgeTests(unittest.TestCase):
    def test_key_is_sent_over_stdin_and_never_as_a_process_argument(self):
        with tempfile.TemporaryDirectory() as temporary:
            bridge = PiSelection()
            bridge.folder = Path(temporary)
            (bridge.folder/'runtime').mkdir()
            (bridge.folder/'runtime/node').touch()
            (bridge.folder/'dist').mkdir()
            (bridge.folder/'dist/reader-agent.mjs').touch()
            process = Mock(returncode=0, stdout=json.dumps({'selected': [], 'runtime': 'pi-agent-core'}))
            with patch('subprocess.run', return_value=process) as run:
                bridge.select({'articles': [], 'preferences': 'Gardening', 'maximum': 1},
                              provider='openai', model='test-model', key='synthetic-private-key')
            self.assertNotIn('synthetic-private-key', ' '.join(run.call_args.args[0]))
            self.assertEqual(json.loads(run.call_args.kwargs['input'])['api_key'], 'synthetic-private-key')

    def test_missing_pi_build_is_reported_instead_of_silently_using_another_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            bridge = PiSelection()
            bridge.folder = Path(temporary)
            with self.assertRaisesRegex(ValueError, 'missing from this build'):
                bridge.select({}, provider='openai', model='test-model', key='test-key')
