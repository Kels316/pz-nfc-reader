#!/usr/bin/env python3
"""
register_tag.py — Associate physical NFC tags with PZTrack trackers.

What it does
------------
Scans an NFC tag with the PN532 and writes its UID into the
trackers.t_rfid column of the PZTrack PostgreSQL database.

Once a tag is registered, nfc_reader.py will recognise it immediately —
no config files to edit, no restart required.

Usage
-----
  # Interactive (picks tracker from a displayed list):
  python3 register_tag.py

  # Non-interactive (useful for scripted batch registration):
  python3 register_tag.py --dev-eui 6777570257507A01

  # List current registrations:
  python3 register_tag.py --list

  # Remove a tag from a tracker:
  python3 register_tag.py --remove --dev-eui 6777570257507A01
"""

import argparse
import json
import sys
import time
from pathlib import Path

_BASE_DIR = Path(__file__).parent
CONFIG_FILE = _BASE_DIR / "config.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(CONFIG_FILE) as fh:
        return json.load(fh)


def init_db(config: dict):
    from db_client import DBClient

    db_cfg = config["db"]
    db = DBClient(
        dbname=db_cfg["dbname"],
        user=db_cfg["user"],
        password=db_cfg.get("password"),
        host=db_cfg["host"],
        port=db_cfg.get("port", 5432),
    )
    db.connect()
    return db


def init_pn532(config: dict):
    import board
    import busio
    from adafruit_pn532.i2c import PN532_I2C

    i2c = busio.I2C(board.SCL, board.SDA)
    nfc_cfg = config.get("nfc", {})
    address = int(nfc_cfg.get("i2c_address", "0x24"), 16)
    pn532 = PN532_I2C(i2c, address=address, debug=False)
    ic, ver, rev, support = pn532.firmware_version
    print(f"  PN532 found — firmware v{ver}.{rev}")
    pn532.SAM_configuration()
    return pn532


def read_one_tag(pn532, timeout_secs: float = 30.0) -> bytes | None:
    deadline = time.monotonic() + timeout_secs
    while time.monotonic() < deadline:
        uid = pn532.read_passive_target(timeout=0.5)
        if uid is not None:
            return bytes(uid)
    return None


def pick_tracker(trackers: list[dict]) -> dict | None:
    """
    Print a numbered list of trackers and ask the operator to pick one.
    Returns the chosen tracker dict, or None on cancel.
    """
    print()
    print(f"  {'#':<4}  {'Competitor':<12}  {'Craft Name':<26}  {'DevEUI':<16}  Current Tag")
    print(f"  {'-'*4}  {'-'*12}  {'-'*26}  {'-'*16}  {'-'*20}")

    for i, t in enumerate(trackers, start=1):
        comp = t["competitorId"] or "—"
        craft = t["craftName"] or t["name"] or "—"
        deveui = (t["devEui"] or "").upper()
        rfid = t["rfid"].upper() if t["rfid"] else "(none)"
        print(f"  {i:<4}  {comp:<12}  {craft:<26}  {deveui:<16}  {rfid}")

    print()
    raw = input("Enter # to select a tracker (or 'q' to quit): ").strip()
    if raw.lower() == "q":
        return None
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(trackers):
            return trackers[idx]
    except ValueError:
        pass
    print("Invalid selection.")
    return None


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------

def cmd_list(db) -> None:
    trackers = db.get_all_trackers()
    print()
    print(f"  {'Competitor':<12}  {'Craft Name':<26}  {'DevEUI':<16}  NFC Tag UID")
    print(f"  {'-'*12}  {'-'*26}  {'-'*16}  {'-'*20}")
    any_registered = False
    for t in trackers:
        if t["rfid"] is None:
            continue
        any_registered = True
        comp  = t["competitorId"] or "—"
        craft = t["craftName"] or t["name"] or "—"
        deveui = (t["devEui"] or "").upper()
        print(f"  {comp:<12}  {craft:<26}  {deveui:<16}  {t['rfid'].upper()}")
    if not any_registered:
        print("  (no tags registered yet)")
    print()


