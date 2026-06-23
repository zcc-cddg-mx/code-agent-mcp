"""X-Agent-Token authentication for all endpoints.

Set TOKEN_AZURE env var to the shared secret. Missing or wrong token → 401.
"""

from __future__ import annotations

import os
from functools import wraps

from flask import jsonify, request

_TOKEN_AZURE = os.environ.get("TOKEN_AZURE", "")


def require_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("X-Agent-Token", "")
        if not _TOKEN_AZURE:
            return jsonify({"error": "TOKEN_AZURE not configured on server"}), 500
        if token != _TOKEN_AZURE:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated
