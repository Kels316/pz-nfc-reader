#!/usr/bin/env python3
"""
gateway_discovery.py — Automatic PZTrack gateway discovery.

Since each LoRaWAN gateway IS the WiFi access point for "PZ Network",
it is always reachable at the default route IP on whatever network the
Pi connects to.

This module reads the default gateway IP from the routing table and
verifies it is running the PZTrack API before returning it.  If the
network is not yet up (e.g. the Pi booted before the gateway was
reachable), it retries with a configurable interval.

Usage in config.json
--------------------
Set api.base_url and/or db.host to "auto" to enable discovery:

  "api": { "base_url": "auto" }
  "db":  { "host": "auto" }

A fixed IP/URL can still be used by setting the values normally.
"""

import logging
import re
import subprocess
import time

import requests

logger = logging.getLogger(__name__)

_API_PORT = 5000
_DISCOVERY_TIMEOUT = 3      # seconds to wait for each API probe
_RETRY_INTERVAL   = 5       # seconds between retries
_MAX_RETRIES      = None     # None = retry forever until success


def get_default_gateway_ip() -> str | None:
    """
    Return the default gateway IP from the kernel routing table, or None
    if it cannot be determined.

    Parses the output of `ip route show default`, e.g.:
      default via 192.168.4.1 dev wlan0 proto dhcp src 192.168.4.23 metric 303
    """
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        match = re.search(r"default via (\d{1,3}(?:\.\d{1,3}){3})", result.stdout)
        if match:
            return match.group(1)
    except Exception as exc:
        logger.debug("ip route failed: %s", exc)
    return None


def probe_api(ip: str, port: int = _API_PORT) -> bool:
    """
    Return True if the PZTrack API is responding at http://<ip>:<port>/.
    Uses a short timeout so we fail fast.
    """
    try:
        resp = requests.get(f"http://{ip}:{port}/", timeout=_DISCOVERY_TIMEOUT)
        # The root route returns "Hello, World!" — any 2xx is fine
        return resp.status_code < 500
    except requests.exceptions.RequestException:
        return False


def discover_gateway(
    api_port: int = _API_PORT,
    retry_interval: float = _RETRY_INTERVAL,
    max_retries: int | None = _MAX_RETRIES,
    display=None,
) -> str:
    """
    Block until a reachable PZTrack gateway is found and return its IP.

    Repeatedly:
      1. Gets the default gateway IP from the routing table.
      2. Probes the PZTrack API at that IP.
      3. If reachable, returns the IP.
      4. Otherwise waits retry_interval seconds and tries again.

    Parameters
    ----------
    api_port      : port the PZTrack Flask app listens on (default 5000)
    retry_interval: seconds between retries
    max_retries   : maximum attempts before raising RuntimeError
                    (None = retry forever)
    display       : optional DisplayManager — shows progress on OLED
    """
    attempt = 0
    while True:
        attempt += 1
        if max_retries is not None and attempt > max_retries:
            raise RuntimeError(
                f"Gateway not found after {max_retries} attempts."
            )

        gw_ip = get_default_gateway_ip()

        if gw_ip is None:
            logger.warning(
                "[%d] No default route found — network not up yet.", attempt
            )
            if display:
                display.show_error("No Network")
        else:
            logger.info(
                "[%d] Default gateway: %s — probing API on port %s...",
                attempt, gw_ip, api_port,
            )
            if display:
                display.show_connecting(gw_ip)

            if probe_api(gw_ip, api_port):
                logger.info("PZTrack gateway found at %s.", gw_ip)
                return gw_ip
            else:
                logger.warning(
                    "[%d] Gateway %s did not respond on port %s.",
                    attempt, gw_ip, api_port,
                )
                if display:
                    display.show_error("Gateway Unreachable")

        time.sleep(retry_interval)


def resolve(value: str, key: str, api_port: int = _API_PORT, display=None) -> str:
    """
    Resolve a config value that may be "auto".

    Parameters
    ----------
    value    : the raw config value, e.g. "auto" or "192.168.1.1"
    key      : "api_url" or "db_host" — determines what to return
    api_port : PZTrack API port (used for discovery probe)
    display  : optional DisplayManager

    Returns the resolved string ready for use.
    """
    if value != "auto":
        return value

    ip = discover_gateway(api_port=api_port, display=display)

    if key == "api_url":
        return f"http://{ip}:{api_port}"
    elif key == "db_host":
        return ip
    else:
        return ip
