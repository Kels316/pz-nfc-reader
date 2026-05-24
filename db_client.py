#!/usr/bin/env python3
"""
db_client.py — Direct PostgreSQL access for the PZTrack NFC reader module.

Responsibilities
----------------
  1. Look up a competitor by NFC tag UID via trackers.t_rfid             (nfc_reader.py)
  2. Write an NFC tag UID into trackers.t_rfid during registration        (register_tag.py)
  3. List all trackers + linked competitors for the registration UI       (register_tag.py)

This module connects to the SAME PostgreSQL database as the existing
phasezero-tracker-api-server but never modifies any tables other than
setting trackers.t_rfid — the field specifically intended for this purpose.

The existing server code is not changed in any way.

Schema notes (from apidb.sql)
------------------------------
  trackers.t_rfid       BYTEA   — NFC/RFID tag UID for the physical tracker
  trackers.t_deveui     BYTEA   — LoRa DevEUI (primary key)
  competitors.tracker_id INTEGER — corresponds to bytes 6-7 of t_deveui
                                   (same logic used by the existing server)
  latest_checkins_vw            — view of most-recent checkin_state per competitor
"""

import logging

import psycopg2
import psycopg2.extensions

logger = logging.getLogger(__name__)


class DBClient:
    """Thin wrapper around psycopg2 for the NFC reader's database needs."""

    def __init__(
        self,
        dbname: str,
        user: str,
        password: str | None,
        host: str,
        port: int = 5432,
    ):
        self._cfg = dict(
            dbname=dbname,
            user=user,
            password=password,
            host=host,
            port=port,
        )
        self._conn: psycopg2.extensions.connection | None = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open (or re-open) the database connection."""
        self._conn = psycopg2.connect(**self._cfg)
        self._conn.autocommit = True
        logger.info(
            "Connected to PostgreSQL database '%s' on %s:%s.",
            self._cfg["dbname"],
            self._cfg["host"],
            self._cfg["port"],
        )

    def _cursor(self) -> psycopg2.extensions.cursor:
        """Return a cursor, reconnecting automatically if the connection dropped."""
        try:
            # Cheap liveness check
            cur = self._conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
        except Exception:
            logger.warning("Database connection lost — reconnecting.")
            self.connect()
        return self._conn.cursor()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.debug("Database connection closed.")

    # ------------------------------------------------------------------
    # NFC reader — tag lookup
    # ------------------------------------------------------------------

    def find_competitor_by_rfid(self, uid_bytes: bytes) -> dict | None:
        """
        Return competitor + current checkin state for the tracker whose
        t_rfid matches uid_bytes.  Returns None if no match is found.

        Join path mirrors the existing server:
          trackers  →  competitors  (via bytes 6-7 of t_deveui = tracker_id)
                    →  latest_checkins_vw  (current state)
        """
        cur = self._cursor()
        cur.execute(
            """
            SELECT
                c.competitor_id,
                c.craft_name,
                c.team_name,
                lc.checkin_state,
                lc.state_change_timestamp AT TIME ZONE 'GMT'
            FROM trackers t
            INNER JOIN competitors c
                ON (  get_byte(t.t_deveui, 6) * 256
                    + get_byte(t.t_deveui, 7)
                   ) = c.tracker_id
            LEFT JOIN latest_checkins_vw lc
                ON lc.competitor_id = c.competitor_id
            WHERE t.t_rfid = %s
            LIMIT 1;
            """,
            (uid_bytes,),
        )
        row = cur.fetchone()
        cur.close()

        if row is None:
            return None

        return {
            "competitorId": str(row[0]),
            "craftName":    row[1] or "",
            "teamName":     row[2] or "",
            "checkinState": row[3],       # "checked_in", "checked_out", or None
            "checkinStateChangeTimestamp": row[4].isoformat() if row[4] else None,
        }

    # ------------------------------------------------------------------
    # Tag registration — list / write
    # ------------------------------------------------------------------

    def get_all_trackers(self) -> list[dict]:
        """
        Return every tracker row together with its linked competitor (if any).
        Used by register_tag.py to let the operator pick a tracker.

        Sorted by competitor_id ASC (unlinked trackers last), then tracker name.
        """
        cur = self._cursor()
        cur.execute(
            """
            SELECT
                t.t_deveui,
                t.t_name,
                t.t_rfid,
                c.competitor_id,
                c.craft_name,
                c.team_name
            FROM trackers t
            LEFT JOIN competitors c
                ON (  get_byte(t.t_deveui, 6) * 256
                    + get_byte(t.t_deveui, 7)
                   ) = c.tracker_id
            ORDER BY c.competitor_id ASC NULLS LAST, t.t_name ASC;
            """
        )
        rows = cur.fetchall()
        cur.close()

        result = []
        for row in rows:
            result.append(
                {
                    "devEui":       row[0].tobytes().hex() if row[0] else None,
                    "name":         row[1] or "",
                    "rfid":         row[2].tobytes().hex() if row[2] else None,
                    "competitorId": str(row[3]) if row[3] is not None else None,
                    "craftName":    row[4] or "",
                    "teamName":     row[5] or "",
                }
            )
        return result

    def set_tracker_rfid(self, dev_eui_hex: str, uid_bytes: bytes) -> bool:
        """
        Write uid_bytes into trackers.t_rfid for the tracker identified by
        dev_eui_hex.  Also bumps t_db_timestamp so the existing sync logic
        can detect the change.

        Returns True if a row was updated, False if the devEUI was not found.
        """
        cur = self._cursor()
        cur.execute(
            """
            UPDATE trackers
               SET t_rfid         = %s,
                   t_db_timestamp = NOW()
             WHERE t_deveui = decode(%s, 'hex')::BYTEA;
            """,
            (uid_bytes, dev_eui_hex),
        )
        updated = cur.rowcount
        cur.close()

        if updated:
            logger.info(
                "Set t_rfid = %s for tracker %s.",
                uid_bytes.hex().upper(),
                dev_eui_hex,
            )
        else:
            logger.warning("No tracker found with devEUI '%s'.", dev_eui_hex)

        return updated > 0

    def clear_tracker_rfid(self, dev_eui_hex: str) -> bool:
        """Remove the t_rfid association from a tracker (sets it to NULL)."""
        cur = self._cursor()
        cur.execute(
            """
            UPDATE trackers
               SET t_rfid         = NULL,
                   t_db_timestamp = NOW()
             WHERE t_deveui = decode(%s, 'hex')::BYTEA;
            """,
            (dev_eui_hex,),
        )
        updated = cur.rowcount
        cur.close()
        return updated > 0