def cmd_remove(db, dev_eui: str) -> None:
    dev_eui = dev_eui.replace(":", "").lower()
    ok = db.clear_tracker_rfid(dev_eui)
    if ok:
        print(f"Removed NFC tag from tracker {dev_eui.upper()}.")
    else:
        print(f"No tracker found with devEUI '{dev_eui.upper()}'.")
        sys.exit(1)


def cmd_register(db, pn532, dev_eui: str | None) -> None:
    trackers = db.get_all_trackers()
    if not trackers:
        print("No trackers found in the database.")
        sys.exit(1)

    # Select tracker
    if dev_eui:
        dev_eui = dev_eui.replace(":", "").lower()
        matches = [t for t in trackers if (t["devEui"] or "").lower() == dev_eui]
        if not matches:
            print(f"No tracker found with devEUI '{dev_eui.upper()}'.")
            sys.exit(1)
        tracker = matches[0]
        print(
            f"\nSelected: [{tracker['competitorId'] or '—'}]  "
            f"{tracker['craftName'] or tracker['name'] or '—'}"
            f"  ({dev_eui.upper()})"
        )
    else:
        print("\nAvailable trackers:")
        tracker = pick_tracker(trackers)
        if tracker is None:
            print("Cancelled.")
            return

    # Warn if this tracker already has a tag registered
    if tracker["rfid"]:
        answer = input(
            f"\nThis tracker already has tag '{tracker['rfid'].upper()}' registered.\n"
            "Replace it? [y/N]: "
        ).strip().lower()
        if answer != "y":
            print("Cancelled.")
            return

    print(
        f"\nHold the NFC tag for '{tracker['craftName'] or tracker['name']}' "
        "near the reader..."
    )
    print("(Waiting up to 30 seconds — Ctrl+C to cancel)\n")

    uid_bytes = read_one_tag(pn532)
    if uid_bytes is None:
        print("No tag detected within timeout.")
        sys.exit(1)

    uid_hex = uid_bytes.hex()
    print(f"Tag detected: {uid_hex.upper()}")

    # Check if this UID is already assigned to a different tracker
    existing = db.find_competitor_by_rfid(uid_bytes)
    if existing and existing["competitorId"] != tracker["competitorId"]:
        answer = input(
            f"\nThis tag is already registered to competitor "
            f"'{existing['competitorId']}' ({existing['craftName']}).\n"
            "Re-assign to this tracker? [y/N]: "
        ).strip().lower()
        if answer != "y":
            print("Cancelled.")
            return

    ok = db.set_tracker_rfid(tracker["devEui"], uid_bytes)
    if ok:
        comp  = tracker["competitorId"] or "—"
        craft = tracker["craftName"] or tracker["name"] or "—"
        print(f"\nRegistered: tag {uid_hex.upper()}  →  [{comp}] {craft}")
        print("nfc_reader.py will recognise this tag immediately.")
    else:
        print("ERROR: database update failed.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register NFC tags to PZTrack trackers (writes trackers.t_rfid)."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--list", "-l",
        action="store_true",
        help="List all current NFC tag registrations and exit.",
    )
    group.add_argument(
        "--remove", "-r",
        action="store_true",
        help="Remove the NFC tag from a tracker (requires --dev-eui).",
    )
    parser.add_argument(
        "--dev-eui", "-d",
        metavar="HEX",
        help="Target tracker DevEUI in hex (e.g. 6777570257507A01). "
             "If omitted, an interactive list is shown.",
    )
    args = parser.parse_args()

    print("PZTrack NFC Tag Registration")
    print("=" * 40)

    config = load_config()

    print("\nConnecting to database...")
    try:
        db = init_db(config)
    except Exception as exc:
        print(f"Database connection failed: {exc}")
        sys.exit(1)

    try:
        if args.list:
            cmd_list(db)
            return

        if args.remove:
            if not args.dev_eui:
                print("--remove requires --dev-eui <HEX>")
                sys.exit(1)
            cmd_remove(db, args.dev_eui)
            return

        # Registration — needs the PN532
        print("Initialising PN532...")
        try:
            pn532 = init_pn532(config)
        except Exception as exc:
            print(f"PN532 initialisation failed: {exc}")
            sys.exit(1)

        cmd_register(db, pn532, args.dev_eui)

    finally:
        db.close()


if __name__ == "__main__":
    main()
