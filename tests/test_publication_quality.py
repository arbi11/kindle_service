"""Host enforcement, private receipts and failover for the Decisions-backed Pi tools."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from connection_sdk import Candidate
from desktop.agent import PiTextReview, checked_text_review
from desktop.connections import Catalogue
from desktop.content import plain_text
from desktop.engine import Engine
from desktop.secrets import Secrets
from desktop.store import Store
from test_connections import response
from test_publications import SITE, STORY, TEXT
from test_publication_search import COPY, copy_page


def receipt(*, category='article_text', category_probability=.98, match='same_article', match_probability=.98):
    classified = category == 'article_text' and category_probability >= .8
    verdict = classified and match == 'same_article' and match_probability >= .9
    return {'candidate_id': 'copy', 'classification': {'choice': category, 'probability': category_probability, 'confidence': .96, 'input_tokens': 100},
            'match': {'choice': match, 'probability': match_probability, 'confidence': .96, 'input_tokens': 100} if classified else None,
            'status': 'accepted' if verdict else 'rejected', 'runtime': 'pi-agent-core', 'phase': 'text_review',
            'tools_used': ['classify_article_text', 'verify_article_match'] if classified else ['classify_article_text'],
            'model_turns': 2 if classified else 1, 'decisions_model': 'gpt-6-luna',
            'usage': {'requests': 2 if classified else 1, 'input_tokens': 200 if classified else 100, 'output_tokens': 0},
            'policy': {'version': 1, 'tools': ['classify_article_text', 'verify_article_match'], 'max_tool_calls': 2,
                       'article_probability': .8, 'match_probability': .9}}


class TextReceiptTests(unittest.TestCase):
    def test_host_recomputes_verdict_from_both_tool_answers(self):
        for values, accepted in [({}, True), ({'category': 'podcast_description'}, False),
                                 ({'category_probability': .79}, False), ({'match': 'related_reporting'}, False),
                                 ({'match_probability': .89}, False)]:
            data = receipt(**values)
            self.assertEqual(checked_text_review(data)['status'], 'accepted' if accepted else 'rejected')
            data['status'] = 'rejected' if accepted else 'accepted'
            with self.assertRaisesRegex(ValueError, 'verdict'): checked_text_review(data)

    def test_host_rejects_tool_protocol_or_probability_tampering(self):
        for changes in ({'tools_used': ['browse', 'verify_article_match']}, {'tools_used': ['classify_article_text']},
                        {'model_turns': 4}, {'model_turns': True}, {'runtime': 'another-agent'},
                        {'decisions_model': 'other-model'}, {'usage': {'requests': 99}},
                        {'classification': {'choice': 'article_text', 'probability': True, 'confidence': .9, 'input_tokens': 1}},
                        {'classification': {'choice': 'article_text', 'probability': float('nan'), 'confidence': .9, 'input_tokens': 1}},
                        {'policy': {'version': 99}}, {'match': None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): checked_text_review({**receipt(), **changes})
        bad = receipt(category='excerpt'); bad['match'] = receipt()['match']
        with self.assertRaisesRegex(ValueError, 'before classification'): checked_text_review(bad)

    def test_only_bounded_evidence_and_stdin_credentials_enter_pi(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge = PiTextReview(); bridge.folder = Path(folder)
            (bridge.folder/'runtime').mkdir(); (bridge.folder/'runtime/node').touch()
            (bridge.folder/'dist').mkdir(); (bridge.folder/'dist/reader-agent.mjs').touch()
            candidate = Candidate('id', 'The patient investor', STORY, '2026-11-20T00:00:00+00:00',
                                  author='A. Writer', detail={'description': 'Long-term earnings.', 'cookies': 'PRIVATE-COOKIE'})
            article = {'title': candidate.title, 'body_html': '<p>' + TEXT * 100 + '</p>', 'content_url': COPY,
                       'password': 'PRIVATE-PASSWORD'}
            def run(command, **kwargs):
                request = json.loads(kwargs['input']); data = receipt(); data['candidate_id'] = request['review']['id']
                return Mock(returncode=0, stdout=json.dumps(data))
            with patch('subprocess.run', side_effect=run) as process:
                bridge.review(candidate, article, model='test-model', key='PRIVATE-KEY', timeout=4)
            kwargs = process.call_args.kwargs; body = json.loads(kwargs['input'])
            self.assertEqual(kwargs['timeout'], 4)
            self.assertNotIn('PRIVATE-KEY', ' '.join(process.call_args.args[0]))
            self.assertNotIn('PRIVATE-COOKIE', kwargs['input']); self.assertNotIn('PRIVATE-PASSWORD', kwargs['input'])
            self.assertEqual(body['phase'], 'text_review'); self.assertEqual(body['api_key'], 'PRIVATE-KEY')
            self.assertEqual(len(body['review']['copy']['text']), 24000)
            self.assertTrue(body['review']['copy']['text_truncated'])
            with patch('subprocess.run', return_value=Mock(returncode=0, stdout=json.dumps(receipt()))), \
                 self.assertRaisesRegex(ValueError, 'another copy'):
                bridge.review(candidate, article, model='test-model', key='PRIVATE-KEY')


class TextReviewFailoverTests(unittest.TestCase):
    def setUp(self):
        self.connection = Catalogue().get('publications')
        self.candidate = Candidate('id', 'The patient investor and economic policy', STORY, '2026-11-20T00:00:00+00:00', detail={})
        self.article = {'title': self.candidate.title, 'body_html': '<p>' + TEXT + '</p>', 'url': STORY,
                        'content_source': 'publisher', 'content_url': STORY}

    def test_rejected_text_continues_to_the_next_provider(self):
        self.connection.text_review = Mock(side_effect=[ValueError('Excerpt'), None])
        with patch.object(self.connection, '_read_feed', return_value=self.article), \
             patch.object(self.connection, '_read_publisher', return_value={**self.article, 'content_source': 'next-provider'}), \
             patch.object(self.connection, '_read_browser') as browser:
            result = self.connection.read_article(self.candidate)
        self.assertEqual(result['content_source'], 'next-provider')
        self.assertEqual(self.connection.text_review.call_count, 2); browser.assert_not_called()

    def test_rejected_search_copy_does_not_hide_a_later_verified_copy(self):
        second = COPY + '-second'
        self.connection.text_review = Mock(side_effect=[ValueError('Related coverage'), None])
        with patch('desktop.publications.PublicSearch.search', return_value=[{'title': self.candidate.title, 'url': COPY},
                                                                            {'title': self.candidate.title, 'url': second}]), \
             patch('desktop.publications.PublicFeedHTTP.get', side_effect=lambda url, **kwargs: response(url, copy_page())):
            result = self.connection._read_public_copy(self.candidate)
        self.assertEqual(result['content_url'], second)
        self.assertEqual(self.connection.text_review.call_count, 2)

    def test_four_copy_review_limit_cannot_be_bypassed_by_later_searches(self):
        self.connection.text_review = Mock(side_effect=ValueError('Rejected'))
        for _ in range(6):
            with self.assertRaises(ValueError): self.connection._review_text(self.candidate, self.article)
        self.assertEqual(self.connection.text_review.call_count, 4)


class PublicationQualityEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name)); self.secrets = Secrets(self.store.root, use_keyring=False)
        self.secrets.set('openai_key', 'PRIVATE-KEY')
        self.store.save_settings({'provider': 'openai', 'model': 'test-model', 'summarize_articles': False})
        self.engine = Engine(self.store, self.secrets); self.addCleanup(self.engine.close)
        self.source_id = self.store.add_source('publications', 'Journal', SITE, {'preferences': '', 'max_articles': 1})
        self.connection = self.engine.catalogue.get('publications')
        self.candidate = Candidate('id', 'The patient investor', STORY, '2026-11-20T00:00:00+00:00',
                                   detail={'publication_id': self.source_id, 'date_known': True})
        self.article = {'title': self.candidate.title, 'url': STORY, 'body_html': '<p>' + TEXT + '</p>',
                        'content_source': 'publisher', 'content_url': STORY}

    def prepare(self, result=None, error=None):
        with patch.object(self.connection, 'discover', return_value=[self.candidate]), \
             patch.object(self.connection, 'retrieval_steps', return_value=[('Feed', lambda c: self.article)]), \
             patch('desktop.engine.PiTextReview.review', return_value=result, side_effect=error):
            return self.engine.job_prepare(self.store.create_job('prepare'), {'source_id': self.source_id})

    def test_passed_checks_are_recorded_and_reach_library_and_epub_without_delivery(self):
        result = self.prepare(receipt())
        self.assertEqual(result['added'], 1); self.assertEqual(result['text_checks'][0]['review']['tools_used'], ['classify_article_text', 'verify_article_match'])
        self.assertTrue((self.store.root/'editions'/self.store.editions()[0]['file']).is_file())
        self.assertEqual(self.store.query('SELECT COUNT(*) n FROM deliveries', one=True)['n'], 0)
        files = list((self.store.root/'publication-checks').glob('*.json')); self.assertEqual(len(files), 1)
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        evidence = files[0].read_text(); self.assertNotIn('PRIVATE-KEY', evidence); self.assertNotIn(TEXT, evidence)
        self.assertEqual(json.loads(evidence)['text_sha256'], hashlib.sha256(plain_text(self.article['body_html']).encode()).hexdigest())

    def test_rejected_or_unavailable_checks_never_import_or_summarize(self):
        for outcome, error in [(receipt(category='podcast_description'), None), (None, ValueError('Decisions unavailable PRIVATE-KEY'))]:
            with self.subTest(outcome=outcome), patch.object(self.engine, 'summarize_article') as summarize, \
                 self.assertRaisesRegex(ValueError, 'no readable articles'):
                self.prepare(outcome, error)
            summarize.assert_not_called(); self.assertEqual(self.store.items(), []); self.assertEqual(self.store.editions(), [])
        for file in (self.store.root/'publication-checks').glob('*.json'): self.assertNotIn('PRIVATE-KEY', file.read_text())

    def test_other_provider_and_reading_without_an_openai_key_keep_existing_checks(self):
        self.store.save_settings({'provider': 'openrouter'})
        with patch('desktop.engine.PiTextReview.review') as review:
            self.engine.review_publication_text('job', self.candidate, self.article)
        review.assert_not_called()
