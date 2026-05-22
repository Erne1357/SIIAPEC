import os, json, threading, tempfile
from contextlib import contextmanager
from pathlib import Path
import msal, requests
from flask import current_app

try:
    import fcntl  # POSIX (producción Linux)
except ImportError:  # Windows (dev local) — sin concurrencia multiproceso real
    fcntl = None

def get_config():
    """Obtiene configuración desde Flask config"""
    try:
        from flask import current_app
        return {
            'TENANT_ID': current_app.config.get('MS_TENANT_ID', ''),
            'CLIENT_ID': current_app.config.get('MS_CLIENT_ID', ''),
            'CLIENT_SECRET': current_app.config.get('MS_CLIENT_SECRET', ''),
            'REDIRECT_URI': current_app.config.get('MS_REDIRECT_URI', 'http://localhost/admin/emails/callback'),
            'CACHE_PATH': current_app.config.get('MAIL_CACHE_PATH', 'instance/mail/msal_cache.json'),
            'ACCT_PATH': current_app.config.get('MAIL_ACCOUNT_PATH', 'instance/mail/msal_account.json')
        }
    except RuntimeError:
        # Fuera del contexto de app, usar variables de entorno
        return {
            'TENANT_ID': os.getenv('MS_TENANT_ID', ''),
            'CLIENT_ID': os.getenv('MS_CLIENT_ID', ''),
            'CLIENT_SECRET': os.getenv('MS_CLIENT_SECRET', ''),
            'REDIRECT_URI': os.getenv('MS_REDIRECT_URI', 'http://localhost/admin/emails/callback'),
            'CACHE_PATH': os.getenv('MAIL_CACHE_PATH', 'instance/mail/msal_cache.json'),
            'ACCT_PATH': os.getenv('MAIL_ACCOUNT_PATH', 'instance/mail/msal_account.json')
        }

# Scopes necesarios para enviar correos (formato completo de Microsoft Graph)
SCOPES = ["https://graph.microsoft.com/Mail.Send"]

# Lock in-process para el archivo de cuenta (escritura única en el callback,
# poca contención).
_ACCOUNT_LOCK = threading.Lock()
# Fallback in-process del cache cuando no hay fcntl (dev Windows, sin
# concurrencia multiproceso real).
_INPROC_CACHE_LOCK = threading.Lock()

def _ensure_dirs():
    cfg = get_config()
    Path(cfg['CACHE_PATH']).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg['ACCT_PATH']).parent.mkdir(parents=True, exist_ok=True)

def _atomic_write_text(path: str, text: str):
    """
    Escritura atómica: archivo temporal + os.replace.
    Garantiza que cualquier lector vea SIEMPRE un archivo completo (el viejo
    o el nuevo), nunca uno truncado a medio escribir.
    """
    d = os.path.dirname(path) or '.'
    fd, tmp = tempfile.mkstemp(prefix='.tmp_', dir=d)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

@contextmanager
def _cache_file_lock():
    """
    Lock exclusivo CROSS-PROCESS sobre el cache MSAL.

    Antes se usaba threading.Lock(), que NO sincroniza entre procesos: web
    (gunicorn) y celery-worker (--concurrency>=2) escribían msal_cache.json
    a la vez, corrompiéndolo y perdiendo la rotación del refresh token →
    "No hay token". fcntl.flock sí serializa entre procesos en Linux.
    """
    cfg = get_config()
    _ensure_dirs()
    if fcntl is None:
        with _INPROC_CACHE_LOCK:
            yield
        return
    lock_path = cfg['CACHE_PATH'] + '.lock'
    f = open(lock_path, 'a+')
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()

@contextmanager
def cache_transaction():
    """
    Sección crítica única: carga el cache, lo entrega al caller, y si MSAL lo
    modificó (rotó el refresh token) lo persiste atómicamente — todo bajo el
    mismo lock cross-process. Evita el lost-update entre load() y save().

    No anidar: load_cache()/save_cache() NO toman el lock para no provocar
    deadlock de fcntl (dos open() en el mismo proceso = dos locks distintos).
    """
    with _cache_file_lock():
        cfg = get_config()
        cache = msal.SerializableTokenCache()
        if os.path.exists(cfg['CACHE_PATH']):
            with open(cfg['CACHE_PATH'], 'r', encoding='utf-8') as fh:
                cache.deserialize(fh.read())
        yield cache
        if cache.has_state_changed:
            _atomic_write_text(cfg['CACHE_PATH'], cache.serialize())

def load_cache() -> msal.SerializableTokenCache:
    cfg = get_config()
    _ensure_dirs()
    cache = msal.SerializableTokenCache()
    if os.path.exists(cfg['CACHE_PATH']):
        # Lectura sin lock segura: todas las escrituras son atómicas (os.replace),
        # el lector nunca ve un archivo parcial.
        with open(cfg['CACHE_PATH'], "r", encoding="utf-8") as f:
            cache.deserialize(f.read())
    return cache

