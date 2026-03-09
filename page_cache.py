"""Cache disque pour les pages web scrapées.

Stocke le contenu HTML dans le dossier ``cache/`` et maintient un index
JSON avec les métadonnées (URL, timestamp).  Chaque URL est identifiée
par son hash MD5.

Utilisation typique :

    cache = PageCache(ttl=86400)
    html = cache.get("https://example.com/contact")
    if html is None:
        html = await fetch(...)
        cache.set("https://example.com/contact", html)
"""

import hashlib
import json
import time
from pathlib import Path


class PageCache:
    """Cache fichier simple pour du contenu HTML."""

    def __init__(
        self,
        cache_dir: str | Path = "cache",
        ttl: int = 86400,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.ttl = ttl
        self._index: dict[str, dict] = {}
        self._load_index()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, url: str) -> str | None:
        """Renvoie le HTML en cache ou ``None`` si absent / expiré."""
        key = self._key(url)
        entry = self._index.get(key)
        if entry is None:
            return None
        if time.time() - entry["ts"] > self.ttl:
            return None
        path = self.cache_dir / f"{key}.html"
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None

    def set(self, url: str, content: str) -> None:
        """Enregistre le HTML d'une page dans le cache."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        key = self._key(url)
        path = self.cache_dir / f"{key}.html"
        try:
            path.write_text(content, encoding="utf-8", errors="replace")
            self._index[key] = {"url": url, "ts": time.time()}
            self._save_index()
        except Exception:
            pass

    def clear(self) -> None:
        """Supprime tout le cache."""
        if self.cache_dir.exists():
            for f in self.cache_dir.iterdir():
                f.unlink(missing_ok=True)
        self._index.clear()
        self._save_index()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @property
    def _index_path(self) -> Path:
        return self.cache_dir / "index.json"

    @staticmethod
    def _key(url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()

    def _load_index(self) -> None:
        if self._index_path.exists():
            try:
                self._index = json.loads(
                    self._index_path.read_text(encoding="utf-8")
                )
            except Exception:
                self._index = {}

    def _save_index(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._index_path.write_text(
                json.dumps(self._index, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
