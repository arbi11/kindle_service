"""Offline Medium/Freedium contracts. All stories and responses are synthetic."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

import requests

from desktop.connections import Catalogue
from desktop.engine import Engine
from desktop.medium import (FREEDIUM_ORIGIN, article_address, creator_feed, creator_list,
                            fetch_freedium, medium_entries, resolve_creator)
from desktop.secrets import Secrets
from desktop.store import Store
from test_connections import response

POST_ID = "0123456789ab"
STORY = "https://medium.com/@alice/a-complete-story-" + POST_ID
FEED = "https://medium.com/feed/@alice"
MIRROR = FREEDIUM_ORIGIN + "/https://medium.com/p/" + POST_ID
TEXT = "A precise description with the original figures, limitations and references. " * 12


def creator_rss(*, preview=False, post_id=POST_ID, title="A complete story", published=None, host="medium.com/@alice", body=None, author="Alice"):
    date = published or datetime.now(timezone.utc)
    content = body if body is not None else ('<p class="medium-feed-snippet">An excerpt. Become a member to read the full story.</p>'
                                             if preview else f'<p>{TEXT}</p><script>window.articleRan=true</script><img src="https://tracking.example/pixel">')
    return (f'<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<channel><title>Stories by {author} on Medium</title><link>https://medium.com/@alice</link>'
            f'<item><title>{title}</title><link>https://{host}/a-complete-story-{post_id}?source=rss</link>'
            f'<dc:creator>{author}</dc:creator><pubDate>{date.strftime("%a, %d %b %Y %H:%M:%S GMT")}</pubDate>'
            f'<content:encoded><![CDATA[{content}]]></content:encoded></item></channel></rss>').encode()


def medium_page(*, paid=False):
    return ('<html><head><script type="application/ld+json">{"@type":"Article","isAccessibleForFree":' +
            ('false' if paid else 'true') + '}</script></head><body><article><h1>A complete story</h1>' +
            ("<p>Become a member to read this story.</p>" if paid else f'<p>{TEXT}</p>') + '</article></body></html>').encode()


def mirror_page(*, title="A complete story", author="Alice", story=STORY):
    return (f'<html><body><nav>Site navigation must not be imported.</nav><article>'
            f'<header>November 1, 2026<h1>{title}</h1><p>By {author} 6 min read</p><a href="{story}">Original</a></header>'
            f'<section>Contents and download buttons must not be imported.</section><div class="prose max-w-none">'
            f'<p>{TEXT}</p><p>The final paragraph is present after the paywall.</p><a href="/reference">Reference</a>'
            '<script>window.articleRan=true</script><iframe src="https://tracking.example"></iframe><img src="https://tracking.example/pixel">'
            '</div></article></body></html>').encode()


def network(method, url, **kwargs):
    if url == FEED:
        return response(url, creator_rss(preview=True))
    if url == STORY:
        return response(url, medium_page(paid=True))
    if url == MIRROR:
        return response(url, mirror_page())
    if url == "https://medium.com/feed/@bob":
        return response(url, creator_rss(post_id="aabbccddeeff", title="A public story by Bob", host="medium.com/@bob", author="Bob"))
    if url == "https://medium.com/@bob/a-complete-story-aabbccddeeff":
        return response(url, medium_page())
    if url == FREEDIUM_ORIGIN + "/https://medium.com/p/abcdefabcdef":
        return response(url, mirror_page(title="An article saved from a link", author="Charlie", story="https://medium.com/@charlie/a-saved-story-abcdefabcdef"))
    raise AssertionError("Unexpected request: " + url)


class MediumAddressTests(unittest.TestCase):
    def test_creator_aliases_and_lists_have_one_identity(self):
        for value in ("@Alice", "Alice", "medium.com/@Alice", "https://www.medium.com/@alice/",
                      "https://alice.medium.com/", "https://alice.medium.com/feed", FEED):
            with self.subTest(value=value): self.assertEqual(creator_feed(value), FEED)
        self.assertEqual(creator_list("@Alice\n\nhttps://alice.medium.com/\n@bob"), [FEED, "https://medium.com/feed/@bob"])
        for value in (STORY, "https://medium.com/a-publication", "https://example.com", "http://medium.com/@alice",
                      "https://user:secret@medium.com/@alice", "https://127.0.0.1/@alice", "https://medium.com:8080/@alice"):
            with self.subTest(value=value), self.assertRaises(ValueError): creator_feed(value)
        for value in ("", ["@alice"], "\n".join('@creator'+str(i) for i in range(21))):
            with self.subTest(value=value), self.assertRaises(ValueError): creator_list(value)

    def test_article_identity_survives_domains_tracking_and_freedium_wrappers(self):
        for value in (STORY + "?source=rss#top", "https://alice.medium.com/another-title-" + POST_ID,
                      "https://publication.example/same-story-" + POST_ID,
                      FREEDIUM_ORIGIN + "/" + STORY, FREEDIUM_ORIGIN + "/" + quote(STORY, safe="")):
            with self.subTest(value=value):
                record = article_address(value)
                self.assertEqual(record['external_id'], 'medium:' + POST_ID)
                self.assertEqual(record['medium_url'], 'https://medium.com/p/' + POST_ID)
                self.assertNotIn('?source', record['url'])
        for value in ("https://medium.com/@alice", "https://127.0.0.1/story-" + POST_ID,
                      "file:///etc/passwd", "https://user:secret@medium.com/p/" + POST_ID):
            with self.subTest(value=value), self.assertRaises(ValueError): article_address(value)


class MediumReadingTests(unittest.TestCase):
    def test_creator_feed_keeps_publication_domains_and_author_and_stable_ids(self):
        title, entries = medium_entries(creator_rss(host='publication.example'), FEED)
        self.assertEqual(title, 'Alice')
        self.assertEqual(entries[0].external_id, 'medium:' + POST_ID)
        self.assertEqual(entries[0].author, 'Alice')
        with patch('requests.sessions.Session.request', side_effect=network):
            self.assertEqual(resolve_creator('@alice')['url'], FEED)

    def test_free_medium_content_stays_local_sanitized_and_does_not_use_freedium(self):
        _, entries = medium_entries(creator_rss(), FEED)
        connection = Catalogue().get('medium')
        with patch('requests.sessions.Session.request', return_value=response(STORY, medium_page())) as get:
            article = connection.read_article(entries[0])
        self.assertEqual(get.call_count, 1)
        self.assertEqual(article['content_source'], 'medium')
        self.assertIn(TEXT, article['body_html'])
        self.assertNotIn('<script', article['body_html'])
        self.assertNotIn('<img', article['body_html'])

    def test_page_only_public_content_can_be_extracted(self):
        _, entries = medium_entries(creator_rss(body=''), FEED)
        with patch('requests.sessions.Session.request', return_value=response(STORY, medium_page())):
            article = Catalogue().get('medium').read_article(entries[0])
        self.assertEqual(article['content_source'], 'medium')
        self.assertIn(TEXT.strip(), article['body_html'])

    def test_both_explicit_rss_excerpts_and_long_paid_previews_use_freedium(self):
        for preview in (True, False):
            _, entries = medium_entries(creator_rss(preview=preview), FEED)
            with patch('requests.sessions.Session.request', side_effect=network) as get:
                article = Catalogue().get('medium').read_article(entries[0])
            self.assertEqual(get.call_count, 2)
            self.assertEqual(article['content_source'], 'freedium')
            self.assertEqual(article['url'], STORY)
            self.assertEqual(article['author'], 'Alice')
            self.assertEqual(article['published'], '2026-11-01T00:00:00+00:00')
            self.assertIn('final paragraph', article['body_html'])
            self.assertNotIn('Site navigation', article['body_html'])
            self.assertNotIn('download buttons', article['body_html'])
            self.assertNotIn('<script', article['body_html'])
            self.assertNotIn('<iframe', article['body_html'])

    def test_unavailable_or_redirected_mirror_does_not_import_a_landing_page(self):
        for payload in (b'<html><h1>Freedium</h1>Paste a link</html>', b'<article><h1>Unavailable</h1><p>Try later</p></article>'):
            with patch('requests.sessions.Session.request', return_value=response(MIRROR, payload)), self.assertRaisesRegex(ValueError, 'not been saved'):
                fetch_freedium(STORY)
        with patch('requests.sessions.Session.request', return_value=response(MIRROR, status=302, headers={'Location':'https://unexpected.example/story'})) as get:
            with self.assertRaisesRegex(ValueError, 'not been saved'): fetch_freedium(STORY)
            self.assertEqual(get.call_count, 1)

    def test_sign_in_html_is_not_saved_in_place_of_a_story(self):
        _, entries = medium_entries(creator_rss(body=''), FEED)
        def login_page(method, url, **kwargs):
            if url == STORY: return response(url, ('<html><main><h1>Sign in</h1><p>'+TEXT+'</p></main></html>').encode())
            return network(method, url, **kwargs)
        with patch('requests.sessions.Session.request', side_effect=login_page):
            article = Catalogue().get('medium').read_article(entries[0])
        self.assertEqual(article['content_source'], 'freedium')
        self.assertIn('final paragraph', article['body_html'])


class MediumIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.engine = Engine(self.store, Secrets(self.store.root, use_keyring=False))
        self.no_network = patch('requests.sessions.Session.request', side_effect=network)
        self.no_network.start()
        self.no_ai = patch('ai_client.chat', side_effect=AssertionError('No AI key configured'))
        self.no_ai.start()

    def tearDown(self):
        self.engine.executor.shutdown(wait=True)
        self.no_ai.stop(); self.no_network.stop(); self.temp.cleanup()

    def test_follow_prepare_repeat_new_post_pause_and_disconnect(self):
        result = self.engine.job_add_subscription(self.store.create_job('add_subscription'), {'kind':'medium','url':'@alice\nhttps://alice.medium.com/'})
        source_id = result['source_ids'][0]
        self.assertEqual(len(self.store.sources()), 1)
        payload = {'source_id':source_id}
        with patch.object(self.engine, 'job_send', side_effect=AssertionError('No mail')):
            first = self.engine.job_prepare(self.store.create_job('prepare'), payload)
            again = self.engine.job_prepare(self.store.create_job('prepare'), payload)
        self.assertEqual(first['added'], 1); self.assertEqual(again['added'], 0)
        item = self.store.items()[0]
        self.assertEqual(item['connection_id'], 'medium')
        self.assertEqual(item['content_source'], 'freedium')
        self.assertIn('final paragraph', item['body_html'])
        self.assertEqual(self.store.query('SELECT count(*) n FROM deliveries', one=True)['n'], 0)
        old_date = datetime.now(timezone.utc) - timedelta(days=30)
        new_id = 'abcdef012345'
        def second_feed(method, url, **kwargs):
            if url == FEED: return response(url, creator_rss(post_id=new_id, published=old_date))
            if url.endswith(new_id): return response(url, medium_page())
            return network(method, url, **kwargs)
        with patch('requests.sessions.Session.request', side_effect=second_feed):
            self.assertEqual(self.engine.job_prepare(self.store.create_job('prepare'), payload)['added'], 1)
        self.store.execute('UPDATE sources SET enabled=0 WHERE id=?', (source_id,))
        with self.assertRaisesRegex(ValueError, 'enabled connection'):
            self.engine.job_prepare(self.store.create_job('prepare'), payload)
        self.store.execute('DELETE FROM sources WHERE id=?', (source_id,))
        self.assertEqual(len(self.store.items()), 2)
        self.assertEqual(Store(self.store.root).items()[0]['content_source'], 'freedium')

    def test_manual_article_saves_immediately_without_source_then_exports_and_deduplicates(self):
        result = self.engine.job_import_freedium(self.store.create_job('import_freedium'), {'url':STORY})
        item = self.store.query('SELECT * FROM items WHERE id=?', (result['item_id'],), one=True)
        self.assertEqual(item['connection_id'], 'freedium')
        self.assertEqual(self.store.sources(), [])
        again = self.engine.job_import_freedium(self.store.create_job('import_freedium'), {'url':FREEDIUM_ORIGIN+'/'+STORY+'?source=share'})
        self.assertEqual(result['item_id'], again['item_id'])
        edition = self.engine.job_prepare(self.store.create_job('prepare'), {})
        self.assertTrue(edition['edition_id'])
        self.assertTrue(self.engine.export_epub(edition['edition_id']).is_file())
        self.engine.job_add_subscription(self.store.create_job('add_subscription'), {'kind':'medium','url':'@alice'})
        self.assertEqual(self.engine.job_prepare(self.store.create_job('prepare'), {})['added'], 0)
        self.assertEqual(len(self.store.items()), 1)

    def test_failed_excerpt_is_not_saved_and_is_retried_on_later_refresh(self):
        source_id = self.engine.job_add_subscription(self.store.create_job('add_subscription'), {'kind':'medium','url':'@alice'})['source_ids'][0]
        def unavailable(method, url, **kwargs):
            if url == MIRROR: return response(url, b'<h1>Unavailable</h1>', status=503)
            return network(method, url, **kwargs)
        with patch('requests.sessions.Session.request', side_effect=unavailable), self.assertRaisesRegex(ValueError, 'No edition could be prepared'):
            self.engine.job_prepare(self.store.create_job('prepare'), {'source_id':source_id})
        self.assertEqual(self.store.items(), [])
        self.assertIn('Freedium', self.store.sources()[0]['error'])
        self.assertEqual(self.engine.job_prepare(self.store.create_job('prepare'), {'source_id':source_id})['added'], 1)

    def test_legacy_freedium_links_do_not_duplicate_reading_imported_from_the_old_service(self):
        old_id = self.store.add_item(kind='article', external_id='older-raindrop-bookmark-id', connection_id='raindrop',
                                     title='An earlier saved copy', url=FREEDIUM_ORIGIN+'/https://publication.example/old-title-'+POST_ID,
                                     body_html='<p>'+TEXT+'</p>')
        before = self.store.query('SELECT * FROM items WHERE id=?', (old_id,), one=True)
        with patch('desktop.engine.fetch_freedium', side_effect=AssertionError('Already saved')):
            result = self.engine.job_import_freedium(self.store.create_job('import_freedium'), {'url':STORY})
            self.engine.job_add_subscription(self.store.create_job('add_subscription'), {'kind':'medium','url':'@alice'})
            prepared = self.engine.job_prepare(self.store.create_job('prepare'), {})
        self.assertEqual(result['item_id'], old_id)
        self.assertEqual(prepared['added'], 0)
        self.assertEqual(len(self.store.items()), 1)
        self.assertEqual(before, self.store.query('SELECT * FROM items WHERE id=?', (old_id,), one=True))

    def test_batch_partial_failure_keeps_valid_creators_and_reports_failure(self):
        result = self.engine.job_add_subscription(self.store.create_job('add_subscription'), {'kind':'medium','url':'@alice\n@missing'})
        self.assertEqual(len(result['source_ids']), 1)
        self.assertEqual(len(result['warnings']), 1)
        self.assertIn('missing', result['message'])


if __name__ == '__main__':
    unittest.main()
