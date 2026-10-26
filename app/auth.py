"""Google sign-in for a single allowed user. There is no sign-up: any other account gets a 403."""

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from .config import get_settings

router = APIRouter()
oauth = OAuth()
oauth.register(
    name="google",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile", "prompt": "select_account"},
)


class NotAuthenticated(Exception):
    """Raised by page dependencies; the app turns it into a redirect to /login."""


def _configure() -> None:
    s = get_settings()
    oauth.google.client_id = s.google_client_id
    oauth.google.client_secret = s.google_client_secret


def current_user(request: Request) -> str | None:
    s = get_settings()
    if s.auth_dev_bypass and s.is_local:
        return s.allowed_email or "dev@localhost"
    email = request.session.get("email")
    return email if email and email == s.allowed_email.lower() else None


def require_page_user(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise NotAuthenticated()
    return user


def require_api_user(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Not signed in")
    return user


@router.get("/auth/google")
async def login_google(request: Request):
    _configure()
    return await oauth.google.authorize_redirect(request, request.url_for("auth_callback"))


@router.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request):
    _configure()
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError:
        return RedirectResponse("/login?error=failed", status_code=303)
    info = token.get("userinfo") or {}
    email = (info.get("email") or "").lower()
    if not info.get("email_verified") or email != get_settings().allowed_email.lower():
        request.session.clear()
        raise HTTPException(403, "This account is not allowed to use this app")
    request.session.clear()
    request.session["email"] = email
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
