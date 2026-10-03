import os

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from rules import DEFAULT_GRADES, judge_microstrain

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54398/bridgestrain"
)

# 表结构幂等：全新库直接建成，旧库则补列/补约束/补默认分档。
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS strain_readings (
    id serial PRIMARY KEY,
    span_code text NOT NULL,
    microstrain double precision NOT NULL,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);

ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS load_grade text;
ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS grade_lower double precision;
ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS grade_upper double precision;
ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS grade_snapshot_at timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_strain_readings_load_grade'
    ) THEN
        ALTER TABLE strain_readings
        ADD CONSTRAINT ck_strain_readings_load_grade
        CHECK (load_grade IS NULL OR load_grade IN ('light', 'heavy'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_strain_readings_status ON strain_readings (status, id);

CREATE TABLE IF NOT EXISTS load_grades (
    grade_key text PRIMARY KEY,
    grade_name text NOT NULL,
    lower_bound double precision NOT NULL,
    upper_bound double precision NOT NULL,
    sort_order int NOT NULL DEFAULT 0,
    updated_by text,
    updated_at timestamptz,
    CONSTRAINT ck_load_grades_bounds CHECK (lower_bound <= upper_bound)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_strain_readings_grade'
    ) THEN
        ALTER TABLE strain_readings
        ADD CONSTRAINT fk_strain_readings_grade
        FOREIGN KEY (load_grade) REFERENCES load_grades (grade_key);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS grade_change_log (
    id serial PRIMARY KEY,
    grade_key text NOT NULL,
    old_lower double precision NOT NULL,
    old_upper double precision NOT NULL,
    new_lower double precision NOT NULL,
    new_upper double precision NOT NULL,
    changed_by text NOT NULL,
    note text,
    changed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_grade_change_log_grade
    ON grade_change_log (grade_key, id DESC);
"""

# 旧库遗留读数没有等级：历史判定带就是轻载 80～220，回填以保持展示一致。
BACKFILL_SQL = """
UPDATE strain_readings
SET load_grade = 'light', grade_lower = 80, grade_upper = 220
WHERE load_grade IS NULL
"""

SEED_GRADES_SQL = """
INSERT INTO load_grades (grade_key, grade_name, lower_bound, upper_bound, sort_order, updated_by, updated_at)
VALUES (%s, %s, %s, %s, %s, 'system', now())
ON CONFLICT (grade_key) DO NOTHING
"""


async def create_pool() -> AsyncConnectionPool:
    pool = AsyncConnectionPool(
        conninfo=DSN,
        min_size=1,
        max_size=5,
        kwargs={"row_factory": dict_row},
        open=False,
    )
    await pool.open()
    return pool


async def _seed_grades(cur) -> None:
    for index, (key, name, lower, upper) in enumerate(DEFAULT_GRADES):
        await cur.execute(
            SEED_GRADES_SQL, (key, name, lower, upper, index)
        )


async def ensure_schema(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SCHEMA_SQL)
            await _seed_grades(cur)
            # 先播种分档，再回填旧读数，否则外键会拒绝。
            await cur.execute(BACKFILL_SQL)
        await conn.commit()


async def seed_if_empty(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) AS n FROM strain_readings")
            row = await cur.fetchone()
            if row["n"] > 0:
                return
            # 种子读数：轻载 150 合格；按轻载默认 80～220 判，40 越界。
            samples = [
                ("跨中S1", 150.0, "light", "轻载"),
                ("支座S2", 40.0, "light", "轻载"),
            ]
            for span_code, microstrain, grade_key, grade_name in samples:
                verdict, reason = judge_microstrain(
                    microstrain, 80.0, 220.0, grade_name
                )
                await cur.execute(
                    """
                    INSERT INTO strain_readings
                        (span_code, microstrain, verdict, reason, status,
                         created_by, processed_at, load_grade,
                         grade_lower, grade_upper, grade_snapshot_at)
                    VALUES (%s, %s, %s, %s, 'done', 'surveyor', now(),
                            %s, 80, 220, now())
                    """,
                    (span_code, microstrain, verdict, reason, grade_key),
                )
        await conn.commit()


def connect_sync():
    import psycopg

    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)
    for index, (key, name, lower, upper) in enumerate(DEFAULT_GRADES):
        conn.execute(
            SEED_GRADES_SQL, (key, name, lower, upper, index)
        )
    # 先播种分档，再回填旧读数，否则外键会拒绝。
    conn.execute(BACKFILL_SQL)


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM strain_readings").fetchone()
    if row["n"] > 0:
        return
    samples = [
        ("跨中S1", 150.0, "light", "轻载"),
        ("支座S2", 40.0, "light", "轻载"),
    ]
    for span_code, microstrain, grade_key, grade_name in samples:
        verdict, reason = judge_microstrain(
            microstrain, 80.0, 220.0, grade_name
        )
        conn.execute(
            """
            INSERT INTO strain_readings
                (span_code, microstrain, verdict, reason, status,
                 created_by, processed_at, load_grade,
                 grade_lower, grade_upper, grade_snapshot_at)
            VALUES (%s, %s, %s, %s, 'done', 'surveyor', now(),
                    %s, 80, 220, now())
            """,
            (span_code, microstrain, verdict, reason, grade_key),
        )
    conn.commit()
