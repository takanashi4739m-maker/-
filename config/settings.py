"""
Django settings for config project.

環境変数で開発（SQLite / DEBUG=True）と本番（PostgreSQL / DEBUG=False / HTTPS）を
切り替える。環境変数が無ければ従来通り開発用のデフォルトで動作する。

主な環境変数:
- DJANGO_SECRET_KEY        本番の秘密鍵（未設定時は開発用フォールバック）
- DJANGO_DEBUG             "True"/"False"（既定 False）
- DJANGO_ALLOWED_HOSTS     カンマ区切り（既定 localhost,127.0.0.1）
- DJANGO_CSRF_TRUSTED_ORIGINS  カンマ区切り（例: https://<app>.onrender.com）
- DATABASE_URL             設定時 PostgreSQL、未設定時 SQLite にフォールバック
- BASE_URL                 show_urls コマンドでフルURL表示に使う（任意）

For the full list of settings and their values, see
https://docs.djangoproject.com/en/6.0/ref/settings/
"""

import os
from pathlib import Path

import dj_database_url

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# --- .env の読み込み（存在すれば。ローカル開発用。本番はプラットフォームの環境変数を使う） ---
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    # python-dotenv が無くても環境変数が直接設定されていれば動作する。
    pass


def env_bool(name: str, default: bool = False) -> bool:
    """環境変数を真偽値として解釈する。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    """カンマ区切りの環境変数をリストに変換（空要素は除去）。"""
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
# 本番では DJANGO_SECRET_KEY を必ず設定する。未設定時は開発用フォールバック。
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-8qddvn45*)0b77d#tcg-ikyk4lj3#^%+k9l*6&&25vn024t!jo",
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env_bool("DJANGO_DEBUG", default=False)

# 本番では Render のホスト名等をカンマ区切りで指定する。既定はローカル開発用。
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

# トークンURLへの HTTPS POST（売上・経費・在庫記録）を通すため、
# 本番の Origin を明示的に信頼する（例: https://<app>.onrender.com）。
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

# Render は本番のホスト名を RENDER_EXTERNAL_HOSTNAME 環境変数に自動注入する。
# 手動設定（DJANGO_ALLOWED_HOSTS 等）に頼らず、これを許可ホスト/信頼Originへ自動反映する。
_render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if _render_host:
    if _render_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_render_host)
    _render_origin = f"https://{_render_host}"
    if _render_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_render_origin)

# 本番では onrender.com のサブドメインを広く許可する（設定漏れの保険）。
if not DEBUG:
    if ".onrender.com" not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(".onrender.com")
    if "https://*.onrender.com" not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append("https://*.onrender.com")


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # WhiteNoise は SecurityMiddleware の直後に置く（静的ファイルを本番で配信）。
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases
# DATABASE_URL があれば PostgreSQL、無ければ開発用 SQLite にフォールバック。
DATABASES = {
    'default': dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
        conn_health_checks=True,
    )
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'ja'

TIME_ZONE = 'Asia/Tokyo'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'
# collectstatic の出力先（本番で WhiteNoise が配信する）。
STATIC_ROOT = BASE_DIR / 'staticfiles'

# WhiteNoise の圧縮 + マニフェスト方式（キャッシュ最適・ハッシュ付きファイル名）。
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}


# Default primary key field type
# https://docs.djangoproject.com/en/6.0/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# --- 本番セキュリティ設定（DEBUG=False のときだけ有効化。ローカル開発では無効） ---
if not DEBUG:
    # Render 等のリバースプロキシが付ける X-Forwarded-Proto を信頼して HTTPS 判定する。
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    # HTTP アクセスを HTTPS にリダイレクト。
    SECURE_SSL_REDIRECT = True
    # Cookie は HTTPS のみで送信。
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # HSTS（まず短めの期間から。安定後に延長可）。
    SECURE_HSTS_SECONDS = 31536000  # 1年
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    # ブラウザのコンテンツタイプ推測を無効化。
    SECURE_CONTENT_TYPE_NOSNIFF = True
