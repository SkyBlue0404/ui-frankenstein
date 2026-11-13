from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "server": {"host": "127.0.0.1", "port": 8777},
    "defaults": {
        "fps": 12,
        "quality": 80,
        "capture": "window",
        "click_mode": "event",
        "hotkey": "ctrl+alt+p",
    },
    "layout": {"min_tile_px": 260, "gap_px": 10},
    "tiles": [],
}

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ConfigError(ValueError):
    pass


class Config:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.RLock()
        self.data: dict[str, Any] = json.loads(json.dumps(DEFAULT_CONFIG))
        self.load()

    # ---- 磁盘 ----

    def load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text("utf-8"))
        if not isinstance(raw, dict):
            raise ConfigError("config root must be a JSON object")
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        merged.update(raw)
        self.data = self._normalize(merged)

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self.data, fh, ensure_ascii=False, indent=2)
                    fh.write("\n")
                os.replace(tmp, self.path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    # ---- 访问 ----

    @property
    def server(self) -> dict[str, Any]:
        return self.data["server"]

    @property
    def defaults(self) -> dict[str, Any]:
        return self.data["defaults"]

    @property
    def layout(self) -> dict[str, Any]:
        return self.data["layout"]

    @property
    def tiles(self) -> list[dict[str, Any]]:
        return self.data["tiles"]

    def tile(self, tile_id: str) -> dict[str, Any] | None:
        for t in self.tiles:
            if t["id"] == tile_id:
                return t
        return None

    # ---- 变更 ----

    def upsert_tile(self, tile: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            norm = self._normalize_tile(tile)
            for i, t in enumerate(self.tiles):
                if t["id"] == norm["id"]:
                    self.tiles[i] = norm
                    return norm
            self.tiles.append(norm)
            return norm

    def remove_tile(self, tile_id: str) -> bool:
        with self._lock:
            before = len(self.tiles)
            self.data["tiles"] = [t for t in self.tiles if t["id"] != tile_id]
            return len(self.data["tiles"]) != before

    def reorder(self, ids: list[str]) -> None:
        with self._lock:
            by_id = {t["id"]: t for t in self.tiles}
            self.data["tiles"] = [by_id[i] for i in ids if i in by_id]

    # ---- 校验 ----

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        tiles = data.get("tiles")
        if not isinstance(tiles, list):
            raise ConfigError("'tiles' must be a list")
        seen: set[str] = set()
        out = []
        for i, t in enumerate(tiles):
            if not isinstance(t, dict):
                raise ConfigError(f"tiles[{i}] must be an object")
            norm = self._normalize_tile(t, index=i)
            if norm["id"] in seen:
                raise ConfigError(f"duplicate tile id: {norm['id']}")
            seen.add(norm["id"])
            out.append(norm)
        data["tiles"] = out
        return data

    def _normalize_tile(self, tile: dict[str, Any], index: int = 0) -> dict[str, Any]:
        tid = str(tile.get("id") or "").strip()
        if not tid:
            tid = f"tile-{index + 1}"
        if not _ID_RE.match(tid):
            raise ConfigError(f"invalid tile id {tid!r}: use 1-64 chars of [A-Za-z0-9_-]")

        win = tile.get("window") or {}
        if not isinstance(win, dict):
            raise ConfigError(f"{tid}: 'window' must be an object")
        window = {
            "handle": str(win.get("handle") or "").strip(),
            "title_regex": str(win.get("title_regex") or "").strip(),
            "app_regex": str(win.get("app_regex") or "").strip(),
            "index": int(win.get("index") or 0),
        }
        for key in ("title_regex", "app_regex"):
            if window[key]:
                try:
                    re.compile(window[key])
                except re.error as exc:
                    raise ConfigError(f"{tid}: invalid {key}: {exc}") from exc

        region = tile.get("region") or {}
        if not isinstance(region, dict):
            raise ConfigError(f"{tid}: 'region' must be an object")
        try:
            x = float(region.get("x", 0.0))
            y = float(region.get("y", 0.0))
            w = float(region.get("w", 1.0))
            h = float(region.get("h", 1.0))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{tid}: region values must be numbers") from exc
        x, y = max(0.0, min(1.0, x)), max(0.0, min(1.0, y))
        w, h = max(0.001, min(1.0 - x, w)), max(0.001, min(1.0 - y, h))

        defaults = self.data.get("defaults", DEFAULT_CONFIG["defaults"])
        capture = str(tile.get("capture") or defaults.get("capture", "window"))
        if capture not in ("window", "screen"):
            raise ConfigError(f"{tid}: capture must be 'window' or 'screen'")

        # 画布模式下的自由位置；None = 还没摆放过，由前端自动布局
        canvas = tile.get("canvas")
        canvas_norm = None
        if isinstance(canvas, dict):
            try:
                canvas_norm = {
                    "x": float(canvas.get("x", 40)),
                    "y": float(canvas.get("y", 40)),
                    "w": max(40.0, float(canvas.get("w", 480))),
                    "h": max(20.0, float(canvas.get("h", 300))),
                }
            except (TypeError, ValueError):
                canvas_norm = None

        return {
            "id": tid,
            "title": str(tile.get("title") or tid),
            "window": window,
            "region": {"x": round(x, 6), "y": round(y, 6), "w": round(w, 6), "h": round(h, 6)},
            "fps": max(0.5, min(30.0, float(tile.get("fps") or defaults.get("fps", 12)))),
            "quality": max(30, min(95, int(tile.get("quality") or defaults.get("quality", 80)))),
            "capture": capture,
            "interactive": bool(tile.get("interactive", True)),
            "canvas": canvas_norm,
        }
