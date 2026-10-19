"""Deterministic random streams (docs/day1-design.md section 8.1).

Every part of the generator draws from its own named stream, so turning one feature on or
off (for example physical snapshots) does not change what the other streams produce.
"""

import hashlib
import random
from dataclasses import dataclass


def rng_stream(seed: int, attempt: int, name: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{attempt}:{name}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


@dataclass(frozen=True)
class Streams:
    flow: random.Random
    players: random.Random
    cards: random.Random
    subs: random.Random
    physical: random.Random
    patterns: random.Random
    meta: random.Random

    @classmethod
    def create(cls, seed: int, attempt: int) -> "Streams":
        return cls(
            flow=rng_stream(seed, attempt, "flow"),
            players=rng_stream(seed, attempt, "players"),
            cards=rng_stream(seed, attempt, "cards"),
            subs=rng_stream(seed, attempt, "subs"),
            physical=rng_stream(seed, attempt, "physical"),
            patterns=rng_stream(seed, attempt, "patterns"),
            meta=rng_stream(seed, attempt, "meta"),
        )
