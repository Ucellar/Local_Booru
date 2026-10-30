"""Web interface of Local Booru: the gallery in the classic booru layout (and later manga and tools).

``WebUI`` is a small HTTP server on 127.0.0.1.  The desktop program starts it and shows its pages
in its window; ``python -m webui`` runs it alone and opens the gallery in the usual browser.
"""
from webui.server import WebUI, free_port

__all__ = ["WebUI", "free_port"]
