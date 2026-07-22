"""Pytest settings module — real Postgres, fast auth/cache for CI.

Inherits everything from admin.settings (Postgres DATABASES, USE_TZ, secrets
from env, ...) and only swaps the two knobs that make tests slow without
changing test-observable behavior.
"""

from admin.settings import *

# Fast hashing — tests don't need PBKDF2's ~600k iterations.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# In-memory cache — no Postgres round-trips, no createcachetable needed.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
