"""Offline contract and EPUB tests. No network, email, or installed state writes."""

import copy
import json
import os
import tempfile
import tomllib
import unittest
import wave
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import ai_client as ai
import raindrop_kindle as rk
import youtube_kindle as yt
from preview_openai import read_srt


def completion(content, **extra):
    data = {"model": "gpt-6-luna", "choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(content), "refusal": None}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}}
    data.update(extra)
    return data


class Response:
    def __init__(self, data=None, status=200, text=""):
        self.data, self.status_code, self.text = data, status, text
        self.ok = status < 400

    def json(self):
        return self.data


class Session:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"OPENAI_API_KEY": ""})
        self.env.start()
        self.cfg = {"ai": {"provider": "openai", "openai_api_key": "sk-test-openai",
                           "openrouter_api_key": "sk-or-test-old", "candidate_writer": "z-ai/glm-5.3-flash"}}
        self.no_network = patch("requests.sessions.Session.request", side_effect=AssertionError("Real network forbidden"))
        self.no_network.start()
        self.no_mail = patch.object(rk, "send_to_kindle", side_effect=AssertionError("Email forbidden"))
        self.no_mail.start()

    def tearDown(self):
        self.no_mail.stop()
        self.no_network.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_chat_adapts_authentication_and_reasoning_without_mutating_input(self):
        session = Session([Response(completion({"ok": True}))])
        body = {"model": "gpt-6-luna", "messages": [{"role": "user", "content": "Hello"}],
                "reasoning": {"effort": "minimal"}, "usage": {"include": True},
                "max_tokens": 1000, "temperature": .5, "top_p": .8}
        original = copy.deepcopy(body)
        ai.chat(session, self.cfg, body)
        url, request = session.calls[0]
        self.assertEqual(url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(request["headers"]["Authorization"], "Bearer sk-test-openai")
        payload = request["json"]
        self.assertEqual(payload["reasoning_effort"], "low")
        self.assertEqual(payload["max_completion_tokens"], 1000)
        self.assertFalse(payload["store"])
        for forbidden in ["reasoning", "usage", "temperature", "top_p", "max_tokens"]:
            self.assertNotIn(forbidden, payload)
        self.assertEqual(body, original)

    def test_legacy_provider_and_payload_are_preserved(self):
        cfg = {"ai": {"openrouter_api_key": "sk-or-test"}}
        body = {"model": "z-ai/glm-5.3-flash", "messages": [], "temperature": .4, "usage": {"include": True}}
        session = Session([Response(completion({}))])
        ai.chat(session, cfg, body)
        url, request = session.calls[0]
        self.assertEqual(url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(request["json"], body)
        self.assertEqual(request["headers"]["Authorization"], "Bearer sk-or-test")

    def test_openai_never_uses_openrouter_key_when_openai_key_is_missing(self):
        cfg = {"ai": {"provider": "openai", "openrouter_api_key": "sk-or-old",
                      "openai_api_key_file": str(self.root / "missing")}}
        session = Session()
        with self.assertRaisesRegex(RuntimeError, "No openai API key"):
            ai.chat(session, cfg, {"model": "gpt-6-luna"})
        self.assertEqual(session.calls, [])

    def test_private_key_file_is_used(self):
        path = self.root / "key"
        path.write_text("sk-test-private\n")
        cfg = {"ai": {"provider": "openai", "openai_api_key_file": str(path)}}
        self.assertEqual(ai.api_key(cfg), "sk-test-private")

    def test_selected_key_file_takes_precedence_over_unrelated_shell_key(self):
        path = self.root / "key"
        path.write_text("sk-test-credited-organization\n")
        cfg = {"ai": {"provider": "openai", "openai_api_key_file": str(path)}}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-other-organization"}):
            self.assertEqual(ai.api_key(cfg), "sk-test-credited-organization")


    def test_saved_transcript_timestamps_are_preserved(self):
        path = self.root / "old.srt"
        path.write_text('1\n00:00:01,250 --> 00:00:03,000\nA $3 million loss.\n\n2\n00:01:05,000 --> 00:01:07,000\nReduce risk.\n')
        self.assertEqual(read_srt(path), [[1.25, "A $3 million loss."], [65.0, "Reduce risk."]])

    def test_provider_specific_model_ids_are_rejected_before_a_request(self):
        session = Session()
        with self.assertRaisesRegex(ValueError, "OpenAI model ID"):
            ai.chat(session, self.cfg, {"model": "anthropic/claude-haiku-5.5"})
        self.assertEqual(session.calls, [])

    def test_partial_and_refused_responses_fail_explicitly(self):
        partial = completion({})
        partial["choices"][0]["finish_reason"] = "length"
        refused = completion({})
        refused["choices"][0]["message"]["refusal"] = "Declined"
        for data in [partial, refused]:
            with self.subTest(data=data), self.assertRaises(RuntimeError):
                ai.chat(Session([Response(data)]), self.cfg, {"model": "gpt-6-luna"})

    def test_error_never_repeats_secret_or_falls_back_to_openrouter(self):
        session = Session([Response(status=401, text="Invalid API key sk-test-openai")])
        with self.assertRaises(RuntimeError) as error:
            ai.chat(session, self.cfg, {"model": "gpt-6-luna"})
        self.assertNotIn("sk-test-openai", str(error.exception))
        self.assertEqual(len(session.calls), 1)

    def test_choice_and_score_decisions_preserve_the_downstream_contract(self):
        questions = {"a1": {"type": "choice", "criteria": {"BondYields": None, "Inflation": None}},
                     "v_one": {"type": "score", "criteria": ["short", "medium", "long", "full"]}}
        session = Session([Response(completion({"a1": {"choice": "BondYields"}, "v_one": {"score": 1.5}}))])
        data = ai.decisions(session, self.cfg, {"articles": []}, questions)
        self.assertEqual(data["answers"]["a1"]["choice"], "BondYields")
        self.assertEqual(data["answers"]["v_one"]["score"], 1.5)
        self.assertEqual(session.calls[0][0], "https://api.openai.com/v1/chat/completions")

    def test_missing_decisions_and_invalid_candidates_fail(self):
        questions = {"a1": {"type": "choice", "criteria": {"BondYields": None}}}
        for content in [{}, {"a1": {"choice": "Unexpected"}}]:
            with self.subTest(content=content), self.assertRaises(ValueError):
                ai.decisions(Session([Response(completion(content))]), self.cfg, {}, questions)

    def test_provider_failure_preserves_free_local_keyword_fallback(self):
        articles = [rk.Article(1, {}, "Bond yields and inflation", "https://example.com/bonds", "<p>Prices rose.</p>")]
        session = Session([Response(status=429, text="credit_balance_exhausted"),
                           Response(status=429, text="credit_balance_exhausted")])
        picker = rk.assign_keywords(articles, False, session, self.cfg)
        self.assertEqual(picker, "built-in")
        self.assertTrue(articles[0].keyword)
        self.assertTrue(all(url.startswith(ai.OPENAI_API) for url, _ in session.calls))

    def test_model_selection_supports_switching_back_without_losing_legacy_choices(self):
        cfg = {"shared": self.cfg, "summary_model": "z-ai/glm-5.3-flash",
               "editor_model": "anthropic/claude-haiku-5.5"}
        self.assertEqual(yt.text_model(cfg), "gpt-6-luna")
        self.assertEqual(yt.text_model(cfg, "editor"), "gpt-6-luna")
        self.cfg["ai"]["provider"] = "openrouter"
        self.assertEqual(yt.text_model(cfg), "z-ai/glm-5.3-flash")
        self.assertEqual(yt.text_model(cfg, "editor"), "anthropic/claude-haiku-5.5")

    def test_shared_config_round_trip_preserves_delivery_and_schedule(self):
        cfg = {**self.cfg, "raindrop_token": "test-token", "kindle_emails": ["test@kindle.com"],
               "smtp": {"host": "smtp.example.com", "username": "reader@example.com", "password": "test-smtp"},
               "schedule": {"timezone": "Asia/Kolkata", "slots": {"M": "09:30"}},
               "content": {"include_images": False}}
        path = self.root / "config.toml"
        with patch.object(rk, "CONFIG_DIR", self.root), patch.object(rk, "CONFIG_PATH", path):
            rk.write_config(cfg)
        result = tomllib.loads(path.read_text())
        self.assertEqual(result["ai"]["provider"], "openai")
        self.assertEqual(result["ai"]["openrouter_api_key"], "sk-or-test-old")
        self.assertEqual(result["smtp"]["password"], "test-smtp")
        self.assertEqual(result["schedule"]["slots"], {"M": "09:30"})
        self.assertEqual(result["kindle_emails"], ["test@kindle.com"])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_transcription_uses_native_multipart_upload_and_closes_file(self):
        audio = self.root / "clip.mp3"
        audio.write_bytes(b"fake-audio")
        session = Session([Response({"text": "The reported loss was $3 million.", "languages": [{"code": "en"}]})])
        ai.transcribe(session, self.cfg, audio, "gpt-transcribe")
        url, request = session.calls[0]
        self.assertEqual(url, "https://api.openai.com/v1/audio/transcriptions")
        self.assertEqual(request["data"], {"model": "gpt-transcribe"})
        self.assertNotIn("json", request)
        self.assertTrue(request["files"]["file"][1].closed)

    def test_native_transcription_has_real_clip_offsets_and_keeps_audio_cap(self):
        audio = self.root / "sample.wav"
        with wave.open(str(audio), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\0\0" * 16000 * 65)
        cfg = {"shared": self.cfg}
        with patch.object(yt, "CACHE_DIR", self.root / "cache"), patch.object(
                yt, "ai_transcribe", return_value={"text": "A recorded statement.", "languages": [{"code": "en"}]}) as call:
            lines, language = yt.cloud_transcribe(None, cfg, audio, max_minutes=1)
        self.assertEqual([stamp for stamp, _ in lines], [0, 30])
        self.assertEqual(language, "en")
        self.assertEqual(call.call_count, 2)

    def test_unknown_cost_is_not_reported_as_free(self):
        self.assertIsNone(ai.usage_cost({"usage": {"total_tokens": 100}}))
        self.assertIsNone(ai.total_cost([.01, None]))
        self.assertNotIn("$0", ai.cost_label(None))

    def test_both_pipelines_build_valid_epubs_using_only_openai_transport(self):
        class PipelineSession:
            def __init__(self):
                self.calls = []

            def post(self, url, **kwargs):
                self.calls.append(url)
                payload = kwargs["json"]
                fmt = payload["response_format"]["json_schema"]
                if fmt["name"] == "summary":
                    content = {"takeaway": "Protect capital after a $3 million loss.",
                               "paragraphs": ["[0:00] A trading desk lost $3 million after a court ruling.",
                                              "[0:30] It reduced its risk while rebuilding confidence."]}
                elif fmt["name"] == "ideas":
                    source = json.loads(payload["messages"][-1]["content"])
                    content = {"articles": [{"id": a["id"], "keywords": ["TradingLoss", "RiskLimits"]}
                                             for a in source["articles"]]}
                else:
                    source = json.loads(payload["messages"][-1]["content"])
                    content = {name: ({"choice": next(iter(q["criteria"]))} if q["type"] == "choice" else {"score": 1.5})
                               for name, q in source["questions"].items()}
                return Response(completion(content))

        transcript_dir = self.root / "transcripts"
        transcript_dir.mkdir()
        (transcript_dir / "oldvideo01A.json").write_text(json.dumps({"lines": [
            [0, "A trading desk lost $3 million after a court ruling."],
            [30, "The response was to reduce risk, review mistakes, and recover carefully."]]}))
        video = {"title": "Recovering from a trading loss", "channel": "Local test", "published": "2026-10-01T00:00:00+00:00"}
        session = PipelineSession()
        slot = datetime(2026, 11, 19, 10, tzinfo=ZoneInfo("Asia/Kolkata"))
        with patch.object(yt, "TRANSCRIPTS_DIR", transcript_dir):
            youtube_path, summaries = yt.make_book(session, {"shared": self.cfg}, [("oldvideo01A", video)],
                                                    "Nov19_M", slot, self.root / "youtube")
        articles = [rk.Article(1, {}, "Trading risk limits", "https://example.com/risk", "<p>A $3 million loss.</p>")]
        rk.assign_keywords(articles, False, session, self.cfg)
        raindrop_path = rk.build_epub("Nov19_M_TradingLoss", articles, self.root / "raindrop", slot, False)
        for path in [youtube_path, raindrop_path]:
            with zipfile.ZipFile(path) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
                content = b" ".join(archive.read(name) for name in archive.namelist() if name.endswith(".xhtml"))
                self.assertIn(b"$3 million", content)
        self.assertTrue(all(url == "https://api.openai.com/v1/chat/completions" for url in session.calls))
        self.assertTrue(summaries[0].keyword)


if __name__ == "__main__":
    unittest.main(verbosity=2)
