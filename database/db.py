import os
import sqlite3
import threading
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
DAY_RESET_HOUR = int(os.getenv("DAY_RESET_HOUR", "4"))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_kst(dt: datetime) -> datetime:
    return dt.astimezone(KST)


def utc_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def to_dev_date(dt: datetime) -> str:
    """datetime을 개발일 기준 날짜(YYYY-MM-DD)로 변환. 자정~DAY_RESET_HOUR시는 전날로 취급."""
    kst = to_kst(dt) - timedelta(hours=DAY_RESET_HOUR)
    return kst.strftime("%Y-%m-%d")


def dev_day_start_utc(dt: datetime) -> datetime:
    """dt이 속하는 개발일의 시작 시각(UTC) 반환."""
    dev_date_str = to_dev_date(dt)
    day_start_kst = datetime.strptime(dev_date_str, "%Y-%m-%d").replace(tzinfo=KST) + timedelta(hours=DAY_RESET_HOUR)
    return day_start_kst.astimezone(timezone.utc)


def _month_utc_range(year_month: str) -> tuple[datetime, datetime]:
    """해당 월의 시작/끝 UTC 시각 반환 (개발일 기준, DAY_RESET_HOUR 적용)."""
    year, month = map(int, year_month.split("-"))
    start_kst = datetime(year, month, 1, DAY_RESET_HOUR, 0, 0, tzinfo=KST)
    if month == 12:
        end_kst = datetime(year + 1, 1, 1, DAY_RESET_HOUR, 0, 0, tzinfo=KST)
    else:
        end_kst = datetime(year, month + 1, 1, DAY_RESET_HOUR, 0, 0, tzinfo=KST)
    return start_kst.astimezone(timezone.utc), end_kst.astimezone(timezone.utc)


