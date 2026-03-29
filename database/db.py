import sqlite3
import threading
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_kst(dt: datetime) -> datetime:
    return dt.astimezone(KST)


def utc_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


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

    def get_day_total_secs(self, user_id: str, guild_id: str, date_kst: str) -> int:
        """특정 날짜(KST, YYYY-MM-DD)의 세션 duration 합계(초)."""
        with self._lock:
            cur = self._conn.cursor()
            # join_time을 KST로 변환하여 날짜 비교
            cur.execute(
                """
                SELECT COALESCE(SUM(duration), 0)
                FROM sessions
                WHERE user_id = ? AND guild_id = ?
                  AND duration IS NOT NULL
                  AND date(datetime(join_time, '+9 hours')) = ?
                """,
                (user_id, guild_id, date_kst),
            )
            return cur.fetchone()[0]

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

        # 오늘이 아직 dev_days에 없고 include_today=True면 오늘을 목록 맨 앞에 추가
        if include_today and (not dates or dates[0] != today):
            dates.insert(0, today)

        if not dates:
            return 0

        # 오늘 또는 어제부터 시작하지 않으면 연속 없음
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
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT COALESCE(SUM(total_secs), 0) FROM dev_days "
                "WHERE user_id = ? AND guild_id = ? AND date LIKE ?",
                (user_id, guild_id, f"{year_month}-%"),
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

    def get_monthly_ranking(self, guild_id: str, year_month: str) -> list[dict]:
        """이번 달 개발 시간 기준 서버 랭킹."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT user_id, COUNT(*) as days, SUM(total_secs) as secs
                FROM dev_days
                WHERE guild_id = ? AND date LIKE ?
                GROUP BY user_id
                ORDER BY secs DESC
                LIMIT 5
                """,
                (guild_id, f"{year_month}-%"),
            )
            return [{"user_id": r[0], "days": r[1], "secs": r[2]} for r in cur.fetchall()]

    def close(self):
        self._conn.close()
