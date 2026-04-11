"""Firebase Authentication via Identity Toolkit / securetoken REST (desktop, no Admin SDK)."""

from __future__ import annotations

import os
import urllib.parse
from typing import Any

import httpx
import keyring

try:
    import firebase_embedded as _firebase_embedded
except ImportError:
    _firebase_embedded = None

KEYRING_SERVICE = "RootRecord"
KEYRING_REFRESH = "firebase_refresh_token"
KEYRING_EMAIL = "firebase_account_email_cache"
KEYRING_LOCAL_ID = "firebase_local_id_cache"


def _web_api_key_raw() -> str:
    """Env / .env first, then shipped firebase_embedded (production client config in the exe)."""
    k = os.environ.get("FIREBASE_WEB_API_KEY", "").strip()
    if k:
        return k
    k = os.environ.get("FIREBASE_API_KEY", "").strip()
    if k:
        return k
    if _firebase_embedded is not None:
        k = str(getattr(_firebase_embedded, "FIREBASE_WEB_API_KEY", "") or "").strip()
        if k:
            return k
    return ""


def firebase_configured() -> bool:
    return bool(_web_api_key_raw())


def _api_key() -> str:
    k = _web_api_key_raw()
    if not k:
        raise RuntimeError("FIREBASE_WEB_API_KEY is not set")
    return k


def _google_oauth_id_secret() -> tuple[str, str]:
    cid = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    sec = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if cid and sec:
        return cid, sec
    if _firebase_embedded is not None:
        cid = str(getattr(_firebase_embedded, "GOOGLE_OAUTH_CLIENT_ID", "") or "").strip()
        sec = str(getattr(_firebase_embedded, "GOOGLE_OAUTH_CLIENT_SECRET", "") or "").strip()
        if cid and sec:
            return cid, sec
    return "", ""


def google_oauth_configured() -> bool:
    cid, sec = _google_oauth_id_secret()
    return bool(cid and sec)


def decode_id_token_payload(id_token: str) -> dict[str, Any]:
    """Decode JWT payload (Firebase / Google id_token) without verifying signature."""
    try:
        import base64
        import json

        parts = str(id_token).split(".")
        if len(parts) != 3:
            return {}
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        return dict(json.loads(base64.urlsafe_b64decode(pad)))
    except Exception:
        return {}


def list_linked_provider_ids(id_token: str) -> list[str]:
    """
    Return provider IDs attached to this account (e.g. ``password``, ``google.com``).
    Uses Identity Toolkit ``accounts:lookup`` (accurate for linked accounts).
    """
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={_api_key()}"
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, json={"idToken": id_token})
    data = r.json()
    if r.status_code != 200:
        return []
    users = data.get("users") or []
    if not users:
        return []
    prows = users[0].get("providerUserInfo") or []
    out: list[str] = []
    for p in prows:
        pid = str(p.get("providerId") or "").strip()
        if pid:
            out.append(pid)
    return out


def human_labels_for_provider_ids(provider_ids: list[str]) -> list[str]:
    labels: list[str] = []
    for pid in provider_ids:
        if pid == "google.com":
            labels.append("Google")
        elif pid == "password":
            labels.append("Email/password")
        else:
            labels.append(pid)
    return labels


def firebase_auth_summary_from_id_token(id_token: str) -> dict[str, Any]:
    """
    Sign-in summary: prefers ``accounts:lookup``; falls back to JWT ``firebase`` claims.
    Keys: sign_in_provider, linked_labels, provider_ids, identities_keys.
    """
    payload = decode_id_token_payload(id_token)
    fb = payload.get("firebase") if isinstance(payload.get("firebase"), dict) else {}
    sip = str(fb.get("sign_in_provider") or "").strip()
    identities = fb.get("identities") if isinstance(fb.get("identities"), dict) else {}
    id_keys = [str(k) for k in identities.keys()]
    pids = list_linked_provider_ids(id_token)
    if pids:
        return {
            "sign_in_provider": sip,
            "linked_labels": human_labels_for_provider_ids(pids),
            "provider_ids": pids,
            "identities_keys": id_keys,
        }
    labels: list[str] = []
    if "google.com" in identities:
        labels.append("Google")
    if sip == "password":
        labels.append("Email/password")
    if not labels:
        if sip == "google.com":
            labels.append("Google")
        elif sip:
            labels.append(sip)
        else:
            labels.append("Unknown")
    return {
        "sign_in_provider": sip,
        "linked_labels": sorted(set(labels)),
        "provider_ids": [],
        "identities_keys": id_keys,
    }


