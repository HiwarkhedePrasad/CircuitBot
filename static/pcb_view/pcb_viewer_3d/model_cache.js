/**
 * model_cache.js — LRU cache for parsed 3D model geometries.
 */
class ModelCache {
    constructor(maxEntries = 200) {
        this._cache = new Map();
        this._maxEntries = maxEntries;
    }

    get(path) {
        if (!this._cache.has(path)) return null;
        // Move to end (most recently used)
        const val = this._cache.get(path);
        this._cache.delete(path);
        this._cache.set(path, val);
        return val;
    }

    set(path, geometries) {
        if (this._cache.has(path)) {
            this._cache.delete(path);
        } else if (this._cache.size >= this._maxEntries) {
            // Evict oldest
            const firstKey = this._cache.keys().next().value;
            const oldGeo = this._cache.get(firstKey);
            this._disposeGeometries(oldGeo);
            this._cache.delete(firstKey);
        }
        this._cache.set(path, geometries);
    }

    has(path) {
        return this._cache.has(path);
    }

    clear() {
        for (const [, geos] of this._cache) {
            this._disposeGeometries(geos);
        }
        this._cache.clear();
    }

    _disposeGeometries(geos) {
        if (!Array.isArray(geos)) return;
        for (const g of geos) {
            if (g && g.dispose) g.dispose();
        }
    }
}
