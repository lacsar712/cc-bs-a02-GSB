"""后台工人：用 SKIP LOCKED 认领 pending 应变读数并写入合格/越界结论。

判定带按荷载等级分档。领取（claim）的瞬间在同一事务里把该等级现行
闭区间抄到读数上（grade_lower/grade_upper）；finish 一律吃抄档，
所以分档随后被测量员修改，也不影响已被领走、尚在处理中的单。
"""

import os
import time

from db import connect_sync, ensure_schema_sync, seed_if_empty_sync
from rules import judge_microstrain

POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "1.0"))


def claim_one(conn):
    with conn.transaction():
        # 先锁读数行；再锁该等级分档行，保证抄下的 lower/upper 是
        # 领取瞬间的现行值（与并发改档互斥）。
        row = conn.execute(
            """
            SELECT r.id, r.microstrain, r.load_grade,
                   g.grade_name, g.lower_bound, g.upper_bound
            FROM strain_readings r
            JOIN load_grades g ON g.grade_key = r.load_grade
            WHERE r.status = 'pending'
            ORDER BY r.id
            FOR UPDATE OF r SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        grade = conn.execute(
            """
            SELECT grade_name, lower_bound, upper_bound
            FROM load_grades
            WHERE grade_key = %s
            FOR UPDATE
            """,
            (row["load_grade"],),
        ).fetchone()
        conn.execute(
            """
            UPDATE strain_readings
            SET status = 'processing',
                grade_lower = %s,
                grade_upper = %s,
                grade_snapshot_at = now()
            WHERE id = %s
            """,
            (grade["lower_bound"], grade["upper_bound"], row["id"]),
        )
        return {
            "id": row["id"],
            "microstrain": row["microstrain"],
            "load_grade": row["load_grade"],
            "grade_name": grade["grade_name"],
            "grade_lower": grade["lower_bound"],
            "grade_upper": grade["upper_bound"],
        }


def finish(conn, claimed: dict) -> None:
    verdict, reason = judge_microstrain(
        float(claimed["microstrain"]),
        float(claimed["grade_lower"]),
        float(claimed["grade_upper"]),
        claimed["grade_name"],
    )
    conn.execute(
        """
        UPDATE strain_readings
        SET status = 'done', verdict = %s, reason = %s, processed_at = now()
        WHERE id = %s
        """,
        (verdict, reason, claimed["id"]),
    )
    conn.commit()


def run_once(conn) -> bool:
    claimed = claim_one(conn)
    if not claimed:
        return False
    try:
        finish(conn, claimed)
    except Exception:
        conn.execute(
            "UPDATE strain_readings SET status = 'pending' WHERE id = %s",
            (claimed["id"],),
        )
        conn.commit()
        raise
    return True


def main() -> None:
    with connect_sync() as conn:
        ensure_schema_sync(conn)
        seed_if_empty_sync(conn)
        conn.commit()

    while True:
        try:
            with connect_sync() as conn:
                processed = run_once(conn)
        except Exception as exc:
            print(f"worker error: {exc}", flush=True)
            processed = False
        if not processed:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