def google_account_linked(id_token: str) -> bool:
    """True if Google is linked or used for this account."""
    for pid in list_linked_provider_ids(id_token):
        if pid == "google.com":
            return True
    return google_account_linked_in_jwt(id_token)


def google_account_linked_in_jwt(id_token: str) -> bool:
    payload = decode_id_token_payload(id_token)
    fb = payload.get("firebase") if isinstance(payload.get("firebase"), dict) else {}
    identities = fb.get("identities") if isinstance(fb.get("identities"), dict) else {}
    if "google.com" in identities:
        return True
    return str(fb.get("sign_in_provider") or "") == "google.com"


def fetch_sign_in_methods_for_email(email: str) -> list[str]:
    """
    Ask Firebase which providers are registered for this email (before sign-in).
    """
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:createAuthUri?key={_api_key()}"
    payload = {
        "identifier": email.strip(),
        "continueUri": "http://localhost",
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, json=payload)
    data = r.json()
    if r.status_code != 200:
        return []
    raw = data.get("signinMethods") or data.get("allProviders") or []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return []


def friendly_firebase_error(message: str) -> str:
    """Map common Identity Toolkit messages to short user guidance."""
    m = (message or "").strip()
    low = m.lower()
    if "email already exists" in low or m == "EMAIL_EXISTS":
        return (
            "This email is already registered. Sign in with your existing method first "
            "(password or Google). To add Google to an email/password account, sign in with "
            "password, then use Account Settings → Link Google account."
        )
    if "credential already in use" in low or "credential is already associated" in low:
        return "This Google account is already linked to another user."
    if "invalid idp response" in low or "invalid id_token" in low:
        return "Google sign-in token was rejected. Try again, or check OAuth client settings in Google Cloud Console."
    if "redirect_uri_mismatch" in low:
        return "OAuth redirect URI mismatch — use a Desktop OAuth client in the same project as Firebase."
    return m


def _google_id_token_from_credentials(creds: Any) -> str | None:
    t = getattr(creds, "id_token", None)
    if t:
        return str(t).strip() or None
    tr = getattr(creds, "token_response", None)
    if isinstance(tr, dict) and tr.get("id_token"):
        return str(tr["id_token"]).strip() or None
    return None


