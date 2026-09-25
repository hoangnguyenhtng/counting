"""Small access-control helpers for protected operator functions."""

import hmac
import os


DEFAULT_TEACHING_PASSWORD = "1"
TEACHING_PASSWORD_ENV = "VPVA_TEACHING_PASSWORD"


def teaching_password_is_valid(candidate: str) -> bool:
    """Validate the Teaching password, allowing a deployment-time override."""
    configured_password = os.environ.get(
        TEACHING_PASSWORD_ENV,
        DEFAULT_TEACHING_PASSWORD,
    )
    return hmac.compare_digest(str(candidate), configured_password)
