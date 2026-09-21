import json
import unittest
from unittest.mock import MagicMock

from digest_bot.llm import AnthropicSummarizer, LLMError, TemplateSummarizer, parse_draft_json
from tests.helpers import make_item


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
