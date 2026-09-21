import json
import unittest
from unittest.mock import MagicMock

from digest_bot.llm import (
    AnthropicSummarizer,
    LLMError,
    OpenAICompatSummarizer,
    TemplateSummarizer,
    build_summarizer,
    parse_draft_json,
)
from tests.helpers import make_config, make_item


class ParseDraftJsonTests(unittest.TestCase):
    def test_valid_json(self):
        raw = json.dumps({"ok": True, "hook": "Заголовок", "body": "Текст поста", "tags": ["ai"]})
        result = parse_draft_json(raw)
        self.assertTrue(result.ok)
        self.assertEqual(result.hook, "Заголовок")
        self.assertEqual(result.tags, ["ai"])

    def test_json_wrapped_in_markdown_fence(self):
        raw = "```json\n" + json.dumps({"ok": True, "hook": "H", "body": "B", "tags": []}) + "\n```"
        result = parse_draft_json(raw)
        self.assertTrue(result.ok)
        self.assertEqual(result.hook, "H")

    def test_model_declines_explicitly(self):
        raw = json.dumps({"ok": False, "reason": "мало фактов"})
        result = parse_draft_json(raw)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "мало фактов")

    def test_invalid_json_does_not_raise(self):
        result = parse_draft_json("это не json вообще")
        self.assertFalse(result.ok)
        self.assertIn("invalid_json", result.reason)

    def test_missing_required_fields_declines(self):
        raw = json.dumps({"ok": True, "hook": "", "body": ""})
        result = parse_draft_json(raw)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "missing_hook_or_body")

    def test_missing_ok_field_declines(self):
        raw = json.dumps({"hook": "H", "body": "B"})
        result = parse_draft_json(raw)
        self.assertFalse(result.ok)


class AnthropicSummarizerTests(unittest.TestCase):
    def test_requires_api_key(self):
        with self.assertRaises(LLMError):
            AnthropicSummarizer(api_key="")

    def test_summarize_parses_successful_response(self):
        fake_session = MagicMock()
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "content": [
                {"type": "text", "text": json.dumps({"ok": True, "hook": "H", "body": "B", "tags": ["x"]})}
            ]
        }
        fake_session.post.return_value = fake_response

        summarizer = AnthropicSummarizer(api_key="test-key", session=fake_session)
        item = make_item()
        result = summarizer.summarize(item)

        self.assertTrue(result.ok)
        self.assertEqual(result.hook, "H")
        # проверяем, что реально ушёл вызов с нужным URL и заголовком авторизации
        called_url = fake_session.post.call_args.args[0]
        called_headers = fake_session.post.call_args.kwargs["headers"]
        self.assertIn("anthropic.com", called_url)
        self.assertEqual(called_headers["x-api-key"], "test-key")

    def test_summarize_raises_llm_error_on_network_failure(self):
        import requests

        fake_session = MagicMock()
        fake_session.post.side_effect = requests.ConnectionError("boom")
        summarizer = AnthropicSummarizer(api_key="test-key", session=fake_session)
        with self.assertRaises(LLMError):
            summarizer.summarize(make_item())


def _ok_response(payload: dict):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


