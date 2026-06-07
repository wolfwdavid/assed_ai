"""
Best-effort Claude helper.

The entire AssedGuard pipeline is deterministic and runs fully offline. Claude
is used ONLY to enrich human-facing text (audit context, narrative prose). Every
call here is wrapped: if there is no API key, or the SDK/network fails, we return
a deterministic `fallback` string and the pipeline keeps moving. This guarantees
the demo never breaks and that no decision ever depends on "the model said so."
"""
import os

MODEL = "claude-sonnet-4-6"

# Circuit breaker: once a call fails for a reason that won't fix itself within
# the run (bad/missing key, auth error), stop hammering the API and serve
# deterministic fallbacks instantly for the rest of the session.
_DISABLED = False


async def ask_claude(prompt: str, system: str | None = None,
                     max_tokens: int = 400, fallback: str = "") -> str:
    """Call Claude and return its text. On ANY failure, return `fallback`."""
    global _DISABLED
    if _DISABLED:
        return fallback
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or api_key.strip() in ("", "your_anthropic_api_key_here"):
        return fallback
    try:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=api_key)
        kwargs = {
            "model": MODEL,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        msg = await client.messages.create(**kwargs)
        parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
        text = "".join(parts).strip()
        return text or fallback
    except Exception as e:  # noqa: BLE001 — never break the pipeline
        msg = str(e).lower()
        if any(s in msg for s in ("401", "403", "authentication", "invalid x-api-key",
                                  "permission", "credit", "not_found", "404")):
            _DISABLED = True
            print(f"[ai] Claude unavailable ({e}); disabling further calls and using "
                  "deterministic fallbacks for the rest of this run.")
        else:
            print(f"[ai] Claude call failed, using deterministic fallback: {e}")
        return fallback
