"""feicms: collect resource sites into a database, serve them via FastAPI, keep them healthy."""

from .config import settings

__version__ = "0.1.0"

__all__ = ["settings", "__version__"]
