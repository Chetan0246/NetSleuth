"""Application-wide configuration constants.

All thresholds live here so that the diagnostic engine, the probes and the
experiment runner always use one single source of truth (no duplicated logic,
see plan.md section 15.4).
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "NetSleuth"
APP_VERSION = "1.0.0"
API_PREFIX = "/api/v1"

# Version identifier stored with every persisted run / experiment export so that
# results can be tied back to a specific likelihood-model revision.
MODEL_VERSION = "netsleuth-likelihood-v1"
PRIOR_CONFIG_VERSION = "netsleuth-priors-v1"

# --- persistence -----------------------------------------------------------
DEFAULT_DB_FILENAME = "netsleuth.sqlite3"


def default_db_path() -> Path:
    """Location of the SQLite database (overridable for tests/deployments)."""
    env = os.environ.get("NETSLEUTH_DB")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "data" / DEFAULT_DB_FILENAME


# --- simulated network model ----------------------------------------------
DEFAULT_RANDOM_SEED = 20261009

# Simulated ICMP echo behaviour.
ICMP_ECHO_COUNT = 4
ICMP_TIMEOUT_MS = 1200.0
# A mean RTT above this value is reported as "slow" (REACHABLE_SLOW evidence).
ICMP_HIGH_LATENCY_MS = 150.0

# Simulated DNS behaviour.
DNS_TIMEOUT_MS = 2000.0
DNS_SLOW_MS = 400.0

# Simulated TCP behaviour.
TCP_CONNECT_TIMEOUT_MS = 3000.0

# Traceroute behaviour.
TRACEROUTE_MAX_TTL = 16
TRACEROUTE_HOP_TIMEOUT_MS = 1000.0

# MTU probe: the documented ladder of packet sizes (bytes of IP payload + 28
# bytes of IP+ICMP header are NOT included; the values are total IP datagram
# sizes as reported to the user).
MTU_PROBE_SIZES = (64, 128, 256, 512, 1000, 1400, 1472, 1500)
# Ethernet/typical minimum IPv4 MTU accepted by validation (RFC 791 minimum
# reassembly buffer is 576 bytes; the absolute minimum legal IPv4 MTU is 68).
MTU_MIN = 68
MTU_MAX = 65535
MTU_STANDARD = 1500

# --- diagnostic engine ----------------------------------------------------
# Stop when the leading hypothesis reaches this posterior ...
CONFIDENCE_THRESHOLD = 0.80
# ... and leads the runner-up by at least this margin ...
CONFIDENCE_LEAD = 0.20
# ... or when no remaining probe is expected to reduce entropy by this much
# information (bits). UNKNOWN is always available as a fallback hypothesis.
MIN_INFORMATION_GAIN_BITS = 0.02

#: A "no fault detected" verdict is a claim about *every* layer, so it may only be
#: asserted once at least this many distinct probe classes have been executed. A
#: single healthy echo request proves nothing about the DNS layer, the transport
#: layer or the path MTU, so without this guard the engine would confidently report
#: "no fault" three probes into a diagnosis that had not yet looked at the layer
#: where the actual fault lives. This is the "absence of evidence requires breadth
#: of evidence" rule; it is enforced in :func:`app.diagnosis.planner.evaluate_stopping_rule`.
MIN_PROBE_TYPES_FOR_NO_FAULT = 4

DEFAULT_MAX_PROBES = 8
MAX_MAX_PROBES = 24

# --- experiments ----------------------------------------------------------
DEFAULT_RUNS_PER_SCENARIO = 30
MAX_RUNS_PER_SCENARIO = 200
MAX_EXPERIMENT_TOTAL_RUNS = 4000

# --- live probes (optional feature) ---------------------------------------
LIVE_TIMEOUT_S = 3.0
LIVE_MAX_ATTEMPTS = 3
