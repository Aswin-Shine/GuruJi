"""Put `backend/` on sys.path so operator scripts can `import app`.

Every script in this directory is run directly (`python scripts/foo.py`), which
makes `sys.path[0]` the *scripts* directory, not `backend/`. Without this,
`from app.config import ...` raises ModuleNotFoundError — which is exactly what
`scripts/delete_user.py` did before this file existed, despite being the
documented DPDP deletion path.

Import this first, before any `app.*` import:

    import _bootstrap  # noqa: F401

The alternative (`python -m scripts.foo`, or a packaging step) would work too,
but this keeps the documented command line identical to what people already type
and costs one line per script.
"""
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
