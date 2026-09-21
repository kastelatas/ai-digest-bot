import tempfile
import unittest
from pathlib import Path

from digest_bot.db import Database
from digest_web.auth import MAX_FAILURES, Authenticator
from tests.helpers import FakeTelegramAPI, make_config

try:
    from fastapi.testclient import TestClient

    from digest_web.app import create_app

    HAVE_WEB = True
except ImportError:  # веб-зависимости нужны только панели (digest_web/requirements.txt)
    HAVE_WEB = False


class AuthenticatorTests(unittest.TestCase):
    def test_empty_password_refuses_to_start(self):
        with self.assertRaises(RuntimeError):
            Authenticator("")

    def test_token_roundtrip_expiry_and_tampering(self):
        auth = Authenticator("secret")
        token = auth.issue_token(now=1000)
        self.assertTrue(auth.verify_token(token, now=1001))
        self.assertFalse(auth.verify_token(token, now=1000 + 8 * 24 * 3600))
        expires, _, sig = token.partition(".")
        self.assertFalse(auth.verify_token(f"{int(expires) + 999999}.{sig}", now=1001))
        self.assertFalse(auth.verify_token("garbage"))
        self.assertFalse(auth.verify_token(None))

    def test_token_from_another_password_is_rejected(self):
        self.assertFalse(Authenticator("b").verify_token(Authenticator("a").issue_token()))

    def test_lockout_after_repeated_failures_and_reset(self):
        auth = Authenticator("secret")
        for _ in range(MAX_FAILURES):
            self.assertFalse(auth.is_blocked("1.2.3.4", now=100))
            auth.register_failure("1.2.3.4", now=100)
        self.assertTrue(auth.is_blocked("1.2.3.4", now=101))
        self.assertFalse(auth.is_blocked("5.6.7.8", now=101))
        self.assertFalse(auth.is_blocked("1.2.3.4", now=100 + 10_000))  # окно истекло


@unittest.skipUnless(HAVE_WEB, "нужны fastapi и httpx (pip install -r digest_web/requirements.txt httpx)")
class WebApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "test.db")
        self.tg = FakeTelegramAPI()
        app = create_app(cfg=make_config(channel_chat_id="@chan", channel_timezone="UTC"), db=self.db,
                         telegram=self.tg, password="pw")
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self._tmp.cleanup()

    def _login(self, password="pw"):
        return self.client.post("/api/login", json={"password": password})

    def test_api_requires_login(self):
        for method, url in [("get", "/api/overview"), ("get", "/api/subscribers"), ("get", "/api/posts"),
                            ("get", "/api/posts/daily"), ("delete", "/api/posts/1")]:
            self.assertEqual(getattr(self.client, method)(url).status_code, 401, url)
        self.assertEqual(self.client.post("/api/posts", json={"text": "x"}).status_code, 401)
        self.assertEqual(self.client.patch("/api/posts/1", json={"text": "x"}).status_code, 401)

    def test_health_and_me_are_public(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        me = self.client.get("/api/me").json()
        self.assertFalse(me["authenticated"])
        self.assertTrue(me["telegram_enabled"])

    def test_wrong_password_rejected_and_locked_out_after_repeats(self):
        for _ in range(MAX_FAILURES):
            self.assertEqual(self._login("nope").status_code, 401)
        self.assertEqual(self._login("nope").status_code, 429)
        self.assertEqual(self._login("pw").status_code, 429)  # даже верный пароль — пока окно не истечёт

    def test_login_sets_httponly_strict_cookie_and_logout_clears_it(self):
        resp = self._login()
        self.assertEqual(resp.status_code, 200)
        cookie = resp.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertTrue(self.client.get("/api/me").json()["authenticated"])
        self.client.post("/api/logout")
        self.assertFalse(self.client.get("/api/me").json()["authenticated"])
        self.assertEqual(self.client.get("/api/overview").status_code, 401)

    def test_metrics_endpoints_on_empty_db(self):
        self._login()
        ov = self.client.get("/api/overview?days=7").json()
        self.assertIsNone(ov["subscribers"])
        self.assertEqual(self.client.get("/api/subscribers?days=7").json()["daily"], [])
        self.assertEqual(len(self.client.get("/api/posts/daily?days=7").json()["daily"]), 7)
        self.assertEqual(self.client.get("/api/overview?days=0").status_code, 422)

    def test_post_crud_flow(self):
        self._login()
        created = self.client.post("/api/posts", json={"text": "<b>Привет</b>", "mode": "draft"})
        self.assertEqual(created.status_code, 201)
        pid = created.json()["id"]

        listed = self.client.get("/api/posts", params={"status": "pending"}).json()
        self.assertEqual([p["id"] for p in listed["items"]], [pid])

        patched = self.client.patch(f"/api/posts/{pid}", json={"text": "Правка", "status": "approved"}).json()
        self.assertEqual((patched["text"], patched["status"]), ("Правка", "approved"))

        self.assertEqual(self.client.delete(f"/api/posts/{pid}").status_code, 204)
        self.assertEqual(self.client.get("/api/posts").json()["total"], 0)
        self.assertEqual(self.client.delete(f"/api/posts/{pid}").status_code, 404)

    def test_publish_now_and_delete_from_channel(self):
        self._login()
        post = self.client.post("/api/posts", json={"text": "В канал", "mode": "publish"}).json()
        self.assertEqual(post["status"], "published")
        self.assertEqual(self.tg.sent[-1]["chat_id"], "@chan")
        self.assertEqual(self.client.delete(f"/api/posts/{post['id']}").status_code, 204)
        self.assertEqual(self.tg.deleted[-1], ("@chan", post["channel_message_id"]))

    def test_publish_now_endpoint(self):
        self.assertEqual(self.client.post("/api/posts/1/publish").status_code, 401)
        self._login()
        pid = self.client.post("/api/posts", json={"text": "В очередь", "mode": "queue"}).json()["id"]
        resp = self.client.post(f"/api/posts/{pid}/publish")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "published")
        self.assertEqual(len(self.tg.sent), 1)
        self.assertEqual(self.client.post(f"/api/posts/{pid}/publish").status_code, 409)  # повторно нельзя
        self.assertEqual(self.client.post("/api/posts/999/publish").status_code, 404)
        self.assertEqual(len(self.tg.sent), 1)

    def test_validation_errors_are_json_with_detail(self):
        self._login()
        resp = self.client.post("/api/posts", json={"text": "  ", "mode": "draft"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("пуст", resp.json()["detail"])

    def test_spa_fallback_serves_index_but_not_for_api_paths(self):
        static = Path(self._tmp.name) / "static"
        (static / "assets").mkdir(parents=True)
        (static / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
        (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
        (Path(self._tmp.name) / "secret.txt").write_text("nope", encoding="utf-8")
        client = TestClient(create_app(cfg=make_config(), db=self.db, telegram=self.tg, password="pw",
                                       static_dir=static))
        self.assertIn("SPA", client.get("/posts").text)
        self.assertEqual(client.get("/assets/app.js").status_code, 200)
        self.assertEqual(client.get("/api/unknown").status_code, 404)
        self.assertNotIn("nope", client.get("/../secret.txt").text)  # выход за пределы static невозможен
        client.close()


if __name__ == "__main__":
    unittest.main()
