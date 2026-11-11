import sys
from os import environ
from pathlib import Path

from django.core.management.utils import get_random_secret_key
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

######################################################################
# General
######################################################################
BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = environ.get("SECRET_KEY", get_random_secret_key())

DEBUG = environ.get("DEBUG", "") == "1"

ALLOWED_HOSTS = [
    host.strip()
    for host in environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,api").split(",")
    if host.strip()
]

# Behind the compose/ingress proxy, trust the forwarded scheme so Django knows
# the request was HTTPS and does not build http:// redirects.
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

WSGI_APPLICATION = "api.wsgi.application"

ROOT_URLCONF = "api.urls"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

######################################################################
# Apps
######################################################################
INSTALLED_APPS = [
    "unfold",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "corsheaders",
    "django_celery_results",
    "api",
    "rag",
]

######################################################################
# Middleware
######################################################################
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

######################################################################
# Templates
######################################################################
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

######################################################################
# Database
######################################################################
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "USER": environ.get("DATABASE_USER", "postgres"),
        "PASSWORD": environ.get("DATABASE_PASSWORD", "change-password"),
        "NAME": environ.get("DATABASE_NAME", "db"),
        "HOST": environ.get("DATABASE_HOST", "db"),
        "PORT": environ.get("DATABASE_PORT", "5432"),
        "TEST": {
            "NAME": "test",
        },
    }
}

######################################################################
# Authentication
######################################################################
AUTH_USER_MODEL = "api.User"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

######################################################################
# Internationalization
######################################################################
LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

######################################################################
# Staticfiles
######################################################################
STATIC_URL = "static/"

# Required by `collectstatic`, which the production image runs at build time so
# the admin/DRF/Swagger assets are baked in. Unused under `runserver`, which
# serves each app's static/ directory directly.
STATIC_ROOT = BASE_DIR / "staticfiles"

######################################################################
# Rest Framework
######################################################################
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 10,
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    # Every RAG call costs money (embeddings, LLM tokens) — rate limit by default.
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": environ.get("THROTTLE_USER", "1000/hour"),
        "anon": environ.get("THROTTLE_ANON", "60/hour"),
        "chat": environ.get("THROTTLE_CHAT", "120/hour"),
        "documents": environ.get("THROTTLE_DOCUMENTS", "1000/hour"),
        "audit": environ.get("THROTTLE_AUDIT", "60/hour"),
        "evaluation": environ.get("THROTTLE_EVALUATION", "30/hour"),
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "RAG_Kit API",
    "DESCRIPTION": "Retrieval-augmented generation over your documents.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

######################################################################
# JWT
######################################################################
from datetime import timedelta  # noqa: E402

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(environ.get("JWT_ACCESS_MINUTES", "30"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(environ.get("JWT_REFRESH_DAYS", "7"))),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
}

######################################################################
# CORS
######################################################################
# Explicit origins only. A credentialed API must never use a wildcard.
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in environ.get("CSRF_TRUSTED_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]

######################################################################
# Uploads
######################################################################
# Stream uploads to disk beyond 5 MB instead of buffering them in memory.
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000

######################################################################
# Celery
######################################################################
CELERY_BROKER_URL = environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = environ.get("CELERY_RESULT_BACKEND", "django-db")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_TASK_TRACK_STARTED = True
# Ingestion of a large PDF with summaries can legitimately take minutes.
CELERY_TASK_TIME_LIMIT = int(environ.get("CELERY_TASK_TIME_LIMIT", "1800"))
CELERY_TASK_SOFT_TIME_LIMIT = int(environ.get("CELERY_TASK_SOFT_TIME_LIMIT", "1500"))
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = 50
CELERY_BEAT_SCHEDULE = {
    "reap-stuck-documents": {
        "task": "rag.reap_stuck_documents",
        "schedule": 600.0,
    },
}

######################################################################
# Cache
######################################################################
# Under pytest, use an in-process cache so the suite needs no Redis. DRF's
# throttling stores counters in the cache, so a missing Redis would otherwise
# fail every authenticated request.
if "PYTEST_CURRENT_TEST" in environ or "pytest" in sys.argv[0]:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": environ.get("REDIS_URL", "redis://redis:6379/1"),
        }
    }

######################################################################
# Production security
######################################################################
# These are no-ops under DEBUG so local http development keeps working.
if not DEBUG:
    # Off under pytest: the test client speaks plain HTTP, and a blanket
    # redirect would turn every API assertion into a 301.
    _TESTING = "PYTEST_CURRENT_TEST" in environ or "pytest" in sys.argv[0]
    SECURE_SSL_REDIRECT = not _TESTING and environ.get("SECURE_SSL_REDIRECT", "1") == "1"
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SECURE_HSTS_SECONDS = int(environ.get("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"

######################################################################
# Logging
######################################################################
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": environ.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "rag": {
            "handlers": ["console"],
            "level": environ.get("RAG_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        # These are extremely chatty at INFO.
        "httpx": {"level": "WARNING"},
        "urllib3": {"level": "WARNING"},
    },
}

######################################################################
# Unfold
######################################################################
UNFOLD = {
    "SITE_HEADER": _("RAG_Kit Admin"),
    "SITE_TITLE": _("RAG_Kit Admin"),
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": _("Knowledge Base"),
                "separator": False,
                "items": [
                    {
                        "title": _("Documents"),
                        "icon": "description",
                        "link": reverse_lazy("admin:rag_document_changelist"),
                    },
                    {
                        "title": _("Chunks"),
                        "icon": "dataset",
                        "link": reverse_lazy("admin:rag_chunk_changelist"),
                    },
                    {
                        "title": _("Conversations"),
                        "icon": "forum",
                        "link": reverse_lazy("admin:rag_conversation_changelist"),
                    },
                    {
                        "title": _("Messages"),
                        "icon": "chat",
                        "link": reverse_lazy("admin:rag_message_changelist"),
                    },
                ],
            },
            {
                "title": _("Quality & Evaluation"),
                "separator": False,
                "items": [
                    {
                        "title": _("Ingestion Reports"),
                        "icon": "analytics",
                        "link": reverse_lazy("admin:rag_ingestionreport_changelist"),
                    },
                    {
                        "title": _("Evaluation Reports"),
                        "icon": "grade",
                        "link": reverse_lazy("admin:rag_evaluationreport_changelist"),
                    },
                ],
            },
            {
                "title": _("Monitoring"),
                "separator": False,
                "items": [
                    {
                        "title": _("Audit Logs"),
                        "icon": "history",
                        "link": reverse_lazy("admin:rag_auditlog_changelist"),
                    },
                ],
            },
            {
                "title": _("Access"),
                "separator": True,
                "items": [
                    {
                        "title": _("Users"),
                        "icon": "person",
                        "link": reverse_lazy("admin:api_user_changelist"),
                    },
                    {
                        "title": _("Groups"),
                        "icon": "label",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                    },
                ],
            },
        ],
    },
}
