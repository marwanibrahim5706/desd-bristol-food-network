from pathlib import Path
import os
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR.parent / ".env"

load_dotenv(ENV_FILE)


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")



INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    "rest_framework",
    "accounts",  
    "market_accounts",
    "market_products",
    "market_cart",
    "market_orders",
    "market_payments",
    "market_finance",
    "market_alerts",
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
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
        'DIRS': [BASE_DIR / "templates"],
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


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME") or os.getenv("POSTGRES_DB"),
        "USER": os.getenv("DB_USER") or os.getenv("POSTGRES_USER"),
        "PASSWORD": os.getenv("DB_PASSWORD") or os.getenv("POSTGRES_PASSWORD"),
        "HOST": (
            os.getenv("DB_HOST")
            or os.getenv("POSTGRES_HOST")
            or ("db" if Path("/.dockerenv").exists() else "localhost")
        ),
        "PORT": os.getenv("DB_PORT") or os.getenv("POSTGRES_PORT", "5432"),
    }
}



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


LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


STATIC_URL = 'static/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = "market_accounts.User"
TEST_RUNNER = "config.test_runner.AppLabelTestRunner"

LOGIN_URL = "/accounts/login/"
CSRF_FAILURE_VIEW = "accounts.views.csrf_failure"

# Internal URL for the dedicated payments microservice container.
PAYMENTS_SERVICE_URL = os.getenv("PAYMENTS_SERVICE_URL", "http://payments:8001")

# Browser-facing URL used when redirecting customers to the payments page.
PAYMENTS_BROWSER_URL = os.getenv("PAYMENTS_BROWSER_URL", "http://localhost:8001")

# Optional OpenWeather integration for product pages. Missing or invalid values
# simply hide the weather card instead of affecting the main marketplace flow.
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY") or os.getenv("OPENWEATHER_API_KEY", "")
WEATHER_LOCATION = os.getenv("WEATHER_LOCATION", "Bristol,UK")
