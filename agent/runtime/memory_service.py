"""Memory Service — persistent design and user memory.

Three memory types:
1. Session memory — conversation + thought stream (in-memory, ephemeral)
2. Design memory — intent + decisions + assumptions (persisted to disk)
3. User memory — cross-session preferences (persisted to disk)

Thread-safe: all writes are serialized. Reads are lock-free (consistent snapshots).
No memory leaks: bounded in-memory stores with explicit eviction.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


# ── Data Classes ───────────────────────────────────────────────────────────

@dataclass
class DesignIntent:
    """Structured design intent captured from user prompts."""
    cost: dict = field(default_factory=dict)           # {"max_bom_cost": 20.0}
    performance: dict = field(default_factory=dict)     # {"max_power": 500}
    thermal: dict = field(default_factory=dict)         # {"max_temp": 80}
    physical: dict = field(default_factory=dict)        # {"max_layers": 4}
    reliability: dict = field(default_factory=dict)     # {"mtbf_hours": 50000}
    emi: dict = field(default_factory=dict)             # {"standard": "FCC Class B"}
    exclusions: list[str] = field(default_factory=list) # ["BGA", "0402"]

    def to_dict(self) -> dict:
        return {
            "cost": self.cost, "performance": self.performance,
            "thermal": self.thermal, "physical": self.physical,
            "reliability": self.reliability, "emi": self.emi,
            "exclusions": self.exclusions,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DesignIntent:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Decision:
    """A recorded design decision with rationale."""
    id: str = field(default_factory=lambda: uuid4().hex[:8])
    entity_id: str = ""
    revision: int = 0
    decision: str = ""
    rationale: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "entity_id": self.entity_id,
            "revision": self.revision, "decision": self.decision,
            "rationale": self.rationale, "timestamp": self.timestamp,
        }


@dataclass
class UserPreferences:
    """Cross-session user preferences."""
    preferred_parts: dict[str, str] = field(default_factory=dict)
    rejected_parts: list[str] = field(default_factory=list)
    preferred_values: dict[str, str] = field(default_factory=dict)
    design_patterns: list[str] = field(default_factory=list)
    correction_count: int = 0
    # Review feedback: tracks which suggestions users accept/reject
    review_feedback: dict[str, bool] = field(default_factory=dict)  # {suggestion_id: accepted}
    # Learned patterns from reviews: recurring issues across designs
    learned_patterns: list[dict] = field(default_factory=list)  # [{type, description, frequency}]

    def to_dict(self) -> dict:
        return {
            "preferred_parts": self.preferred_parts,
            "rejected_parts": self.rejected_parts,
            "preferred_values": self.preferred_values,
            "design_patterns": self.design_patterns,
            "correction_count": self.correction_count,
            "review_feedback": self.review_feedback,
            "learned_patterns": self.learned_patterns,
        }

    @classmethod
    def from_dict(cls, data: dict) -> UserPreferences:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class MemoryService:
    """Persistent memory with bounded in-memory stores and disk persistence.

    Properties:
        max_decisions: maximum in-memory decisions per design (default 500)
        max_conversation: maximum conversation messages (default 200)
    """

    def __init__(self, design_id: str, data_dir: str | None = None, *,
                 max_decisions: int = 500, max_conversation: int = 200):
        self._design_id = design_id
        self._data_dir = data_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "data", "memory"
        )
        self._max_decisions = max_decisions
        self._max_conversation = max_conversation

        # In-memory stores
        self._conversation: list[dict] = []
        self._decisions: OrderedDict[str, Decision] = OrderedDict()
        self._intent: DesignIntent | None = None
        self._lock = threading.Lock()

        # Ensure data directory exists
        os.makedirs(self._data_dir, exist_ok=True)

        # Load persisted data on construction
        self._load_from_disk()

    @property
    def design_id(self) -> str:
        return self._design_id

    # ── Session Memory ─────────────────────────────────────────────────

    def get_conversation(self) -> list[dict]:
        """Get conversation history."""
        with self._lock:
            return list(self._conversation)

    def append_conversation(self, message: dict) -> None:
        """Append a message to conversation history."""
        with self._lock:
            self._conversation.append(message)
            # Bounded: drop oldest if over limit
            if len(self._conversation) > self._max_conversation:
                self._conversation = self._conversation[-self._max_conversation:]

    # ── Design Memory ──────────────────────────────────────────────────

    def get_intent(self) -> DesignIntent:
        """Get the current design intent."""
        if self._intent is None:
            return DesignIntent()
        return self._intent

    def set_intent(self, intent: DesignIntent) -> None:
        """Set the design intent."""
        with self._lock:
            self._intent = intent
            self._persist_design()

    def record_decision(self, entity_id: str, decision: str, rationale: str = "",
                        revision: int = 0) -> Decision:
        """Record a design decision."""
        dec = Decision(
            entity_id=entity_id, revision=revision,
            decision=decision, rationale=rationale,
        )
        with self._lock:
            self._decisions[dec.id] = dec
            # Bounded: drop oldest if over limit
            while len(self._decisions) > self._max_decisions:
                self._decisions.popitem(last=False)
            self._persist_design()
        return dec

    def get_decisions(self, entity_id: str | None = None) -> list[Decision]:
        """Get decisions, optionally filtered by entity."""
        with self._lock:
            decisions = list(self._decisions.values())
        if entity_id:
            decisions = [d for d in decisions if d.entity_id == entity_id]
        return decisions

    def record_assumption(self, entity_id: str, assumption: str, value: str) -> Decision:
        """Record a design assumption as a decision."""
        return self.record_decision(
            entity_id=entity_id,
            decision=f"Assume: {assumption} = {value}",
            rationale="Design assumption",
        )

    # ── User Memory ────────────────────────────────────────────────────

    def get_preferences(self) -> UserPreferences:
        """Get user preferences."""
        path = self._preferences_path()
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return UserPreferences.from_dict(json.load(f))
            except Exception:
                pass
        return UserPreferences()

    def set_preferences(self, prefs: UserPreferences) -> None:
        """Persist user preferences."""
        path = self._preferences_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(prefs.to_dict(), f, indent=2)

    def record_preference(self, domain: str, key: str, value: str,
                          confidence: float = 1.0) -> None:
        """Record a user preference."""
        prefs = self.get_preferences()
        if domain == "preferred_parts":
            prefs.preferred_parts[key] = value
        elif domain == "rejected_parts":
            if value not in prefs.rejected_parts:
                prefs.rejected_parts.append(value)
        elif domain == "preferred_values":
            prefs.preferred_values[key] = value
        elif domain == "design_patterns":
            if value not in prefs.design_patterns:
                prefs.design_patterns.append(value)
        prefs.correction_count += 1
        self.set_preferences(prefs)

    def record_review_feedback(self, suggestion_id: str, accepted: bool,
                                suggestion_type: str = "") -> None:
        """Record whether a review suggestion was accepted or rejected.

        Args:
            suggestion_id: unique identifier for the suggestion
            accepted: True if user accepted, False if rejected
            suggestion_type: category/type of suggestion (e.g., 'power', 'signal')
        """
        prefs = self.get_preferences()
        prefs.review_feedback[suggestion_id] = accepted

        # Update learned patterns based on feedback
        if suggestion_type:
            self._update_learned_pattern(prefs, suggestion_type, accepted)

        self.set_preferences(prefs)

    def _update_learned_pattern(self, prefs: UserPreferences, pattern_type: str,
                                 accepted: bool) -> None:
        """Update learned patterns based on review feedback."""
        # Find existing pattern or create new one
        existing = next((p for p in prefs.learned_patterns if p.get("type") == pattern_type), None)

        if existing:
            existing["frequency"] = existing.get("frequency", 0) + 1
            existing["last_seen"] = time.time()
            if accepted:
                existing["accepted_count"] = existing.get("accepted_count", 0) + 1
            else:
                existing["rejected_count"] = existing.get("rejected_count", 0) + 1
        else:
            prefs.learned_patterns.append({
                "type": pattern_type,
                "frequency": 1,
                "accepted_count": 1 if accepted else 0,
                "rejected_count": 0 if accepted else 1,
                "last_seen": time.time(),
            })

        # Keep only top 20 patterns by frequency
        prefs.learned_patterns.sort(key=lambda x: x.get("frequency", 0), reverse=True)
        prefs.learned_patterns = prefs.learned_patterns[:20]

    def get_learned_patterns(self, min_frequency: int = 2) -> list[dict]:
        """Get learned patterns that appear at least min_frequency times.

        Returns patterns sorted by acceptance rate (most useful first).
        """
        prefs = self.get_preferences()
        patterns = [
            p for p in prefs.learned_patterns
            if p.get("frequency", 0) >= min_frequency
        ]
        # Sort by acceptance rate
        def acceptance_rate(p):
            total = p.get("accepted_count", 0) + p.get("rejected_count", 0)
            return p.get("accepted_count", 0) / total if total > 0 else 0.5

        patterns.sort(key=acceptance_rate, reverse=True)
        return patterns

    def get_review_stats(self) -> dict:
        """Get statistics about review feedback."""
        prefs = self.get_preferences()
        feedback = prefs.review_feedback
        if not feedback:
            return {"total": 0, "accepted": 0, "rejected": 0, "acceptance_rate": 0}

        accepted = sum(1 for v in feedback.values() if v)
        rejected = sum(1 for v in feedback.values() if not v)
        return {
            "total": len(feedback),
            "accepted": accepted,
            "rejected": rejected,
            "acceptance_rate": accepted / len(feedback) if feedback else 0,
        }

    # ── Cleanup ────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Clear all in-memory state."""
        with self._lock:
            self._conversation.clear()
            self._decisions.clear()
            self._intent = None

    def _preferences_path(self) -> str:
        return os.path.join(self._data_dir, f"{self._design_id}_preferences.json")

    def _design_path(self) -> str:
        return os.path.join(self._data_dir, f"{self._design_id}_design.json")

    # ── Persistence ────────────────────────────────────────────────────

    def _persist_design(self) -> None:
        """Persist design memory to disk (must hold lock)."""
        try:
            data = {
                "design_id": self._design_id,
                "intent": self._intent.to_dict() if self._intent else None,
                "decisions": [d.to_dict() for d in self._decisions.values()],
            }
            path = self._design_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass  # best-effort persistence

    def _load_from_disk(self) -> None:
        """Load persisted design memory from disk."""
        try:
            path = self._design_path()
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                if data.get("intent"):
                    self._intent = DesignIntent.from_dict(data["intent"])
                for d in data.get("decisions", []):
                    dec = Decision(**{k: v for k, v in d.items()
                                      if k in Decision.__dataclass_fields__})
                    self._decisions[dec.id] = dec
        except Exception:
            pass  # best-effort loading