def save_cache(cache: msal.SerializableTokenCache):
    cfg = get_config()
    if cache.has_state_changed:
        with _cache_file_lock():
            _atomic_write_text(cfg['CACHE_PATH'], cache.serialize())

def is_configured() -> bool:
    """Devuelve True solo si las credenciales de Microsoft Graph están definidas."""
    cfg = get_config()
    return bool(cfg['TENANT_ID'] and cfg['CLIENT_ID'] and cfg['CLIENT_SECRET'])


def get_msal_app(cache=None) -> msal.ConfidentialClientApplication:
    cfg = get_config()
    cache = cache or load_cache()
    authority = f"https://login.microsoftonline.com/{cfg['TENANT_ID']}"
    return msal.ConfidentialClientApplication(
        cfg['CLIENT_ID'],
        authority=authority,
        client_credential=cfg['CLIENT_SECRET'],
        token_cache=cache
    )

def save_account_info(account: dict):
    cfg = get_config()
    _ensure_dirs()
    with _ACCOUNT_LOCK:
        _atomic_write_text(cfg['ACCT_PATH'], json.dumps({
            "home_account_id": account.get("home_account_id"),
            "username": account.get("username"),
            "name": account.get("name")
        }))

def read_account_info() -> dict | None:
    cfg = get_config()
    if not os.path.exists(cfg['ACCT_PATH']):
        return None
    with _ACCOUNT_LOCK, open(cfg['ACCT_PATH'], "r", encoding="utf-8") as f:
        return json.load(f)

def clear_account_and_cache():
    cfg = get_config()
    with _cache_file_lock():
        with _ACCOUNT_LOCK:
            if os.path.exists(cfg['CACHE_PATH']):
                os.remove(cfg['CACHE_PATH'])
            if os.path.exists(cfg['ACCT_PATH']):
                os.remove(cfg['ACCT_PATH'])

def build_auth_url(state: str = "email_config"):
    if not is_configured():
        raise RuntimeError("Las credenciales de Microsoft Graph no están configuradas.")
    cfg = get_config()
    app = get_msal_app()
    return app.get_authorization_request_url(
        SCOPES,
        redirect_uri=cfg['REDIRECT_URI'],
        state=state,
        prompt="select_account",
    )

def process_auth_code(code: str) -> dict:
    """
    Intercambia el code por tokens y persiste en cache + archivo de cuenta.
    Retorna dict con info básica de usuario (name, username).
    """
    cfg = get_config()
    with cache_transaction() as cache:
        app = get_msal_app(cache)
        result = app.acquire_token_by_authorization_code(
            code,
            scopes=SCOPES,
            redirect_uri=cfg['REDIRECT_URI']
        )
        if "access_token" not in result:
            return {
                "error": result.get("error"),
                "error_description": result.get("error_description")
            }

        # Selecciona la cuenta
        accounts = app.get_accounts()
        if accounts:
            save_account_info({
                "home_account_id": accounts[0].get("home_account_id"),
                "username": accounts[0].get("username"),
                "name": result.get("id_token_claims", {}).get("name")
            })
        idc = result.get("id_token_claims", {})

    return {
        "name": idc.get("name"),
        "username": idc.get("preferred_username")
    }

def acquire_token_silent() -> str | None:
    """
    Intenta renovar un access token usando el refresh token del cache.
    Retorna el token o None si no hay sesión activa o sin credenciales.
    """
    if not is_configured():
        return None
    acct = read_account_info()
    if not acct:
        return None

    with cache_transaction() as cache:
        app = get_msal_app(cache)
        # Busca la cuenta en el cache
        account = None
        for a in app.get_accounts():
            if a.get("home_account_id") == acct.get("home_account_id"):
                account = a
                break
        if not account:
            return None

        result = app.acquire_token_silent(SCOPES, account=account)

    if not result or "access_token" not in result:
        return None
    return result["access_token"]

def graph_send_mail(access_token: str, subject: str, content_html: str,
                   to_list: list[str], save_to_sent=True):
    """
    Envío delegado: usa /me/sendMail (envía como el usuario que inició sesión).
    """
    endpoint = "https://graph.microsoft.com/v1.0/me/sendMail"
    payload = {
        "message": {
            "subject": subject,
            "body": { "contentType": "HTML", "content": content_html },
            "toRecipients": [{"emailAddress": {"address": a}} for a in to_list]
        },
        "saveToSentItems": bool(save_to_sent)
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    resp = requests.post(endpoint, headers=headers, json=payload, timeout=30)
    return resp

def is_connected() -> bool:
    """
    Verifica si hay una sesión activa de Microsoft.
    Devuelve False si las credenciales no están configuradas o si hay un
    error de red (p. ej. sin acceso a internet en dev).
    """
    if not is_configured():
        return False
    cfg = get_config()
    if not os.path.exists(cfg['ACCT_PATH']):
        return False
    try:
        token = acquire_token_silent()
        return token is not None
    except Exception:
        return False
