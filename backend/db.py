import os

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from rules import judge_microstrain

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54398/bridgestrain"
)

# 荷载等级代码 -> (中文名, 初始下限, 初始上限)
DEFAULT_GRADES = [
    ("light", "轻载", 80.0, 220.0),
    ("heavy", "重载", 100.0, 300.0),
]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS load_grades (
    grade_code  text PRIMARY KEY,
    grade_name  text NOT NULL,
    lower_bound double precision NOT NULL,
    upper_bound double precision NOT NULL,
    updated_by  text,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (lower_bound < upper_bound)
);

CREATE TABLE IF NOT EXISTS grade_changes (
    id           serial PRIMARY KEY,
    grade_code   text NOT NULL REFERENCES load_grades (grade_code),
    grade_name   text NOT NULL,
    lower_before double precision,
    upper_before double precision,
    lower_after  double precision NOT NULL,
    upper_after  double precision NOT NULL,
    changed_by   text NOT NULL,
    changed_at   timestamptz NOT NULL DEFAULT now(),
    CHECK (lower_after < upper_after)
);
CREATE INDEX IF NOT EXISTS idx_grade_changes_code ON grade_changes (grade_code, id);

CREATE TABLE IF NOT EXISTS strain_readings (
    id serial PRIMARY KEY,
    span_code text NOT NULL,
    microstrain double precision NOT NULL,
    load_grade text REFERENCES load_grades (grade_code),
    grade_lower double precision,
    grade_upper double precision,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_strain_readings_status ON strain_readings (status, id);
"""

# 旧库（无分档版本）幂等迁移：补齐列并给历史单据回填默认轻载快照。
_MIGRATE_COLUMNS = [
    ("load_grade", "text REFERENCES load_grades (grade_code)"),
    ("grade_lower", "double precision"),
    ("grade_upper", "double precision"),
]


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


async def ensure_schema(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        await conn.execute(SCHEMA_SQL)
        for column, ddl in _MIGRATE_COLUMNS:
            await conn.execute(
                "ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS "
                f"{column} {ddl}"
            )
        # 先保证等级行存在，再回填历史单（回填值受外键约束）。
        await _seed_grades(conn)
        await _backfill_legacy(conn)
        await conn.commit()


async def _backfill_legacy(conn) -> None:
    # 历史数据按旧的 80～220 全局合格带回填为轻载快照。
    await conn.execute(
        """
        UPDATE strain_readings
        SET load_grade = 'light', grade_lower = 80, grade_upper = 220
        WHERE load_grade IS NULL
        """
    )


async def _seed_grades(conn) -> None:
    for code, name, lower, upper in DEFAULT_GRADES:
        await conn.execute(
            """
            INSERT INTO load_grades (grade_code, grade_name, lower_bound, upper_bound)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (grade_code) DO NOTHING
            """,
            (code, name, lower, upper),
        )


async def seed_if_empty(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) AS n FROM strain_readings")
            row = await cur.fetchone()
            if row["n"] > 0:
                return
            await cur.execute(
                "SELECT lower_bound, upper_bound FROM load_grades WHERE grade_code = 'light'"
            )
            g = await cur.fetchone()
            lower, upper = float(g["lower_bound"]), float(g["upper_bound"])
            samples = [
                ("跨中S1", 150.0),
                ("支座S2", 40.0),
            ]
            for span_code, microstrain in samples:
                verdict, reason = judge_microstrain(microstrain, lower, upper, "轻载")
                await cur.execute(
                    """
                    INSERT INTO strain_readings
                        (span_code, microstrain, load_grade, grade_lower, grade_upper,
                         verdict, reason, status, created_by, processed_at)
                    VALUES (%s, %s, 'light', %s, %s, %s, %s, 'done', 'surveyor', now())
                    """,
                    (span_code, microstrain, lower, upper, verdict, reason),
                )
        await conn.commit()


def connect_sync():
    import psycopg

    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)
    for column, ddl in _MIGRATE_COLUMNS:
        conn.execute(
            "ALTER TABLE strain_readings ADD COLUMN IF NOT EXISTS " f"{column} {ddl}"
        )
    # 先插等级行，再回填引用该等级的历史单（外键顺序）。
    for code, name, lower, upper in DEFAULT_GRADES:
        conn.execute(
            """
            INSERT INTO load_grades (grade_code, grade_name, lower_bound, upper_bound)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (grade_code) DO NOTHING
            """,
            (code, name, lower, upper),
        )
    conn.execute(
        """
        UPDATE strain_readings
        SET load_grade = 'light', grade_lower = 80, grade_upper = 220
        WHERE load_grade IS NULL
        """
    )


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM strain_readings").fetchone()
    if row["n"] > 0:
        return
    g = conn.execute(
        "SELECT lower_bound, upper_bound FROM load_grades WHERE grade_code = 'light'"
    ).fetchone()
    lower, upper = float(g["lower_bound"]), float(g["upper_bound"])
    samples = [
        ("跨中S1", 150.0),
        ("支座S2", 40.0),
    ]
    for span_code, microstrain in samples:
        verdict, reason = judge_microstrain(microstrain, lower, upper, "轻载")
        conn.execute(
            """
            INSERT INTO strain_readings
                (span_code, microstrain, load_grade, grade_lower, grade_upper,
                 verdict, reason, status, created_by, processed_at)
            VALUES (%s, %s, 'light', %s, %s, %s, %s, 'done', 'surveyor', now())
            """,
            (span_code, microstrain, lower, upper, verdict, reason),
        )
    conn.commit()
