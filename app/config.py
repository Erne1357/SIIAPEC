import os
from pathlib import Path

class Config:
    # Versión estática (actualízala cuando cambies CSS/JS)
    STATIC_VERSION = '1.0.42080305'
    
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
    
    GUNICORN_WORKERS = int(os.environ.get('GUNICORN_WORKERS', '4'))
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