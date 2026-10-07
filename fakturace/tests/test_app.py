"""Fakturace-specific behaviour on top of the shared login."""
from datetime import date, timedelta

from conftest import client, login


def test_anonymous_is_sent_to_login():
    r = client().get("/")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_admin_pages(admin):
    r = admin.get("/")
    assert r.status_code == 200 and 'href="/settings"' in r.text
    assert 'href="http://localhost:8731"' in r.text          # link to TimeTrack
    assert admin.get("/settings").status_code == 200
    assert admin.get("/billing").status_code == 200


def test_invoicing_user_without_settings(admin):
    admin.post("/admin/users/add", data={"username": "oksi", "password": "docasne123",
                                         "apps": ["fakturace"]})
    oksi = login("oksi", "docasne123", "oksiheslo1")
    r = oksi.get("/")
    assert r.status_code == 200 and 'href="/settings"' not in r.text
    assert 'href="http://localhost:8731"' not in r.text       # no TimeTrack access
    assert oksi.get("/settings").status_code == 403
    assert oksi.get("/invoices/new").status_code == 200
    oksi.post("/logout")
    assert oksi.get("/").status_code == 303


def test_previous_month_is_preselected(admin):
    last = date.today().replace(day=1) - timedelta(days=1)
    r = admin.get("/billing")
    assert f'<option value="{last.month}" selected>' in r.text
    assert f'<option value="{last.year}" selected>' in r.text


def test_menu_says_banka(admin):
    assert '>Banka</a>' in admin.get("/").text
