"""События (статистика без персональных данных) и журнал изменений кабинета."""

from __future__ import annotations

from typing import Optional

from ..db import Database, iso, now
from .common import _j, _loads, session_ref


class EventRepo:
    def __init__(self, db: Database):
        self.db = db

    def log(self, scenario: str, category: str = "", status: str = "", session: str = "", flags: Optional[dict] = None) -> None:
        """Только обезличенное: время, хэш сессии, тема, сценарий, статус. Ни текста вопросов, ни OCR, ни персональных данных."""
        self.db.execute("INSERT INTO events(ts, session_ref, category, scenario, status, flags) VALUES(?,?,?,?,?,?)",
                        (now(), session_ref(session) if session else None, category or None, scenario, status or None, _j(flags) if flags else None))

    def purge_older_than(self, days: float) -> int:
        return self.db.execute("DELETE FROM events WHERE ts<?", (now() - days * 86400,)).rowcount

    def stats(self, days: int = 30) -> dict:
        since = now() - days * 86400
        day_expr = "strftime('%Y-%m-%d', ts, 'unixepoch')"

        def count(scenario: str, status: Optional[str] = None) -> int:
            if status:
                return int(self.db.one("SELECT COUNT(*) AS n FROM events WHERE ts>=? AND scenario=? AND status=?", (since, scenario, status))["n"])
            return int(self.db.one("SELECT COUNT(*) AS n FROM events WHERE ts>=? AND scenario=?", (since, scenario))["n"])

        by_day: dict[str, dict] = {}
        for r in self.db.query(f"SELECT {day_expr} AS day, scenario, COUNT(*) AS n FROM events WHERE ts>=? GROUP BY day, scenario", (since,)):
            by_day.setdefault(r["day"], {"day": r["day"]})[r["scenario"]] = r["n"]
        topics = [{"topic": r["category"], "count": r["n"]} for r in self.db.query(
            "SELECT category, COUNT(*) AS n FROM events WHERE ts>=? AND category IS NOT NULL AND scenario IN ('question','document_check') "
            "GROUP BY category ORDER BY n DESC, category LIMIT 20", (since,))]
        statuses = {r["status"]: r["n"] for r in self.db.query(
            "SELECT status, COUNT(*) AS n FROM events WHERE ts>=? AND scenario IN ('question','document_check') AND status IS NOT NULL GROUP BY status", (since,))}
        unique_sessions = int(self.db.one("SELECT COUNT(DISTINCT session_ref) AS n FROM events WHERE ts>=? AND session_ref IS NOT NULL", (since,))["n"])
        return {
            "period_days": days,
            "totals": {
                "visits": count("visit"),
                "sessions": unique_sessions,
                "questions": count("question"),
                "document_checks": count("document_check"),
                "screenings_started": count("screening_started"),
                "screenings_transfer": count("screening_started", "transfer"),
                "screenings_enrollment": count("screening_started", "enrollment"),
                "documents_generated": count("document_generated"),
                "tickets_sent": count("ticket", "sent"),
                "tickets_declined": count("ticket", "declined"),
            },
            "topics": topics,
            "statuses": statuses,
            "by_day": [by_day[d] for d in sorted(by_day)],
        }


# ====================================================================== журнал изменений
class AuditRepo:
    def __init__(self, db: Database):
        self.db = db

    def log(self, actor: str, action: str, object_type: str, object_id: str = "", details: Optional[dict] = None) -> None:
        self.db.execute("INSERT INTO audit_log(ts, actor, action, object_type, object_id, details_json) VALUES(?,?,?,?,?,?)",
                        (now(), actor, action, object_type, object_id, _j(details) if details else None))

    def list(self, object_type: Optional[str] = None, object_id: Optional[str] = None, limit: int = 100, offset: int = 0) -> list[dict]:
        where, params = ["1=1"], []
        if object_type:
            where.append("object_type=?")
            params.append(object_type)
        if object_id:
            where.append("object_id=?")
            params.append(object_id)
        rows = self.db.query(f"SELECT * FROM audit_log WHERE {' AND '.join(where)} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?", [*params, max(1, min(500, limit)), max(0, offset)])
        return [{"id": r["id"], "ts": iso(r["ts"]), "actor": r["actor"], "action": r["action"], "object_type": r["object_type"],
                 "object_id": r["object_id"], "details": _loads(r["details_json"], {})} for r in rows]
