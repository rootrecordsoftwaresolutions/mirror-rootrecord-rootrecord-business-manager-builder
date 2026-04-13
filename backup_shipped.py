"""Packaged-build default URL for optional online backup (HTTPS origin only, no path suffix).

Empty string means “use other internal resolution.” Environment overrides always win.
See repository docs for operators who deploy online backup.
"""

from __future__ import annotations

SHIPPED_BACKUP_API_BASE_URL = "https://rootrecord-desktop-backup.wildecho94.workers.dev"