def _obtain_google_oauth_tokens() -> tuple[str | None, str]:
    """
    Run Google desktop OAuth (browser + loopback). Returns (google_id_token, access_token).
    """
    cid, csec = _google_oauth_id_secret()
    if not cid or not csec:
        raise RuntimeError(
            "Google sign-in is not configured. Set GOOGLE_OAUTH_CLIENT_ID and "
            "GOOGLE_OAUTH_CLIENT_SECRET (Desktop OAuth client in the Firebase project)."
        )
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError("Google sign-in requires google-auth-oauthlib (bundled in normal installs).") from exc

    scopes = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]
    client_cfg = {
        "installed": {
            "client_id": cid,
            "client_secret": csec,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost", "http://127.0.0.1"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_cfg, scopes=scopes)
    try:
        creds = flow.run_local_server(
            port=0,
            prompt="consent",
            access_type="offline",
            include_granted_scopes="true",
            success_message="<p style='font-family:system-ui'>You can close this tab and return to RootRecord.</p>",
        )
    except TypeError:
        # Older google-auth-oauthlib without access_type / include_granted_scopes kwargs
        creds = flow.run_local_server(
            port=0,
            prompt="consent",
            success_message="<p style='font-family:system-ui'>You can close this tab and return to RootRecord.</p>",
        )
    except Exception as exc:
        msg = str(exc)
        if "redirect_uri_mismatch" in msg:
            raise RuntimeError(
                "Google OAuth redirect URI mismatch. Create an OAuth client of type Desktop app "
                "in Google Cloud (same project as Firebase), and copy Client ID/Secret into the app config."
            ) from exc
        raise
    id_tok = _google_id_token_from_credentials(creds)
    access = getattr(creds, "token", None) or ""
    if not id_tok and access:
        try:
            from google.auth.transport.requests import Request as GoogleAuthRequest

            creds.refresh(GoogleAuthRequest())
            id_tok = _google_id_token_from_credentials(creds)
        except Exception:
            pass
    if not id_tok and not access:
        raise RuntimeError("Google did not return tokens. Try again or check OAuth client configuration.")
    return id_tok, access


def _firebase_sign_in_with_google_idp(
    *,
    google_id_token: str | None,
    access_token: str,
    firebase_link_id_token: str | None = None,
) -> dict[str, Any]:
    """
    Call Identity Toolkit ``accounts:signInWithIdp``.

    If ``firebase_link_id_token`` is set, links Google to the signed-in user (merge providers).
    """
    if google_id_token:
        post_body = urllib.parse.urlencode(
            {"id_token": google_id_token, "providerId": "google.com"}
        )
    else:
        post_body = urllib.parse.urlencode(
            {"access_token": access_token, "providerId": "google.com"}
        )
    payload: dict[str, Any] = {
        "requestUri": "http://localhost",
        "postBody": post_body,
        "returnSecureToken": True,
    }
    if firebase_link_id_token:
        payload["idToken"] = firebase_link_id_token
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp?key={_api_key()}"
    with httpx.Client(timeout=90.0) as client:
        r = client.post(url, json=payload)
    data = r.json()
    if r.status_code != 200:
        msg = data.get("error", {}).get("message", r.text)
        raise RuntimeError(friendly_firebase_error(str(msg)))
    rt = data.get("refreshToken")
    if not rt:
        raise RuntimeError("No refreshToken in Firebase response")
    email = (
        str(data.get("email") or data.get("emailAddress") or "").strip()
        or None
    )
    if not email and data.get("idToken"):
        try:
            pl = decode_id_token_payload(str(data["idToken"]))
            email = str(pl.get("email") or "").strip() or None
        except Exception:
            email = None
    _save_refresh_token(rt, email=email)
    cache_firebase_local_id_from_auth_response(data)
    return data


def sign_in_with_google() -> dict[str, Any]:
    """
    Desktop OAuth then Firebase ``accounts:signInWithIdp`` (new or returning Google user).
    """
    id_tok, access = _obtain_google_oauth_tokens()
    return _firebase_sign_in_with_google_idp(google_id_token=id_tok, access_token=access)


def link_google_to_current_user() -> dict[str, Any]:
    """
    Link Google to the **currently signed-in** Firebase user (e.g. email/password account).

    User must complete Google OAuth in the browser. Requires the Google account email to match
    the Firebase account email unless Firebase project allows account linking for mismatched emails
    (default: same email recommended).
    """
    link_tok = get_valid_id_token()
    if not link_tok:
        raise RuntimeError("Sign in with email/password first, then use Link Google.")
    id_tok, access = _obtain_google_oauth_tokens()
    return _firebase_sign_in_with_google_idp(
        google_id_token=id_tok,
        access_token=access,
        firebase_link_id_token=link_tok,
    )


def cache_firebase_local_id_from_auth_response(data: dict[str, Any]) -> None:
    """Persist Firebase localId for per-account local data paths (desktop)."""
    lid = str(data.get("localId") or "").strip()
    if not lid:
        id_tok = data.get("idToken")
        if isinstance(id_tok, str) and id_tok.strip():
            lid = get_local_id_from_token(id_tok.strip()) or ""
    if not lid:
        return
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_LOCAL_ID, lid)
    except Exception:
        pass


def _save_refresh_token(refresh_token: str, email: str | None = None) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_REFRESH, refresh_token)
    if email:
        keyring.set_password(KEYRING_SERVICE, KEYRING_EMAIL, email)


def _load_refresh_token() -> str | None:
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_REFRESH)
    except Exception:
        return None


def _load_cached_email() -> str | None:
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_EMAIL)
    except Exception:
        return None


