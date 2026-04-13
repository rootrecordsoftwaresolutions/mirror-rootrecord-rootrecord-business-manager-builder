"""Default RootRecord online account service URL for installed (frozen) builds.

Used when the app is packaged and no per-machine override is set. Update to match
your production HTTPS origin for sign-in and subscription checks.

Operators may still override via environment variables documented for development builds.
"""

from __future__ import annotations

# Default shipped license server origin (HTTPS only in production).
SHIPPED_LICENSE_API_BASE_URL = "https://rootrecord-license.wildecho94.workers.dev"
