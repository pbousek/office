"""Login, password change, user management, API token (shared office_auth)."""
import re

from conftest import PASSWORD, client, login


def test_anonymous_is_sent_to_login():
    c = client()
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert c.post("/entries/add", data={}).status_code == 401
    assert c.get("/login").status_code == 200
    assert c.get("/static/style.css").status_code == 200


def test_first_login_forces_password_change():
    c = client()
    assert c.post("/login", data={"username": "admin", "password": "spatne"}).status_code == 401
    r = c.post("/login", data={"username": "admin", "password": "admin", "next": "/settings"})
    assert r.status_code == 303 and r.headers["location"] == "/settings"
    assert c.get("/").headers["location"] == "/account/password"
    r = c.post("/account/password", data={"current": "admin", "new": "short", "new2": "short"})
    assert r.status_code == 400
    r = c.post("/account/password", data={"current": "admin", "new": PASSWORD, "new2": PASSWORD})
    assert r.status_code == 303
    r = c.get("/")
    assert r.status_code == 200 and "Odhlásit" in r.text


def test_login_throttled_after_repeated_failures():
    c = client()
    for _ in range(10):
        c.post("/login", data={"username": "admin", "password": "x"})
    assert c.post("/login", data={"username": "admin", "password": "admin"}).status_code == 429


def test_forgot_link_hidden_without_smtp():
    assert "Zapomenuté heslo" not in client().get("/login").text


def test_user_management(admin):
    r = admin.get("/admin/users")
    assert r.status_code == 200 and "Nový uživatel" in r.text
    r = admin.post("/admin/users/add",
                   data={"username": "oksi", "password": "docasne123", "apps": ["fakturace"]})
    assert "založen" in r.text
    # the last active admin cannot be demoted
    r = admin.post("/admin/users/1/edit",
                   data={"username": "admin", "active": "1", "apps": ["timetrack", "fakturace"]})
    assert r.status_code == 400


def test_app_access_and_admin_only_pages(admin):
    admin.post("/admin/users/add", data={"username": "oksi", "password": "docasne123",
                                         "apps": ["fakturace"]})
    admin.post("/admin/users/add", data={"username": "kamos", "password": "docasne123",
                                         "apps": ["timetrack"]})
    oksi = login("oksi", "docasne123")
    r = oksi.get("/")
    assert r.status_code == 403 and "nemá přístup" in r.text
    kamos = login("kamos", "docasne123", "kamosheslo")
    assert kamos.get("/").status_code == 200
    assert kamos.get("/admin/users").status_code == 403


def test_cross_site_post_blocked(admin):
    r = admin.post("/admin/users/1/edit", data={"username": "admin"},
                   headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_cross_app_link_only_with_access(admin):
    assert 'href="http://localhost:8732"' in admin.get("/").text
    admin.post("/admin/users/add", data={"username": "kamos", "password": "docasne123",
                                         "apps": ["timetrack"]})
    kamos = login("kamos", "docasne123", "kamosheslo")
    assert 'href="http://localhost:8732"' not in kamos.get("/").text


def test_api_token(admin):
    r = admin.post("/account/token", data={"action": "new"})
    token = re.search(r"oft_[\w-]+", r.text).group(0)
    c = client()
    r = c.get("/api/day", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and "by_customer" in r.json()
    r = c.get("/api/day", headers={"Authorization": "Bearer oft_bad", "Accept": "application/json"})
    assert r.status_code == 401


def test_logout(admin):
    admin.post("/logout")
    assert admin.get("/").status_code == 303