class DatabaseManager:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._create_tables()

    def _create_tables(self):
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     TEXT    NOT NULL,
                    guild_id    TEXT    NOT NULL,
                    join_time   TEXT    NOT NULL,
                    leave_time  TEXT,
                    duration    INTEGER
                );

                CREATE TABLE IF NOT EXISTS dev_days (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     TEXT    NOT NULL,
                    guild_id    TEXT    NOT NULL,
                    date        TEXT    NOT NULL,
                    total_secs  INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(user_id, guild_id, date)
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_guild_join
                    ON sessions (guild_id, join_time);
                CREATE INDEX IF NOT EXISTS idx_sessions_user_guild
                    ON sessions (user_id, guild_id, join_time);
                CREATE INDEX IF NOT EXISTS idx_dev_days_user_guild
                    ON dev_days (user_id, guild_id, date);
            """)
            self._conn.commit()

    # ── 세션 관련 ──────────────────────────────────────────

    def open_session(self, user_id: str, guild_id: str, join_time: datetime) -> int:
        """음성채널 입장 시 세션 열기. 생성된 session id 반환."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO sessions (user_id, guild_id, join_time) VALUES (?, ?, ?)",
                (user_id, guild_id, utc_str(join_time)),
            )
            self._conn.commit()
            return cur.lastrowid

    def close_session(self, session_id: int, leave_time: datetime) -> int:
        """음성채널 퇴장 시 세션 닫기. duration(초) 반환."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT join_time FROM sessions WHERE id = ?",
                (session_id,),
            )
            row = cur.fetchone()
            if row is None:
                return 0
            join_time = parse_utc(row[0])
            duration = int((leave_time - join_time).total_seconds())
            cur.execute(
                "UPDATE sessions SET leave_time = ?, duration = ? WHERE id = ?",
                (utc_str(leave_time), duration, session_id),
            )
            self._conn.commit()
            return duration

    def close_orphan_sessions(self, guild_id: str) -> list[dict]:
        """leave_time이 NULL인 좀비 세션 목록 반환 (봇 재시작 복구용)."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT id, user_id, join_time FROM sessions "
                "WHERE guild_id = ? AND leave_time IS NULL",
                (guild_id,),
            )
            rows = cur.fetchall()
        return [{"session_id": r[0], "user_id": r[1], "join_time": parse_utc(r[2])} for r in rows]

    def get_session_join_time(self, session_id: int):
        """진행 중인 세션의 join_time 반환."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT join_time FROM sessions WHERE id = ?", (session_id,))
            row = cur.fetchone()
            return parse_utc(row[0]) if row else None

    def get_session_join_times_batch(self, session_ids: list[int]) -> dict[int, datetime]:
        """여러 세션의 join_time을 한 번에 반환."""
        if not session_ids:
            return {}
        placeholders = ",".join("?" * len(session_ids))
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(f"SELECT id, join_time FROM sessions WHERE id IN ({placeholders})", session_ids)
            return {r[0]: parse_utc(r[1]) for r in cur.fetchall()}

    def get_day_total_secs(self, user_id: str, guild_id: str, date_kst: str) -> int:
        """특정 날짜(개발일 기준, YYYY-MM-DD)에 실제로 체류한 시간(초). DAY_RESET_HOUR~익일 DAY_RESET_HOUR 구간 계산."""
        day_start = datetime.strptime(date_kst, "%Y-%m-%d").replace(tzinfo=KST) + timedelta(hours=DAY_RESET_HOUR)
        day_end = day_start + timedelta(days=1)

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT join_time, leave_time
                FROM sessions
                WHERE user_id = ? AND guild_id = ?
                  AND duration IS NOT NULL
                  AND join_time < ? AND leave_time >= ?
                """,
                (user_id, guild_id, utc_str(day_end), utc_str(day_start)),
            )
            rows = cur.fetchall()

        total = 0
        for row in rows:
            join = parse_utc(row[0])
            leave = parse_utc(row[1])
            effective_start = max(join, day_start)
            effective_end = min(leave, day_end)
            total += int((effective_end - effective_start).total_seconds())
        return total

    def get_day_total_secs_batch(self, user_ids: list[str], guild_id: str, date_kst: str) -> dict[str, int]:
        """여러 유저의 특정 날짜 체류 시간(초)을 한 번에 반환."""
        if not user_ids:
            return {}
        day_start = datetime.strptime(date_kst, "%Y-%m-%d").replace(tzinfo=KST) + timedelta(hours=DAY_RESET_HOUR)
        day_end = day_start + timedelta(days=1)
        placeholders = ",".join("?" * len(user_ids))
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"""
                SELECT user_id, join_time, leave_time
                FROM sessions
                WHERE guild_id = ? AND user_id IN ({placeholders})
                  AND duration IS NOT NULL
                  AND join_time < ? AND leave_time >= ?
                """,
                (guild_id, *user_ids, utc_str(day_end), utc_str(day_start)),
            )
            rows = cur.fetchall()
        totals: dict[str, int] = {uid: 0 for uid in user_ids}
        for uid, join_str, leave_str in rows:
            join = parse_utc(join_str)
            leave = parse_utc(leave_str)
            effective_start = max(join, day_start)
            effective_end = min(leave, day_end)
            totals[uid] = totals.get(uid, 0) + int((effective_end - effective_start).total_seconds())
        return totals

    # ── 개발일 관련 ────────────────────────────────────────

    def upsert_dev_day(self, user_id: str, guild_id: str, date_kst: str, total_secs: int):
        """개발일 확정/업데이트."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO dev_days (user_id, guild_id, date, total_secs)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, guild_id, date)
                DO UPDATE SET total_secs = excluded.total_secs
                """,
                (user_id, guild_id, date_kst, total_secs),
            )
            self._conn.commit()

    def get_consecutive_days(self, user_id: str, guild_id: str, today_kst: str, include_today: bool = False) -> int:
        """오늘 기준 연속 개발일 수. include_today=True면 오늘 기록 없어도 오늘을 포함."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT date FROM dev_days WHERE user_id = ? AND guild_id = ? ORDER BY date DESC",
                (user_id, guild_id),
            )
            rows = cur.fetchall()

        today = datetime.strptime(today_kst, "%Y-%m-%d").date()
        dates = [datetime.strptime(r[0], "%Y-%m-%d").date() for r in rows]

        if include_today and (not dates or dates[0] != today):
            dates.insert(0, today)

        if not dates:
            return 0

        if dates[0] < today - timedelta(days=1):
            return 0

        count = 0
        expected = dates[0]
        for d in dates:
            if d == expected:
                count += 1
                expected -= timedelta(days=1)
            else:
                break
        return count

    def get_max_streak(self, user_id: str, guild_id: str) -> int:
        """역대 최대 연속 개발일 수."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT date FROM dev_days WHERE user_id = ? AND guild_id = ? ORDER BY date ASC",
                (user_id, guild_id),
            )
            rows = cur.fetchall()

        if not rows:
            return 0

        dates = [datetime.strptime(r[0], "%Y-%m-%d").date() for r in rows]
        max_streak = 1
        current = 1
        for i in range(1, len(dates)):
            if dates[i] == dates[i - 1] + timedelta(days=1):
                current += 1
                if current > max_streak:
                    max_streak = current
            else:
                current = 1
        return max_streak

    def get_monthly_days(self, user_id: str, guild_id: str, year_month: str) -> int:
        """해당 월(YYYY-MM)의 개발 일수."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM dev_days WHERE user_id = ? AND guild_id = ? AND date LIKE ?",
                (user_id, guild_id, f"{year_month}-%"),
            )
            return cur.fetchone()[0]

    def get_monthly_secs(self, user_id: str, guild_id: str, year_month: str) -> int:
        """해당 월(YYYY-MM)의 총 개발 시간(초)."""
        month_start, month_end = _month_utc_range(year_month)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT COALESCE(SUM(duration), 0)
                FROM sessions
                WHERE user_id = ? AND guild_id = ?
                  AND duration IS NOT NULL
                  AND join_time >= ? AND join_time < ?
                """,
                (user_id, guild_id, utc_str(month_start), utc_str(month_end)),
            )
            return cur.fetchone()[0]

    def get_monthly_dev_dates(self, user_id: str, guild_id: str, year_month: str) -> list[str]:
        """해당 월의 개발일 날짜 목록(YYYY-MM-DD)."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT date FROM dev_days WHERE user_id = ? AND guild_id = ? AND date LIKE ? ORDER BY date",
                (user_id, guild_id, f"{year_month}-%"),
            )
            return [r[0] for r in cur.fetchall()]

    def get_monthly_dev_dates_batch(self, user_ids: list[str], guild_id: str, year_month: str) -> dict[str, set[str]]:
        """여러 유저의 해당 월 개발일 목록을 한 번에 반환."""
        if not user_ids:
            return {}
        placeholders = ",".join("?" * len(user_ids))
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"SELECT user_id, date FROM dev_days WHERE guild_id = ? AND user_id IN ({placeholders}) AND date LIKE ?",
                (guild_id, *user_ids, f"{year_month}-%"),
            )
            rows = cur.fetchall()
        result: dict[str, set[str]] = {uid: set() for uid in user_ids}
        for uid, date in rows:
            result[uid].add(date)
        return result

    def get_monthly_stats_batch(self, user_ids: list[str], guild_id: str, year_month: str) -> dict[str, dict]:
        """여러 유저의 해당 월 개발일 수 및 개발시간(초)을 한 번에 반환."""
        if not user_ids:
            return {}
        placeholders = ",".join("?" * len(user_ids))
        month_start, month_end = _month_utc_range(year_month)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"SELECT user_id, COUNT(*) FROM dev_days WHERE guild_id = ? AND user_id IN ({placeholders}) AND date LIKE ? GROUP BY user_id",
                (guild_id, *user_ids, f"{year_month}-%"),
            )
            days_map = {r[0]: r[1] for r in cur.fetchall()}
            cur.execute(
                f"""
                SELECT user_id, COALESCE(SUM(duration), 0)
                FROM sessions
                WHERE guild_id = ? AND user_id IN ({placeholders})
                  AND duration IS NOT NULL
                  AND join_time >= ? AND join_time < ?
                GROUP BY user_id
                """,
                (guild_id, *user_ids, utc_str(month_start), utc_str(month_end)),
            )
            secs_map = {r[0]: r[1] for r in cur.fetchall()}
        return {uid: {"days": days_map.get(uid, 0), "secs": secs_map.get(uid, 0)} for uid in user_ids}

    def get_monthly_ranking(self, guild_id: str, year_month: str, min_dev_secs: int) -> list[dict]:
        """이번 달 개발 시간 기준 서버 랭킹. 하루 누적 min_dev_secs 이상인 날만 개발일로 집계."""
        month_start, month_end = _month_utc_range(year_month)
        offset = 9 - DAY_RESET_HOUR
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"""
                SELECT user_id,
                       SUM(CASE WHEN day_total >= ? THEN 1 ELSE 0 END) AS days,
                       SUM(day_total) AS secs
                FROM (
                    SELECT user_id,
                           date(datetime(join_time, '+{offset} hours')) AS day_date,
                           SUM(duration) AS day_total
                    FROM sessions
                    WHERE guild_id = ? AND duration IS NOT NULL
                      AND join_time >= ? AND join_time < ?
                    GROUP BY user_id, day_date
                )
                GROUP BY user_id
                ORDER BY secs DESC
                """,
                (min_dev_secs, guild_id, utc_str(month_start), utc_str(month_end)),
            )
            return [{"user_id": r[0], "days": r[1], "secs": r[2]} for r in cur.fetchall()]

    def close(self):
        self._conn.close()
