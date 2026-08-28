#!/usr/bin/env python3
"""Describe what the athlete's data actually supports (spec 006 quickstart Scenario 0).

    python -m scripts.guardrail_state --describe

Per signal: days with data, date range covered, whether a baseline is establishable, and
whether a current observation exists. Run this first — it tells you which quickstart
scenarios can be checked live and which need fixtures (spec 006 research R1 found no HRV,
no sleep, and no resting HR since 2026-07-19 for this athlete).

Read-only.

Populated in US5 (T050).
"""
from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
