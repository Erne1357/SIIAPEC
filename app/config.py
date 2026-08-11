import os
import sys
from pathlib import Path

# Canonical public host of the deployment. The edge nginx answers on this name;
# `siiapec.cdjuarez.tecnm.mx` only 301-redirects here, so never use it as base.
CANONICAL_PRODUCTION_BASE_URL = 'https://siiap.cdjuarez.tecnm.mx'

# Development-only base. Any outbound link built on top of it is unusable for
# the recipient of an e-mail, which is why production must set APP_BASE_URL.
DEVELOPMENT_BASE_URL = 'http://localhost'


class MissingProductionConfigError(RuntimeError):
    """A mandatory production setting is absent from the environment."""


def _running_under_pytest() -> bool:
    """True while the process is a pytest run (collection included)."""
    return 'pytest' in sys.modules or 'PYTEST_CURRENT_TEST' in os.environ


def _require_production_base_url() -> None:
    """
    Refuse to boot a production process without an explicit APP_BASE_URL.

    Every absolute link that leaves the system (e-mail, notification, the
    7-day set-password token link) is built from APP_BASE_URL and never from
    the incoming request. If the variable is missing the fallback is
    `http://localhost`, so the whole outbound mail stream silently ships dead
    links: an outage discovered by the recipients, days later, one token at a
    time. A container that refuses to start is strictly cheaper.
    """
    if _running_under_pytest():
        return
    if os.environ.get('FLASK_ENV', 'development').strip().lower() != 'production':
        return
    if (os.environ.get('APP_BASE_URL') or '').strip():
        return

    raise MissingProductionConfigError(
        "Falta la variable de entorno APP_BASE_URL y FLASK_ENV=production.\n"
        "\n"
        "APP_BASE_URL es la base de TODO enlace absoluto que sale del sistema\n"
        "(correos, notificaciones, enlace de establecer contrasena con token de\n"
        "7 dias). Sin ella la aplicacion usaria "
        f"'{DEVELOPMENT_BASE_URL}' y todos esos\n"
        "enlaces llegarian rotos al destinatario.\n"
        "\n"
        "Anade esta linea al archivo de entorno del despliegue\n"
        "(docker/.env.prod, el mismo que carga docker-compose.prod.yml):\n"
        "\n"
        f"    APP_BASE_URL={CANONICAL_PRODUCTION_BASE_URL}\n"
        "\n"
        "Usa el host canonico: siiapec.cdjuarez.tecnm.mx recibe un 301 del nginx\n"
        "de borde y cada enlace pagaria un salto de mas."
    )


_require_production_base_url()


