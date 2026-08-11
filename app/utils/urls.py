"""
Absolute URL construction for links that leave the system.

Any link that travels outside the application (e-mail, notification, password
token) must point at the real public host. `url_for(..., _external=True)`
takes the host from the *live request*, and that host is controlled by whoever
makes the request: nginx forwards `Host` verbatim and `ProxyFix(x_host=1)`
additionally honours `X-Forwarded-Host`. An attacker able to trigger a staff
action (bulk student creation, token resend) could therefore mint links to
their own domain carrying a valid 7-day password token.

`external_url()` builds the URL from `APP_BASE_URL` — server configuration —
and never from the request. It also works *without* a request context (Celery
tasks), where `url_for(_external=True)` raises because `SERVER_NAME` is unset.

Context requirements — this is where e-mail bodies get rendered, so be exact:

* **Request context**: never needed, never consulted.
* **Application context**: required by `external_url()`, because the URL map
  and the url-defaults hooks live on the app object. Celery satisfies this:
  `app/celery_app.py` wraps every task in a `ContextTask` that runs the body
  inside `with app.app_context()`, so notification and e-mail tasks are fine.
* **No application context at all** (a bare script, an `import`-time call):
  `external_url()` raises `RuntimeError: Working outside of application
  context`. `get_base_url()` does *not* — it degrades to the `APP_BASE_URL`
  environment variable and finally to the development default, which is why
  callers that must survive that case (e.g. the set-password link builder in
  `student_bulk_service`) fall back to `get_base_url()` plus a literal path.
"""

import os
from urllib.parse import urlsplit

from flask import current_app

from app.config import DEVELOPMENT_BASE_URL

# Development-only fallback. In production APP_BASE_URL must be set explicitly
# or the process refuses to boot (app/config.py::_require_production_base_url).
DEFAULT_BASE_URL = DEVELOPMENT_BASE_URL


def get_base_url() -> str:
    """Return the configured public base URL, normalised without trailing slash."""
    base = ''
    try:
        base = current_app.config.get('APP_BASE_URL') or ''
    except RuntimeError:
        # No application context (stand-alone scripts, shell helpers).
        base = ''
    if not base:
        base = os.environ.get('APP_BASE_URL') or DEFAULT_BASE_URL
    return base.rstrip('/')


def external_url(endpoint: str, **values) -> str:
    """
    Build an absolute URL for a Flask endpoint, pinned to APP_BASE_URL.

    Drop-in replacement for `url_for(endpoint, **values, _external=True)`.
    Requires an application context (the URL map lives on the app), but not a
    request context.
    """
    base = get_base_url()
    parts = urlsplit(base)
    scheme = parts.scheme or 'http'
    # Tolerate a bare "example.org" without scheme: urlsplit puts it in .path.
    netloc = parts.netloc or parts.path
    sub_path = parts.path if parts.netloc else ''

    values = dict(values)
    current_app.inject_url_defaults(endpoint, values)
    adapter = current_app.url_map.bind(
        netloc,
        script_name=(sub_path.rstrip('/') + '/'),
        url_scheme=scheme,
    )
    return adapter.build(endpoint, values, force_external=True)
