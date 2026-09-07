"""Database access for persistent player bowling-type statistics."""

from copy import deepcopy
from typing import Optional

from sqlalchemy.orm import Session

from core.models import PlayerTypeStatsCache


def _cache_key(player_name: str) -> str:
    return " ".join(player_name.lower().split())


def load_player_type_stats(
    db: Optional[Session],
    player_name: str,
) -> dict | None:
    if db is None:
        return None
    try:
        row = db.get(PlayerTypeStatsCache, _cache_key(player_name))
        return deepcopy(row.stats) if row and row.stats else None
    except Exception as exc:
        db.rollback()
        print(f"[player-cache] Could not read {player_name}: {exc}")
        return None


def save_player_type_stats(
    db: Optional[Session],
    player_name: str,
    stats: dict,
) -> bool:
    if db is None:
        return False
    try:
        key = _cache_key(player_name)
        row = db.get(PlayerTypeStatsCache, key)
        if row is None:
            row = PlayerTypeStatsCache(
                query_key=key,
                player_name=stats.get("player") or player_name,
                stats=deepcopy(stats),
            )
            db.add(row)
        else:
            row.player_name = stats.get("player") or player_name
            row.stats = deepcopy(stats)
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        print(f"[player-cache] Could not save {player_name}: {exc}")
        return False
