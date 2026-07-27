"""
TscircuitClient — component data client for CircuitBot.

Provides search, lookup, and download of component data (symbols, footprints,
pin definitions) from tscircuit npm packages and GitHub repositories.

No rendering dependencies — data only.
"""

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import requests

from .schema import Component, Footprint, Symbol, Pin

logger = logging.getLogger(__name__)

# Cache directory
CACHE_DIR = Path(__file__).parent.parent / "data" / "tscircuit_cache"
NPM_CACHE_DIR = Path(__file__).parent.parent / "node_modules" / "@tscircuit"

# GitHub repos for component data
GITHUB_REPOS = {
    "footprinter": "tscircuit/footprinter",
    "core": "tscircuit/core",
    "props": "tscircuit/props",
}


class TscircuitClient:
    """Client for querying tscircuit component data."""

    def __init__(self, cache_enabled: bool = True, timeout: int = 15):
        self.cache_enabled = cache_enabled
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "CircuitBot/0.8 tscircuit-data-client",
            "Accept": "application/json",
        })
        if cache_enabled:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def search(self, query: str, limit: int = 10) -> list[Component]:
        """Search for components by name, description, or part number.

        Args:
            query: Search term (e.g. "ESP32", "10k resistor", "USB-C connector")
            limit: Max results to return

        Returns:
            List of Component objects
        """
        cache_key = f"search_{query}_{limit}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return [Component.from_dict(c) for c in cached]

        components = []

        # Try to search local npm packages first
        components = self._search_npm_packages(query, limit)

        # If no local results, try GitHub
        if not components:
            components = self._search_github(query, limit)

        # Cache results
        if self.cache_enabled and components:
            self._set_cache(cache_key, [c.to_dict() for c in components])

        return components

    def get_component(self, package_id: str) -> Optional[Component]:
        """Get a specific component by package ID.

        Args:
            package_id: Component identifier (e.g. "Device:R", "MCU_ESP32:ESP32-C3")

        Returns:
            Component object or None
        """
        cache_key = f"comp_{package_id}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return Component.from_dict(cached)

        # Try local npm packages
        comp = self._get_from_npm(package_id)
        if comp:
            if self.cache_enabled:
                self._set_cache(cache_key, comp.to_dict())
            return comp

        # Try GitHub
        comp = self._get_from_github(package_id)
        if comp and self.cache_enabled:
            self._set_cache(cache_key, comp.to_dict())

        return comp

    def get_footprint(self, footprint_name: str) -> Optional[Footprint]:
        """Get footprint data by name.

        Args:
            footprint_name: Footprint identifier (e.g. "0805", "SOT-23-6")

        Returns:
            Footprint object or None
        """
        cache_key = f"fp_{footprint_name}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return Footprint.from_dict(cached)

        # Try built-in templates first
        from .footprint import generate_footprint
        fp = generate_footprint(footprint_name)
        if fp:
            if self.cache_enabled:
                self._set_cache(cache_key, {"name": fp.name, "pads": fp.pads, "layers": fp.layers})
            return fp

        return None

    def _search_npm_packages(self, query: str, limit: int) -> list[Component]:
        """Search installed npm packages for components."""
        components = []

        if not NPM_CACHE_DIR.exists():
            return components

        # Search through package directories
        for pkg_dir in NPM_CACHE_DIR.iterdir():
            if not pkg_dir.is_dir():
                continue

            # Look for component data files
            for data_file in pkg_dir.rglob("*.json"):
                try:
                    data = json.loads(data_file.read_text())
                    if isinstance(data, dict):
                        # Check if this matches the query
                        name = data.get("name", data.get("id", ""))
                        desc = data.get("description", "")
                        if query.lower() in name.lower() or query.lower() in desc.lower():
                            comp = Component.from_dict(data)
                            if comp:
                                components.append(comp)
                                if len(components) >= limit:
                                    return components
                except Exception:
                    continue

        return components

    def _get_from_npm(self, package_id: str) -> Optional[Component]:
        """Get component from installed npm packages."""
        if not NPM_CACHE_DIR.exists():
            return None

        # Search for matching package
        for pkg_dir in NPM_CACHE_DIR.iterdir():
            if not pkg_dir.is_dir():
                continue

            for data_file in pkg_dir.rglob("*.json"):
                try:
                    data = json.loads(data_file.read_text())
                    if isinstance(data, dict):
                        name = data.get("name", data.get("id", ""))
                        if name == package_id or package_id in name:
                            return Component.from_dict(data)
                except Exception:
                    continue

        return None

    def _search_github(self, query: str, limit: int) -> list[Component]:
        """Search GitHub for component data."""
        components = []

        for repo_name, repo_path in GITHUB_REPOS.items():
            try:
                # Search GitHub API
                url = f"https://api.github.com/search/code?q={query}+repo:{repo_path}"
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("items", [])[:limit]:
                        # Try to fetch the file
                        raw_url = f"https://raw.githubusercontent.com/{repo_path}/main/{item['path']}"
                        raw_resp = self.session.get(raw_url, timeout=self.timeout)
                        if raw_resp.status_code == 200:
                            try:
                                file_data = json.loads(raw_resp.text)
                                comp = Component.from_dict(file_data)
                                if comp:
                                    components.append(comp)
                                    if len(components) >= limit:
                                        return components
                            except json.JSONDecodeError:
                                pass
            except Exception as e:
                logger.debug(f"GitHub search failed for {repo_name}: {e}")
                continue

        return components

    def _get_from_github(self, package_id: str) -> Optional[Component]:
        """Get component from GitHub."""
        for repo_name, repo_path in GITHUB_REPOS.items():
            try:
                # Try common file paths
                paths = [
                    f"lib/components/{package_id}.json",
                    f"src/components/{package_id}.json",
                    f"data/{package_id}.json",
                ]
                for path in paths:
                    raw_url = f"https://raw.githubusercontent.com/{repo_path}/main/{path}"
                    resp = self.session.get(raw_url, timeout=self.timeout)
                    if resp.status_code == 200:
                        data = resp.json()
                        return Component.from_dict(data)
            except Exception:
                continue

        return None

    def _get_cache(self, key: str) -> Optional[dict]:
        """Get cached data if fresh (< 24 hours old)."""
        if not self.cache_enabled:
            return None
        cache_file = CACHE_DIR / f"{self._safe_filename(key)}.json"
        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < 86400:  # 24 hours
                try:
                    return json.loads(cache_file.read_text())
                except Exception:
                    pass
        return None

    def _set_cache(self, key: str, data: dict):
        """Write data to cache."""
        if not self.cache_enabled:
            return
        cache_file = CACHE_DIR / f"{self._safe_filename(key)}.json"
        try:
            cache_file.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def _safe_filename(self, key: str) -> str:
        """Convert a cache key to a safe filename."""
        return key.replace("/", "_").replace("\\", "_").replace(":", "_").replace(" ", "_")[:100]
