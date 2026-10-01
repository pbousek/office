"""Login, accounts and user management shared by TimeTrack and Fakturace."""
import time
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from starlette.requests import HTTPConnection

from . import store

COOKIE = "office_session"
MIN_PASSWORD = 8
PUBLIC_PATHS = ("/login", "/static/", "/favicon.ico")
APP_LABELS = {"timetrack": "TimeTrack", "fakturace": "Fakturace"}

_current_user: ContextVar[dict | None] = ContextVar("office_user", default=None)
_jinja = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
                     autoescape=True)


def current_user() -> dict | None:
    """The logged-in user of the request being handled (usable from templates)."""
    return _current_user.get()


# ---------- Brute-force throttle (per client IP, in memory) ----------

_FAIL_WINDOW = 15 * 60
_FAIL_LIMIT = 10
_failures: dict[str, list[float]] = {}


def _client_ip(conn: HTTPConnection) -> str:
    # nginx appends the real client as the last X-Forwarded-For hop.
    xff = conn.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[-1].strip()
    return conn.client.host if conn.client else "?"


def _throttled(ip: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _failures.get(ip, []) if now - t < _FAIL_WINDOW]
    _failures[ip] = recent
    return len(recent) >= _FAIL_LIMIT


def _record_failure(ip: str):
    _failures.setdefault(ip, []).append(time.monotonic())


# ---------- Middleware ----------

class AuthMiddleware:
    """Requires a logged-in user (session cookie or Bearer API token) for every
    request except PUBLIC_PATHS, and enforces app access / admin-only paths."""

    def __init__(self, app, app_key: str, admin_paths: tuple[str, ...] = ()):
        self.app = app
        self.app_key = app_key
        self.admin_paths = ("/admin/",) + tuple(admin_paths)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        conn = HTTPConnection(scope)
        path = scope["path"]

        if scope["method"] not in ("GET", "HEAD", "OPTIONS") and not _same_origin(conn):
            return await _plain(403, "Cross-site request blocked.")(scope, receive, send)

        if path.startswith(PUBLIC_PATHS):
            return await self.app(scope, receive, send)

        user = store.user_for_session(conn.cookies.get(COOKIE, ""))
        auth = conn.headers.get("authorization", "")
        if not user and auth.lower().startswith("bearer "):
            user = store.user_for_api_token(auth[7:].strip())

        response = None
        if not user:
            if scope["method"] == "GET" and "application/json" not in conn.headers.get("accept", ""):
                target = path + (f"?{scope['query_string'].decode()}" if scope["query_string"] else "")
                response = RedirectResponse(f"/login?next={quote(target)}", status_code=303)
            else:
                response = JSONResponse({"error": "login required"}, status_code=401)
        elif not user[f"can_{self.app_key}"] and path != "/logout":
            response = _page(403, "denied.html", user=user,
                             app_title=APP_LABELS[self.app_key])
        elif user["must_change_password"] and path not in ("/account/password", "/logout"):
            response = RedirectResponse("/account/password", status_code=303)
        elif path.startswith(self.admin_paths) and not user["is_admin"]:
            response = _page(403, "denied.html", user=user, admin_only=True,
                             app_title=APP_LABELS[self.app_key])

        if response is not None:
            return await response(scope, receive, send)

        scope.setdefault("state", {})["user"] = user
        token = _current_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_user.reset(token)


def _same_origin(conn: HTTPConnection) -> bool:
    """Reject browser POSTs coming from another site (CSRF). Requests without
    an Origin header (curl, scripts with API token) are allowed."""
    origin = conn.headers.get("origin")
    if not origin or origin == "null":
        return origin is None
    return urlsplit(origin).netloc == conn.headers.get("host", "")


def _plain(status: int, text: str):
    return HTMLResponse(text, status_code=status)


def _page(status: int, template: str, **ctx) -> HTMLResponse:
    return HTMLResponse(_jinja.get_template(template).render(**ctx), status_code=status)


def _safe_next(target: str) -> str:
    return target if target.startswith("/") and not target.startswith("//") else "/"


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


# ---------- Routes ----------

