"""Backward-compatible entrypoint.

The server lives in the ``muse_bridge`` package. This module stays so existing
process managers (``python server.py``, ``run.sh``) keep working.

* factory: ``muse_bridge.app:create_app``
* module app: ``muse_bridge.app:app`` (also re-exported here as ``app``)
"""

from muse_bridge.app import app, create_app, main

__all__ = ["app", "create_app", "main"]

if __name__ == "__main__":
    main()
