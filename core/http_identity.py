"""HTTP identity comes from the application, never an unrelated machine login.

Keep requests' proxy and certificate environment support, but do not consult
.netrc when preparing a request or following a redirect. Browser cookies retain
their normal scope; only a documented API/browser-login conflict is filtered.
"""
from __future__ import annotations

from copy import copy
from http.cookiejar import DefaultCookiePolicy
from urllib.parse import urlsplit

import requests
from requests.auth import AuthBase
from requests.structures import CaseInsensitiveDict
from requests.utils import get_auth_from_url

from core import site_agent
from core.redaction import sanitize_text


class _NoImplicitAuth(AuthBase):
    def __call__(self, request):
        return request


_NO_IMPLICIT_AUTH = _NoImplicitAuth()


class IdentitySession(requests.Session):
    """Use explicit request/session credentials and preserve redirect boundaries."""

    def prepare_request(self, request):
        headers = CaseInsensitiveDict(self.headers)
        headers.update(request.headers or {})
        explicit_cookie = headers.get("Cookie") is not None
        if not request.auth and not self.auth:
            # A truthy auth handler prevents requests' implicit .netrc lookup.
            # Copy the Request so reusing it does not retain our fallback auth.
            request = copy(request)
            url_auth = get_auth_from_url(request.url or "") \
                if request.auth is None and self.auth is None else None
            request.auth = url_auth if url_auth and any(url_auth) else _NO_IMPLICIT_AUTH
        prepared = super().prepare_request(request)
        self._scoped_cookies(prepared, explicit=explicit_cookie)
        self._api_cookies(prepared)
        return prepared

    def rebuild_auth(self, prepared_request, response):
        # requests' parent hook strips credentials, then unconditionally looks
        # in .netrc again. Keep the boundary check without changing identities.
        if "Authorization" in prepared_request.headers and self.should_strip_auth(
                response.request.url, prepared_request.url):
            prepared_request.headers.pop("Authorization", None)
        # Redirect cookies have already been merged by requests at this point.
        self._scoped_cookies(prepared_request)
        self._api_cookies(prepared_request)

    @staticmethod
    def _scoped_cookies(prepared, *, explicit: bool = False) -> None:
        if explicit:
            return
        jar = getattr(prepared, "_cookies", None)
        if jar is None:
            return
        # requests merges cookies into a new jar, losing the session's policy.
        # The default Netscape policy otherwise sends host-only browser cookies
        # to subdomains too. Enforce their recorded scope on each prepared jar.
        policy = copy(jar.get_policy())
        if isinstance(policy, DefaultCookiePolicy):
            policy.strict_ns_domain |= DefaultCookiePolicy.DomainStrictNonDomain
            jar.set_policy(policy)
        prepared.headers.pop("Cookie", None)
        prepared.prepare_cookies(jar)

    @staticmethod
    def _api_cookies(prepared) -> None:
        family = site_agent.family(prepared.url)
        host = (urlsplit(prepared.url).hostname or "").lower()
        rejects_browser_login = (family is not None and family.key == "danbooru") or host == "booru.allthefallen.moe"
        if not rejects_browser_login or not str(
                prepared.headers.get("Authorization") or "").lower().startswith("basic "):
            return
        cookie = prepared.headers.get("Cookie") or ""
        parts = cookie.split(";")
        kept = [part.strip() for part in parts if part.partition("=")[0].strip() != "_danbooru2_session"]
        if len(parts) == len(kept):
            return
        if any(kept):
            prepared.headers["Cookie"] = "; ".join(part for part in kept if part)
        else:
            prepared.headers.pop("Cookie", None)


def sanitize_http_error(value: object) -> str:
    """Remove credential fields from an HTTP exception before exposing its text."""
    return sanitize_text(value)


__all__ = ["IdentitySession", "sanitize_http_error"]
