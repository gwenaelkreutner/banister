#!/usr/bin/env python3
"""Run the response verifier over real chat history (spec 006 quickstart Scenario 3).

    python -m scripts.verify_corpus --last 100

For the last N assistant `chat_messages`, prints each `(metric term, number)` claim it
finds, the verdict, and the false-positive candidates — so the verifier's *precision* on
real prose can be reviewed by hand, not just its catch rate (spec 006 research R5: a real
reply carried fourteen numerals of which twelve were durations and zone codes).

Read-only.

Populated in US3 (T036).
"""
from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
