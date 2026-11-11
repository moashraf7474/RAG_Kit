"""Health check.

Reports the reachability of every dependency so a broken stack is diagnosable
from one curl instead of six. Deliberately unauthenticated but detail-free:
it exposes component names and up/down, never versions or connection strings.
"""

from __future__ import annotations

import logging

from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

logger = logging.getLogger(__name__)


def _check(name: str, probe) -> tuple[str, bool]:
    try:
        probe()
        return name, True
    except Exception:
        logger.warning("Health check '%s' failed.", name, exc_info=True)
        return name, False


@extend_schema(
    responses={200: None, 503: None},
    description="Liveness and dependency readiness.",
    auth=[],
)
@api_view(["GET"])
@permission_classes([AllowAny])
# Explicitly unthrottled. Docker/Kubernetes probe this every 15s (~240/hour),
# which the default anon limit (60/hour) would reject with 429 — marking a
# perfectly healthy container unhealthy and blocking everything that waits on
# it. The endpoint is cheap and leaks nothing, so it is safe to leave open.
@throttle_classes([])
def health(request):
    checks: dict[str, bool] = {}

    def database():
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")

    def cache():
        from django.core.cache import cache as django_cache

        django_cache.set("healthcheck", "1", 10)

    def qdrant():
        from rag.vectordb import get_client

        get_client().get_collections()

    def objectstore():
        from rag.conf import get_config
        from rag.storage import get_s3_client

        get_s3_client().head_bucket(Bucket=get_config().s3.bucket)

    for name, probe in (
        ("database", database),
        ("cache", cache),
        ("vector_db", qdrant),
        ("object_storage", objectstore),
    ):
        key, ok = _check(name, probe)
        checks[key] = ok

    healthy = all(checks.values())
    return Response(
        {"status": "ok" if healthy else "degraded", "checks": checks},
        status=200 if healthy else 503,
    )