class OpenAICompatSummarizerTests(unittest.TestCase):
    def _summarizer(self, session, **kw):
        return OpenAICompatSummarizer(api_key="or-key", model="google/gemini-3.1-flash-lite", session=session, **kw)

    def test_requires_api_key(self):
        with self.assertRaises(LLMError):
            OpenAICompatSummarizer(api_key="", model="m")

    def test_summarize_parses_response_and_sends_openrouter_request(self):
        content = json.dumps({"ok": True, "hook": "H", "body": "B", "tags": ["x"]})
        session = MagicMock()
        session.post.return_value = _ok_response({"choices": [{"message": {"content": content}}]})

        result = self._summarizer(session, max_tokens=500, temperature=0.1).summarize(make_item())

        self.assertTrue(result.ok)
        self.assertEqual(result.hook, "H")
        self.assertEqual(session.post.call_args.args[0], "https://openrouter.ai/api/v1/chat/completions")
        kwargs = session.post.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer or-key")
        body = kwargs["json"]
        self.assertEqual(body["model"], "google/gemini-3.1-flash-lite")
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertEqual((body["max_tokens"], body["temperature"]), (500, 0.1))

    def test_custom_base_url_trailing_slash_is_normalised(self):
        session = MagicMock()
        session.post.return_value = _ok_response({"choices": [{"message": {"content": '{"ok": false}'}}]})
        self._summarizer(session, base_url="https://example.com/v1/").summarize(make_item())
        self.assertEqual(session.post.call_args.args[0], "https://example.com/v1/chat/completions")

    def test_markdown_fenced_json_is_accepted(self):
        content = "```json\n" + json.dumps({"ok": True, "hook": "H", "body": "B"}) + "\n```"
        session = MagicMock()
        session.post.return_value = _ok_response({"choices": [{"message": {"content": content}}]})
        self.assertTrue(self._summarizer(session).summarize(make_item()).ok)

    def test_error_body_with_http_200_raises_llm_error(self):
        session = MagicMock()
        session.post.return_value = _ok_response({"error": {"message": "rate limited", "code": 429}})
        with self.assertRaises(LLMError):
            self._summarizer(session).summarize(make_item())

    def test_empty_content_raises_llm_error(self):
        session = MagicMock()
        session.post.return_value = _ok_response({"choices": [{"message": {"content": None}}]})
        with self.assertRaises(LLMError):
            self._summarizer(session).summarize(make_item())

    def test_http_error_raises_llm_error(self):
        import requests

        session = MagicMock()
        resp = MagicMock()
        resp.raise_for_status.side_effect = requests.HTTPError("429 Too Many Requests")
        session.post.return_value = resp
        with self.assertRaises(LLMError):
            self._summarizer(session).summarize(make_item())


class BuildSummarizerTests(unittest.TestCase):
    def test_openrouter_with_key(self):
        cfg = make_config(llm_provider="openrouter", llm_model="google/gemini-3.1-flash-lite", openrouter_api_key="k")
        s = build_summarizer(cfg)
        self.assertIsInstance(s, OpenAICompatSummarizer)
        self.assertEqual(s.model, "google/gemini-3.1-flash-lite")

    def test_openrouter_custom_base_url(self):
        cfg = make_config(llm_provider="OpenRouter", openrouter_api_key="k", llm_base_url="https://proxy.local/v1")
        self.assertEqual(build_summarizer(cfg).url, "https://proxy.local/v1/chat/completions")

    def test_anthropic_with_key(self):
        cfg = make_config(llm_provider="anthropic", anthropic_api_key="k")
        self.assertIsInstance(build_summarizer(cfg), AnthropicSummarizer)

    def test_missing_key_falls_back_to_template_for_each_provider(self):
        for provider in ("openrouter", "anthropic"):
            cfg = make_config(llm_provider=provider, anthropic_api_key="", openrouter_api_key="")
            self.assertIsInstance(build_summarizer(cfg), TemplateSummarizer)

    def test_key_of_other_provider_is_not_used(self):
        cfg = make_config(llm_provider="openrouter", anthropic_api_key="k", openrouter_api_key="")
        self.assertIsInstance(build_summarizer(cfg), TemplateSummarizer)

    def test_unknown_provider_raises(self):
        with self.assertRaises(LLMError):
            build_summarizer(make_config(llm_provider="bogus"))


class TemplateSummarizerTests(unittest.TestCase):
    def test_produces_ok_draft_from_summary(self):
        result = TemplateSummarizer().summarize(make_item(title="T", summary="S"))
        self.assertTrue(result.ok)
        self.assertEqual(result.hook, "T")

    def test_declines_when_no_source_text_at_all(self):
        item = make_item(title="", summary="")
        result = TemplateSummarizer().summarize(item)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
