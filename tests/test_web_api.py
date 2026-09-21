import tempfile
import time
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

    def test_links_require_login(self):
        for method, url in [("get", "/api/links"), ("get", "/api/links/1/daily"),
                            ("get", "/api/links/organic/daily"), ("post", "/api/links/1/revoke")]:
            self.assertEqual(getattr(self.client, method)(url).status_code, 401, url)
        self.assertEqual(self.client.post("/api/links", json={"name": "x"}).status_code, 401)
        self.assertEqual(self.client.patch("/api/links/1", json={"name": "x"}).status_code, 401)

    def test_link_flow_create_stats_edit_revoke(self):
        from digest_bot.moderation import process_admin_updates

        self._login()
        created = self.client.post("/api/links", json={
            "name": "seed_habr", "cost": 100, "currency": "usd", "ad_text": "Заходи: {link}",
        })
        self.assertEqual(created.status_code, 201)
        link = created.json()
        self.assertEqual((link["joined"], link["currency"], link["status"]), (0, "USD", "active"))
        self.assertEqual(self.tg.invite_links[0]["chat_id"], "@chan")

        now = int(time.time())
        self.tg.queue_chat_member(1, joined=True, chat_username="chan", invite_link=link["url"], date=now)
        self.tg.queue_chat_member(2, joined=True, chat_username="chan", invite_link=link["url"], date=now)
        self.tg.queue_chat_member(3, joined=True, chat_username="chan", date=now)
        self.tg.queue_chat_member(2, joined=False, chat_username="chan", date=now)
        process_admin_updates(make_config(channel_chat_id="@chan"), self.db, self.tg)

        listed = self.client.get("/api/links").json()
        item = listed["items"][0]
        self.assertEqual((item["joined"], item["left"], item["retained"], item["retention_pct"]), (2, 1, 1, 50))
        self.assertEqual((item["cost_per_join"], item["cost_per_retained"]), (50.0, 100.0))
        self.assertEqual(listed["organic"], {"joined": 1, "left": 0, "retained": 1})
        self.assertIsNotNone(listed["tracking_since"])

        daily = self.client.get(f"/api/links/{link['id']}/daily?days=7").json()
        self.assertEqual(len(daily["daily"]), 7)
        self.assertEqual({k: daily["daily"][-1][k] for k in ("joined", "left")}, {"joined": 2, "left": 1})
        self.assertEqual(sum(r["joined"] for r in daily["daily"][:-1]), 0)
        self.assertEqual(daily["link"]["id"], link["id"])
        organic = self.client.get("/api/links/organic/daily?days=7").json()
        self.assertIsNone(organic["link"])
        self.assertEqual(organic["daily"][-1]["joined"], 1)

        patched = self.client.patch(f"/api/links/{link['id']}", json={"name": "seed_dou", "cost": None}).json()
        self.assertEqual((patched["name"], patched["cost"], patched["ad_text"]), ("seed_dou", None, "Заходи: {link}"))
        self.assertIsNone(patched["cost_per_join"])

        revoked = self.client.post(f"/api/links/{link['id']}/revoke").json()
        self.assertEqual(revoked["status"], "revoked")
        self.assertEqual(self.tg.revoked_links, [link["url"]])
        self.assertEqual(self.client.post(f"/api/links/{link['id']}/revoke").status_code, 409)

    def test_link_validation_and_errors(self):
        self._login()
        self.assertEqual(self.client.post("/api/links", json={"name": "  "}).status_code, 400)
        self.assertEqual(self.client.post("/api/links", json={"name": "x" * 65}).status_code, 400)
        self.assertEqual(self.client.post("/api/links", json={"name": "x", "cost": -1}).status_code, 400)
        self.assertEqual(self.client.post("/api/links", json={"name": "x", "currency": "dollars"}).status_code, 400)
        self.assertEqual(self.client.get("/api/links/999/daily").status_code, 404)
        self.assertEqual(self.client.patch("/api/links/999", json={"name": "x"}).status_code, 404)
        self.assertEqual(self.client.post("/api/links/999/revoke").status_code, 404)
        self.assertEqual(self.client.get("/api/links").json()["items"], [])

    def test_telegram_refusal_gives_502_with_hint_and_saves_nothing(self):
        client = TestClient(create_app(cfg=make_config(channel_chat_id="@chan"), db=self.db,
                                       telegram=FakeTelegramAPI(fail_invite=True), password="pw"))
        client.post("/api/login", json={"password": "pw"})
        resp = client.post("/api/links", json={"name": "x"})
        self.assertEqual(resp.status_code, 502)
        self.assertIn("Приглашать пользователей", resp.json()["detail"])
        self.assertEqual(client.get("/api/links").json()["items"], [])
        client.close()

    def test_forced_revoke_marks_link_in_panel_when_telegram_refuses(self):
        self._login()
        link = self.client.post("/api/links", json={"name": "x"}).json()
        failing = TestClient(create_app(cfg=make_config(channel_chat_id="@chan"), db=self.db,
                                        telegram=FakeTelegramAPI(fail_invite=True), password="pw"))
        failing.post("/api/login", json={"password": "pw"})
        self.assertEqual(failing.post(f"/api/links/{link['id']}/revoke").status_code, 502)
        self.assertEqual(failing.post(f"/api/links/{link['id']}/revoke?force=true").json()["status"], "revoked")
        failing.close()

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
