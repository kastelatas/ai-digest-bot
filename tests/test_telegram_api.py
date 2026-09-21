import unittest
from unittest.mock import MagicMock

from digest_bot.telegram_api import TelegramAPI, TelegramAPIError, approve_reject_keyboard


def _fake_session_returning(payload: dict) -> MagicMock:
    session = MagicMock()
    response = MagicMock()
    response.json.return_value = payload
    session.post.return_value = response
    return session


class TelegramAPITests(unittest.TestCase):
    def test_requires_bot_token(self):
        with self.assertRaises(ValueError):
            TelegramAPI(bot_token="")

    def test_send_message_returns_result_and_calls_correct_url(self):
        session = _fake_session_returning({"ok": True, "result": {"message_id": 42}})
        api = TelegramAPI(bot_token="123:ABC", session=session)

        result = api.send_message(chat_id="@chan", text="hi")

        self.assertEqual(result["message_id"], 42)
        called_url = session.post.call_args.args[0]
        self.assertIn("bot123:ABC/sendMessage", called_url)
        sent_json = session.post.call_args.kwargs["json"]
        self.assertEqual(sent_json["chat_id"], "@chan")
        self.assertEqual(sent_json["text"], "hi")

    def test_send_message_with_reply_markup_included_in_payload(self):
        session = _fake_session_returning({"ok": True, "result": {"message_id": 1}})
        api = TelegramAPI(bot_token="t", session=session)
        markup = approve_reject_keyboard(draft_id=7)

        api.send_message(chat_id=1, text="x", reply_markup=markup)

        sent_json = session.post.call_args.kwargs["json"]
        self.assertEqual(sent_json["reply_markup"], markup)

    def test_api_error_response_raises_telegram_api_error(self):
        session = _fake_session_returning({"ok": False, "error_code": 403, "description": "bot was blocked"})
        api = TelegramAPI(bot_token="t", session=session)

        with self.assertRaises(TelegramAPIError) as ctx:
            api.send_message(chat_id=1, text="x")
        self.assertEqual(ctx.exception.error_code, 403)

    def test_delete_message_returns_false_on_api_error_instead_of_raising(self):
        session = _fake_session_returning({"ok": False, "error_code": 400, "description": "message to delete not found"})
        api = TelegramAPI(bot_token="t", session=session)

        result = api.delete_message(chat_id=1, message_id=999)
        self.assertFalse(result)

    def test_get_chat_member_count_returns_int(self):
        session = _fake_session_returning({"ok": True, "result": 12345})
        api = TelegramAPI(bot_token="t", session=session)
        self.assertEqual(api.get_chat_member_count("@chan"), 12345)

    def test_get_updates_passes_offset(self):
        session = _fake_session_returning({"ok": True, "result": []})
        api = TelegramAPI(bot_token="t", session=session)
        api.get_updates(offset=100, timeout=5)
        sent_json = session.post.call_args.kwargs["json"]
        self.assertEqual(sent_json["offset"], 100)
        self.assertEqual(sent_json["timeout"], 5)


    def test_get_updates_passes_allowed_updates_only_when_given(self):
        session = _fake_session_returning({"ok": True, "result": []})
        api = TelegramAPI(bot_token="t", session=session)
        api.get_updates()
        self.assertNotIn("allowed_updates", session.post.call_args.kwargs["json"])
        api.get_updates(allowed_updates=["callback_query", "chat_member"])
        self.assertEqual(session.post.call_args.kwargs["json"]["allowed_updates"], ["callback_query", "chat_member"])

    def test_create_invite_link_truncates_name_to_telegram_limit(self):
        session = _fake_session_returning({"ok": True, "result": {"invite_link": "https://t.me/+abc"}})
        api = TelegramAPI(bot_token="t", session=session)
        result = api.create_chat_invite_link("@chan", name="x" * 50)
        self.assertEqual(result["invite_link"], "https://t.me/+abc")
        self.assertIn("createChatInviteLink", session.post.call_args.args[0])
        self.assertEqual(session.post.call_args.kwargs["json"], {"chat_id": "@chan", "name": "x" * 32})

    def test_revoke_invite_link_sends_link(self):
        session = _fake_session_returning({"ok": True, "result": {"is_revoked": True}})
        api = TelegramAPI(bot_token="t", session=session)
        api.revoke_chat_invite_link("@chan", "https://t.me/+abc")
        self.assertIn("revokeChatInviteLink", session.post.call_args.args[0])
        self.assertEqual(session.post.call_args.kwargs["json"]["invite_link"], "https://t.me/+abc")


class ApproveRejectKeyboardTests(unittest.TestCase):
    def test_callback_data_encodes_draft_id(self):
        kb = approve_reject_keyboard(draft_id=42)
        buttons = kb["inline_keyboard"][0]
        callback_data = [b["callback_data"] for b in buttons]
        self.assertEqual(callback_data, ["approve:42", "edit:42", "reject:42"])


if __name__ == "__main__":
    unittest.main()