def clear_stored_credentials() -> None:
    """Remove refresh token and cached email from the OS keyring."""
    for name in (KEYRING_REFRESH, KEYRING_EMAIL, KEYRING_LOCAL_ID):
        try:
            keyring.delete_password(KEYRING_SERVICE, name)
        except Exception:
            pass


def sign_in_with_password(email: str, password: str) -> dict[str, Any]:
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={_api_key()}"
    payload = {"email": email.strip(), "password": password, "returnSecureToken": True}
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, json=payload)
    data = r.json()
    if r.status_code != 200:
        msg = data.get("error", {}).get("message", r.text)
        raise RuntimeError(friendly_firebase_error(str(msg)))
    rt = data.get("refreshToken")
    if not rt:
        raise RuntimeError("No refreshToken in response")
    _save_refresh_token(rt, email=data.get("email") or email.strip())
    cache_firebase_local_id_from_auth_response(data)
    return data


def sign_up(email: str, password: str) -> dict[str, Any]:
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={_api_key()}"
    payload = {"email": email.strip(), "password": password, "returnSecureToken": True}
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, json=payload)
    data = r.json()
    if r.status_code != 200:
        msg = data.get("error", {}).get("message", r.text)
        raise RuntimeError(friendly_firebase_error(str(msg)))
    rt = data.get("refreshToken")
    if not rt:
        raise RuntimeError("No refreshToken in response")
    _save_refresh_token(rt, email=data.get("email") or email.strip())
    cache_firebase_local_id_from_auth_response(data)
    return data


def refresh_id_token() -> str | None:
    """Return a fresh id_token, or None if not signed in / refresh failed."""
    rt = _load_refresh_token()
    if not rt:
        return None
    url = f"https://securetoken.googleapis.com/v1/token?key={_api_key()}"
    body = {"grant_type": "refresh_token", "refresh_token": rt}
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, data=body)
    data = r.json()
    if r.status_code != 200:
        return None
    new_rt = data.get("refresh_token")
    id_tok = data.get("id_token")
    if new_rt:
        email = _load_cached_email()
        _save_refresh_token(new_rt, email=email)
    if id_tok and isinstance(id_tok, str):
        uid = get_local_id_from_token(id_tok)
        if uid:
            try:
                keyring.set_password(KEYRING_SERVICE, KEYRING_LOCAL_ID, uid)
            except Exception:
                pass
    return id_tok


def get_cached_firebase_local_id() -> str | None:
    try:
        v = keyring.get_password(KEYRING_SERVICE, KEYRING_LOCAL_ID)
        return str(v).strip() if v else None
    except Exception:
        return None


def ensure_firebase_local_id_cached() -> str | None:
    """Return Firebase uid for path routing; refresh token if missing from keyring (upgrade path)."""
    lid = get_cached_firebase_local_id()
    if lid:
        return lid
    id_tok = refresh_id_token()
    if not id_tok:
        return None
    uid = get_local_id_from_token(id_tok)
    if uid:
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_LOCAL_ID, uid)
        except Exception:
            pass
        return uid
    return None


def current_firebase_local_id() -> str | None:
    """
    Best-effort current Firebase uid for desktop path routing.
    Prefer a fresh id token (current session), then fallback to cached localId.
    """
    if not is_signed_in():
        return None
    id_tok = refresh_id_token()
    if id_tok:
        uid = get_local_id_from_token(id_tok)
        if uid:
            try:
                keyring.set_password(KEYRING_SERVICE, KEYRING_LOCAL_ID, uid)
            except Exception:
                pass
            return uid
    return get_cached_firebase_local_id()


def get_valid_id_token() -> str | None:
    """Refresh and return id_token for Storage / authenticated calls."""
    return refresh_id_token()


def get_local_id_from_token(id_token: str) -> str | None:
    """Decode JWT payload without verification (server already issued token)."""
    try:
        import base64
        import json

        parts = id_token.split(".")
        if len(parts) != 3:
            return None
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(pad))
        uid = str(payload.get("sub") or payload.get("user_id") or "").strip()
        return uid or None
    except Exception:
        return None


def current_account_email() -> str | None:
    """Best-effort display email: cached keyring value."""
    return _load_cached_email()


def is_signed_in() -> bool:
    return _load_refresh_token() is not None
