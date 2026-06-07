"""
Cognee memory layer — the shared brain the four agents hand off through.

Each agent WRITES its structured output here after acting, and READS upstream
agents' output before acting. This is the real handoff mechanism (not file
passing): Scout -> Ranker -> Fixer -> Narrator all communicate through memory.

Cognee is attempted first. If the SDK is missing or throws at any point, we fall
back to a simple in-process dict store so the pipeline NEVER breaks during a
demo. The fallback is logged loudly so it's obvious what happened.
"""
import json
from datetime import datetime

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
SESSION_ID = "default"
_USE_COGNEE = False                       # flips True only if Cognee initializes
_FALLBACK: dict[str, dict] = {}           # in-process store: agent_name -> record


def _key(agent_name: str) -> str:
    return f"auditguard_{agent_name}_{SESSION_ID}"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
async def init_cognee() -> None:
    """Initialize Cognee. Falls back to dict store on any problem."""
    global _USE_COGNEE
    try:
        import os
        import cognee  # noqa: F401

        # Cognee config is best-effort; if the cloud/key isn't present, the
        # library still works locally for some setups, but to keep the demo
        # bulletproof we treat ANY exception below as "use fallback".
        key = os.getenv("COGNEE_API_KEY")
        if key and hasattr(cognee, "config"):
            try:
                cognee.config.set_llm_api_key(os.getenv("ANTHROPIC_API_KEY", ""))
            except Exception:
                pass
        # Probe: a trivial round-trip would require network; instead we only
        # mark Cognee available if the module imported cleanly AND a key exists.
        if key and key.strip() not in ("", "your_cognee_api_key_here"):
            _USE_COGNEE = True
            print("[memory] Cognee memory layer ENABLED.")
        else:
            _USE_COGNEE = False
            print("[memory] No COGNEE_API_KEY set — using in-process dict store "
                  "(handoffs still fully functional).")
    except Exception as e:  # noqa: BLE001
        _USE_COGNEE = False
        print(f"[memory] Cognee unavailable ({e}). FALLING BACK to dict store.")


async def write_memory(agent_name: str, data: dict) -> bool:
    """Persist an agent's output. Returns True/False, never raises."""
    record = {
        "agent_name": agent_name,
        "timestamp": datetime.utcnow().isoformat(),
        "data": data,
    }
    if _USE_COGNEE:
        try:
            import cognee
            await cognee.add(json.dumps(record), dataset_name=_key(agent_name))
            # Mirror into the dict store too, so reads are instant and exact.
            _FALLBACK[agent_name] = record
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[memory] Cognee write failed for {agent_name} ({e}); "
                  "stored in dict fallback.")
    _FALLBACK[agent_name] = record
    return True


async def read_memory(agent_name: str) -> dict | None:
    """Read one agent's data payload, or None if absent."""
    record = _FALLBACK.get(agent_name)
    if record is None:
        return None
    return record.get("data")


async def read_all_memory() -> dict:
    """Read every agent's data payload, keyed by agent_name."""
    return {name: rec.get("data") for name, rec in _FALLBACK.items()}


async def clear_session() -> None:
    """Wipe memory for a fresh audit run."""
    _FALLBACK.clear()
    if _USE_COGNEE:
        try:
            import cognee
            await cognee.prune.prune_data()
        except Exception as e:  # noqa: BLE001
            print(f"[memory] Cognee prune skipped ({e}).")
    print("[memory] Session cleared.")
