"""后台工人：用 SKIP LOCKED 认领 pending 应变读数。

认领瞬间锁定该读数对应的荷载等级行，把当时的闭区间上下限抄进单据快照，
随后按快照判定——改档只影响之后认领的新单，处理中的单沿用领取瞬间那一档。
"""

import os
import time

from db import connect_sync, ensure_schema_sync, seed_if_empty_sync
from rules import judge_microstrain

POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "1.0"))


def claim_one(conn):
    with conn.transaction():
        row = conn.execute(
            """
            SELECT r.id, r.microstrain, r.load_grade
            FROM strain_readings r
            WHERE r.status = 'pending'
            ORDER BY r.id
            FOR UPDATE OF r SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        # 锁定等级行：与测量员改档串行化，锁拿到手的那一档就是“领取瞬间”的档。
        grade = conn.execute(
            """
            SELECT grade_name, lower_bound, upper_bound
            FROM load_grades
            WHERE grade_code = %s
            FOR UPDATE
            """,
            (row["load_grade"],),
        ).fetchone()
        conn.execute(
            """
            UPDATE strain_readings
            SET status = 'processing',
                grade_lower = %s,
                grade_upper = %s
            WHERE id = %s
            """,
            (grade["lower_bound"], grade["upper_bound"], row["id"]),
        )
        return {
            "id": row["id"],
            "microstrain": float(row["microstrain"]),
            "grade_name": grade["grade_name"],
            "lower_bound": float(grade["lower_bound"]),
            "upper_bound": float(grade["upper_bound"]),
        }


def finish(conn, claimed: dict) -> None:
    verdict, reason = judge_microstrain(
        claimed["microstrain"],
        claimed["lower_bound"],
        claimed["upper_bound"],
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