class Config:
    # Versión estática (actualízala cuando cambies CSS/JS)
    STATIC_VERSION = '1.0.42081101'
    
    # Directorios base
    BASE_DIR = Path(__file__).resolve().parent.parent
    INSTANCE_DIR = BASE_DIR / 'instance'
    STATIC_FOLDER = BASE_DIR / 'app' / 'static'
    
    # Uploads - puede ser sobrescrito por variable de entorno
    UPLOAD_FOLDER = Path(os.environ.get('UPLOAD_FOLDER', str(INSTANCE_DIR / 'uploads')))
    TEMPLATE_STORE = INSTANCE_DIR / 'templates_sys'

    # Directorio para tokens de correo
    MAIL_DIR = INSTANCE_DIR / 'mail'
    MAIL_CACHE_PATH = str(MAIL_DIR / 'msal_cache.json')
    MAIL_ACCOUNT_PATH = str(MAIL_DIR / 'msal_account.json')

    # Sub-rutas útiles
    AVATAR_FOLDER = UPLOAD_FOLDER / 'avatars'
    USER_DOCS_FOLDER = UPLOAD_FOLDER / 'documents'
    EVENTS_FOLDER = UPLOAD_FOLDER / 'events'

    # Límites y tipos permitidos
    ALLOWED_DOC_EXT = {'pdf', 'doc', 'docx'}
    ALLOWED_IMAGE_EXT = {'jpg', 'jpeg', 'png', 'webp'}
    MAX_EVENT_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5 MB (antes 3 MB, subido por imágenes de eventos)

    # ===== SEGURIDAD =====
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-key-CHANGE-IN-PRODUCTION')
    
    # ===== BASE DE DATOS =====
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'postgresql://postgres:password@db:5432/SIIAP'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ===== MICROSOFT GRAPH (CORREOS) =====
    MS_TENANT_ID = os.environ.get('MS_TENANT_ID', '')
    MS_CLIENT_ID = os.environ.get('MS_CLIENT_ID', '')
    MS_CLIENT_SECRET = os.environ.get('MS_CLIENT_SECRET', '')
    MS_REDIRECT_URI = os.environ.get('MS_REDIRECT_URI', 'http://localhost/admin/emails/callback')
    
    # ===== SESIONES Y COOKIES =====
    SESSION_COOKIE_NAME = "siiap_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
    
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
    
    # ===== ENTORNO =====
    FLASK_ENV = os.environ.get('FLASK_ENV', 'development')
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'

    # ===== PROXY REVERSO (para HTTPS en producción) =====
    PREFERRED_URL_SCHEME = os.environ.get('PREFERRED_URL_SCHEME', 'http')

    # ===== URL PÚBLICA DE LA APLICACIÓN =====
    # Base de TODO enlace absoluto que sale del sistema (correos,
    # notificaciones, enlaces de establecer contraseña con token de 7 días).
    #
    # Debe fijarse por entorno en producción, con el host CANÓNICO:
    #   APP_BASE_URL=https://siiap.cdjuarez.tecnm.mx
    #
    # No usar siiapec.cdjuarez.tecnm.mx: el nginx de borde lo redirige con 301
    # al canónico, así que cada enlace de correo pagaría un salto de más.
    #
    # Con FLASK_ENV=production y sin APP_BASE_URL el proceso NO arranca: lo
    # impide _require_production_base_url() arriba en este mismo archivo. El
    # valor de abajo es solo el de desarrollo. No se vuelve a construir la URL
    # desde la petición en curso (ese era justamente el defecto: el Host lo
    # elige quien hace la petición), así que un valor incorrecto se manifiesta
    # como enlaces rotos, nunca como enlaces al dominio de un atacante.
    #
    # Consumido por app/utils/urls.py::external_url().
    APP_BASE_URL = os.environ.get('APP_BASE_URL', DEVELOPMENT_BASE_URL).rstrip('/')
    
    # Uno, no cuatro. eventlet exige exactamente un worker por proceso, y todos
    # los compose arrancan con `--workers 1` sin leer esta clave. Además dos
    # mecanismos dependen de que el proceso sea único: la expulsión de sockets
    # al desactivar una cuenta (app/sockets/emitters.py) y el tope por proceso
    # del registro de autenticación. Dejarlo en 4 era una trampa: quien lo
    # cableara algún día rompería ambos sin enterarse.
    GUNICORN_WORKERS = int(os.environ.get('GUNICORN_WORKERS', '1'))
    GUNICORN_TIMEOUT = int(os.environ.get('GUNICORN_TIMEOUT', '120'))

    # ===== REDIS =====
    REDIS_URL = os.environ.get('REDIS_URL', 'redis://redis:6379/0')

    # ===== CELERY =====
    # DB 1 para el broker, DB 2 para los resultados
    CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://redis:6379/1')
    CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://redis:6379/2')
    CELERY_TIMEZONE = 'America/Ciudad_Juarez'
    CELERY_TASK_SERIALIZER = 'json'
    CELERY_RESULT_SERIALIZER = 'json'
    CELERY_ACCEPT_CONTENT = ['json']
    CELERY_TASK_TRACK_STARTED = True
    CELERY_TASK_TIME_LIMIT = 300       # 5 min máximo por tarea
    CELERY_TASK_SOFT_TIME_LIMIT = 240  # aviso a los 4 min