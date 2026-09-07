"""
Redis connection — used for two things:
  1. Caching live data (score/pitch/weather) for a few seconds so repeated
     questions in a short window don't refetch everything.
  2. Later: holding short conversation memory per match (e.g. so "Why not
     Naseem?" doesn't need the coach to repeat context).

REDIS_URL comes from .env, defaults to localhost for the docker-compose setup.
"""

import os
import json
from dotenv import load_dotenv
import redis

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

redis_client = redis.from_url(REDIS_URL, decode_responses=True)


def cache_get(key: str):
    value = redis_client.get(key)
    return json.loads(value) if value else None


def cache_set(key: str, value, ttl_seconds: int = 10):
    redis_client.set(key, json.dumps(value), ex=ttl_seconds)
