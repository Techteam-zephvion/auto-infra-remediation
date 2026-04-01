"""
database.py — PostgreSQL audit trail for auto-infra-remediation.

Every remediation workflow execution is written to the audit_events table.
Records are immutable once written — updates only change the status/result
columns via workflow_id, never delete rows.

If DATABASE_URL is not set, all operations are no-ops and the in-memory
history in api.py continues to work as a fallback.
"""

import os
import logging
import time
from datetime import datetime
from typing import Optional

from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

_DATABASE_URL = os.getenv("DATABASE_URL", "")


async def _connect():
    import asyncpg
    return await asyncpg.connect(_DATABASE_URL)


async def setup_audit_table() -> None:
    """Create the audit_events table if it does not exist. Call at startup."""
    if not _DATABASE_URL:
        logger.info("[DB] DATABASE_URL not set — audit trail running in-memory only")
        return

    try:
        conn = await _connect()
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id               SERIAL PRIMARY KEY,
                workflow_id      VARCHAR(50)   NOT NULL UNIQUE,
                alert_type       VARCHAR(100),
                status           VARCHAR(20)   NOT NULL DEFAULT 'running',
                analysis         TEXT,
                script           TEXT,
                safety_approved  BOOLEAN,
                safety_reasoning TEXT,
                execution_result TEXT,
                duration_seconds FLOAT,
                started_at       TIMESTAMPTZ   NOT NULL,
                completed_at     TIMESTAMPTZ,
                created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
            )
        """)
        await conn.close()
        logger.info("[DB] audit_events table ready")
    except Exception as e:
        logger.error(f"[DB] Failed to create audit table: {e}")


async def insert_audit_event(record: dict) -> None:
    """Insert a new workflow record (status = 'running')."""
    if not _DATABASE_URL:
        return

    with tracer.start_as_current_span("db.insert_audit_event") as span:
        span.set_attribute("db.operation", "INSERT")
        span.set_attribute("db.table", "audit_events")
        span.set_attribute("db.workflow_id", record["id"])
        
        start_time = time.time()
        try:
            conn = await _connect()
            await conn.execute(
                """
                INSERT INTO audit_events
                    (workflow_id, alert_type, status, started_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (workflow_id) DO NOTHING
                """,
                record["id"],
                record.get("alert_type", "custom"),
                "running",
                datetime.fromisoformat(record["timestamp"]),
            )
            await conn.close()
            
            duration_ms = (time.time() - start_time) * 1000
            span.set_attribute("db.duration_ms", round(duration_ms, 2))
            logger.info(f"[DB] insert_audit_event: {record['id']} - {duration_ms:.2f}ms")
            
            if duration_ms > 500:
                logger.warning(f"[DB] SLOW QUERY: insert_audit_event took {duration_ms:.2f}ms")
                span.set_attribute("db.slow_query", True)
                
        except Exception as e:
            span.set_attribute("db.error", str(e))
            logger.error(f"[DB] insert_audit_event failed: {e}")
            raise


async def update_audit_event(record: dict) -> None:
    """Update an existing workflow record with final state."""
    if not _DATABASE_URL:
        return

    with tracer.start_as_current_span("db.update_audit_event") as span:
        span.set_attribute("db.operation", "UPDATE")
        span.set_attribute("db.table", "audit_events")
        span.set_attribute("db.workflow_id", record["id"])
        span.set_attribute("db.status", record.get("status", "completed"))
        
        start_time = time.time()
        try:
            conn = await _connect()
            result = await conn.execute(
                """
                UPDATE audit_events SET
                    status           = $2,
                    analysis         = $3,
                    script           = $4,
                    safety_approved  = $5,
                    safety_reasoning = $6,
                    execution_result = $7,
                    duration_seconds = $8,
                    completed_at     = $9
                WHERE workflow_id = $1
                """,
                record["id"],
                record.get("status", "completed"),
                record.get("analysis"),
                record.get("script"),
                record.get("safety_approved"),
                record.get("safety_reasoning"),
                record.get("execution_result"),
                record.get("duration_seconds"),
                datetime.utcnow(),
            )
            await conn.close()
            
            duration_ms = (time.time() - start_time) * 1000
            span.set_attribute("db.duration_ms", round(duration_ms, 2))
            span.set_attribute("db.rows_affected", 1)  # UPDATE affects 1 row
            logger.info(f"[DB] update_audit_event: {record['id']} - {record.get('status', 'unknown')} - {duration_ms:.2f}ms")
            
            if duration_ms > 500:
                logger.warning(f"[DB] SLOW QUERY: update_audit_event took {duration_ms:.2f}ms")
                span.set_attribute("db.slow_query", True)
                
        except Exception as e:
            span.set_attribute("db.error", str(e))
            logger.error(f"[DB] update_audit_event failed: {e}")
            raise


async def fetch_audit_events(limit: int = 50) -> Optional[list]:
    """Fetch recent audit events. Returns None if DB is not configured."""
    if not _DATABASE_URL:
        return None

    with tracer.start_as_current_span("db.fetch_audit_events") as span:
        span.set_attribute("db.operation", "SELECT")
        span.set_attribute("db.table", "audit_events")
        span.set_attribute("db.limit", limit)
        
        start_time = time.time()
        try:
            conn = await _connect()
            rows = await conn.fetch(
                """
                SELECT workflow_id, alert_type, status, analysis, script,
                       safety_approved, safety_reasoning, execution_result,
                       duration_seconds, started_at, completed_at
                FROM audit_events
                ORDER BY started_at DESC
                LIMIT $1
                """,
                limit,
            )
            await conn.close()
            
            duration_ms = (time.time() - start_time) * 1000
            rows_fetched = len(rows)
            
            span.set_attribute("db.duration_ms", round(duration_ms, 2))
            span.set_attribute("db.rows_fetched", rows_fetched)
            
            if duration_ms > 500:
                logger.warning(f"[DB] SLOW QUERY: fetch_audit_events took {duration_ms:.2f}ms (returned {rows_fetched} rows)")
                span.set_attribute("db.slow_query", True)
            
            return [dict(r) for r in rows]
            
        except Exception as e:
            span.set_attribute("db.error", str(e))
            logger.error(f"[DB] fetch_audit_events failed: {e}")
            return None
