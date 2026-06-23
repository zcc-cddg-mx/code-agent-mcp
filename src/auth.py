"""X-Agent-Token authentication for all endpoints.

Set AGENT_TOKEN env var to the shared secret. Missing or wrong token → 401.
"""

from __future__ import annotations

import os
from functools import wraps

from flask import jsonify, request

_AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")


def require_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("X-Agent-Token", "")
        if not _AGENT_TOKEN:
            return jsonify({"error": "AGENT_TOKEN not configured on server"}), 500
        if token != _AGENT_TOKEN:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated
