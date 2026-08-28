"""Helpers for runtime data that must survive Render deploys.

Production on Render mounts a persistent disk at /var/data.  Local development
continues to use files beside the source tree unless an explicit environment
variable is supplied.
"""
import os
import shutil
from pathlib import Path


RENDER_DATA_ROOT = Path("/var/data")


def resolve_runtime_db(filename, env_var, local_path):
    """Resolve a SQLite database path and opportunistically migrate local data.

    Precedence:
      1. The database-specific environment variable (for example
         PORTAL_AUTH_DB_PATH).
      2. LOGICORE_DATA_DIR/<filename> when LOGICORE_DATA_DIR is configured.
      3. /var/data/<filename> when the Render persistent mount exists.
      4. The historical source-adjacent local path for local development.

    If the selected persistent target does not exist but the historical local
    database still does, copy it once before the application opens SQLite.
    Existing persistent databases are never overwritten.
    """
    local_path = Path(local_path)
    explicit = (os.environ.get(env_var) or "").strip()
    shared_root = (os.environ.get("LOGICORE_DATA_DIR") or "").strip()

    if explicit:
        target = Path(explicit).expanduser()
    elif shared_root:
        target = Path(shared_root).expanduser() / filename
    elif RENDER_DATA_ROOT.is_dir():
        target = RENDER_DATA_ROOT / filename
    else:
        target = local_path

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Preserve local-development behavior if a configured/mounted path is
        # unexpectedly unavailable rather than preventing the app from booting.
        target = local_path
        target.parent.mkdir(parents=True, exist_ok=True)

    if target != local_path and not target.exists() and local_path.exists():
        try:
            shutil.copy2(local_path, target)
        except OSError:
            # If migration cannot be performed, let SQLite surface a useful
            # error when the selected target is opened rather than silently
            # overwriting or deleting either copy.
            pass

    return target