def setup(app: FastAPI, app_key: str, jinja_env: Environment,
          admin_paths: tuple[str, ...] = ()):
    """Wire authentication into an app: middleware, routes and the
    `current_user()` template global."""
    store.init_db()
    app_title = APP_LABELS[app_key]
    jinja_env.globals["current_user"] = current_user
    router = APIRouter()

    def page(template: str, request: Request, status: int = 200, **ctx) -> HTMLResponse:
        ctx.setdefault("user", getattr(request.state, "user", None))
        return _page(status, template, app_title=app_title, app_key=app_key,
                     app_labels=APP_LABELS, min_password=MIN_PASSWORD, **ctx)

    # --- login / logout ---

    @router.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/"):
        return page("login.html", request, user=None, next=_safe_next(next))

    @router.post("/login")
    def login_submit(request: Request, username: str = Form(""), password: str = Form(""),
                     next: str = Form("/")):
        ip = _client_ip(request)
        if _throttled(ip):
            return page("login.html", request, status=429, user=None, next=_safe_next(next),
                        username=username,
                        error="Příliš mnoho neúspěšných pokusů, zkus to za 15 minut.")
        user = store.authenticate(username, password)
        if not user:
            _record_failure(ip)
            print(f"office_auth: login failed for {username!r} from {ip}", flush=True)
            return page("login.html", request, status=401, user=None, next=_safe_next(next),
                        username=username, error="Neplatné jméno nebo heslo.")
        _failures.pop(ip, None)
        response = RedirectResponse(_safe_next(next), status_code=303)
        response.set_cookie(COOKIE, store.create_session(user["id"]),
                            max_age=store.SESSION_DAYS * 86400, httponly=True,
                            samesite="lax", secure=_is_https(request))
        return response

    @router.post("/logout")
    def logout(request: Request):
        store.delete_session(request.cookies.get(COOKIE, ""))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE)
        return response

    # --- own account ---

    @router.get("/account", response_class=HTMLResponse)
    def account(request: Request, changed: int = 0):
        return page("account.html", request, changed=changed)

    @router.get("/account/password", response_class=HTMLResponse)
    def password_form(request: Request):
        return page("password.html", request)

    @router.post("/account/password")
    def password_submit(request: Request, current: str = Form(""), new: str = Form(""),
                        new2: str = Form("")):
        user = request.state.user
        error = None
        if not store.verify_password(current, user["password_hash"]):
            error = "Současné heslo nesouhlasí."
        elif len(new) < MIN_PASSWORD:
            error = f"Nové heslo musí mít aspoň {MIN_PASSWORD} znaků."
        elif new != new2:
            error = "Nová hesla se neshodují."
        elif new == current:
            error = "Nové heslo musí být jiné než současné."
        if error:
            return page("password.html", request, status=400, error=error)
        store.set_password(user["id"], new, must_change=False)
        store.delete_user_sessions(user["id"], keep_token=request.cookies.get(COOKIE, ""))
        return RedirectResponse("/account?changed=1", status_code=303)

    @router.post("/account/token")
    def account_token(request: Request, action: str = Form("new")):
        user = request.state.user
        if action == "revoke":
            store.revoke_api_token(user["id"])
            return RedirectResponse("/account", status_code=303)
        return page("account.html", request, new_token=store.new_api_token(user["id"]))

    # --- user management (admin) ---

    def users_page(request: Request, status: int = 200, **ctx):
        return page("users.html", request, status=status, users=store.list_users(), **ctx)

    def _apps(form_apps: list[str]) -> set[str]:
        return {a for a in form_apps if a in store.APPS}

    @router.get("/admin/users", response_class=HTMLResponse)
    def users_list(request: Request):
        return users_page(request)

    @router.post("/admin/users/add")
    def users_add(request: Request, username: str = Form(""), password: str = Form(""),
                  is_admin: str = Form(""), apps: list[str] = Form([])):
        username = username.strip()
        if not username:
            return users_page(request, 400, error="Zadej uživatelské jméno.")
        if len(password) < MIN_PASSWORD:
            return users_page(request, 400, error=f"Heslo musí mít aspoň {MIN_PASSWORD} znaků.")
        if store.get_user_by_name(username):
            return users_page(request, 400, error=f"Uživatel „{username}“ už existuje.")
        store.create_user(username, password, bool(is_admin), _apps(apps))
        return users_page(request, message=f"Uživatel „{username}“ založen — při prvním "
                                           f"přihlášení si musí změnit heslo.")

    @router.post("/admin/users/{user_id}/edit")
    def users_edit(request: Request, user_id: int, username: str = Form(""),
                   is_admin: str = Form(""), active: str = Form(""), apps: list[str] = Form([])):
        target = store.get_user(user_id)
        if not target:
            return users_page(request, 404, error="Uživatel neexistuje.")
        username = username.strip()
        other = store.get_user_by_name(username)
        if not username or (other and other["id"] != user_id):
            return users_page(request, 400, error="Neplatné nebo obsazené uživatelské jméno.")
        if (not is_admin or not active) and store.active_admin_count(exclude_id=user_id) == 0:
            return users_page(request, 400, error="Musí zůstat aspoň jeden aktivní admin.")
        store.update_user(user_id, username, bool(is_admin), bool(active), _apps(apps))
        return users_page(request, message=f"Uživatel „{username}“ uložen.")

    @router.post("/admin/users/{user_id}/password")
    def users_password(request: Request, user_id: int, password: str = Form("")):
        target = store.get_user(user_id)
        if not target:
            return users_page(request, 404, error="Uživatel neexistuje.")
        if len(password) < MIN_PASSWORD:
            return users_page(request, 400, error=f"Heslo musí mít aspoň {MIN_PASSWORD} znaků.")
        store.set_password(user_id, password, must_change=True)
        store.delete_user_sessions(user_id)
        return users_page(request, message=f"Heslo pro „{target['username']}“ nastaveno — "
                                           f"při příštím přihlášení si ho musí změnit.")

    @router.post("/admin/users/{user_id}/delete")
    def users_delete(request: Request, user_id: int):
        target = store.get_user(user_id)
        if not target:
            return users_page(request, 404, error="Uživatel neexistuje.")
        if user_id == request.state.user["id"]:
            return users_page(request, 400, error="Sám sebe smazat nemůžeš.")
        if store.active_admin_count(exclude_id=user_id) == 0:
            return users_page(request, 400, error="Musí zůstat aspoň jeden aktivní admin.")
        store.delete_user(user_id)
        return users_page(request, message=f"Uživatel „{target['username']}“ smazán.")

    app.include_router(router)
    app.add_middleware(AuthMiddleware, app_key=app_key, admin_paths=admin_paths)
