import math
import os
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext
from sanic import Sanic
from sanic.response import json as sanic_json

from db import create_pool, ensure_schema, seed_if_empty

SECRET = os.environ.get("JWT_SECRET", "bridge-strain-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "surveyor": {"role": "writer", "password_hash": pwd.hash("surv123456")},
    "reviewer": {"role": "reader", "password_hash": pwd.hash("rev123456")},
}

app = Sanic("bridge-strain-shift")


def _auth_header(request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _decode_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def _require_user(request) -> dict:
    user = _decode_user(_auth_header(request))
    if not user:
        return None
    return user


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _parse_bound(value, field_name: str) -> float:
    """与前端同一套规则：必须是有限数字。"""
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name}必须是数字")
    if not math.isfinite(result):
        raise ValueError(f"{field_name}必须是有限数字")
    return result


def _grade_dict(r) -> dict:
    return {
        "grade_code": r["grade_code"],
        "grade_name": r["grade_name"],
        "lower_bound": r["lower_bound"],
        "upper_bound": r["upper_bound"],
        "updated_by": r["updated_by"],
        "updated_at": _iso(r["updated_at"]),
    }


@app.before_server_start
async def setup(_app, _loop):
    pool = await create_pool()
    _app.ctx.pool = pool
    await ensure_schema(pool)
    await seed_if_empty(pool)


@app.after_server_stop
async def teardown(_app, _loop):
    pool = _app.ctx.pool
    if pool:
        await pool.close()


@app.get("/api/health")
async def health(_request):
    return sanic_json({"status": "ok", "service": "bridge-strain-shift"})


@app.post("/api/auth/login")
async def login(request):
    body = request.json or {}
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        return sanic_json({"detail": "用户名或密码错误"}, status=401)
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return sanic_json(
        {"access_token": token, "username": username, "role": user["role"]}
    )


@app.get("/api/grades")
async def list_grades(request):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT grade_code, grade_name, lower_bound, upper_bound, updated_by, updated_at
                FROM load_grades
                ORDER BY grade_code
                """
            )
            rows = await cur.fetchall()
    return sanic_json([_grade_dict(r) for r in rows])


@app.get("/api/grades/changes")
async def list_grade_changes(request):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, grade_code, grade_name,
                       lower_before, upper_before, lower_after, upper_after,
                       changed_by, changed_at
                FROM grade_changes
                ORDER BY id DESC
                """
            )
            rows = await cur.fetchall()
    out = [
        {
            "id": r["id"],
            "grade_code": r["grade_code"],
            "grade_name": r["grade_name"],
            "lower_before": r["lower_before"],
            "upper_before": r["upper_before"],
            "lower_after": r["lower_after"],
            "upper_after": r["upper_after"],
            "changed_by": r["changed_by"],
            "changed_at": _iso(r["changed_at"]),
        }
        for r in rows
    ]
    return sanic_json(out)


@app.put("/api/grades/<grade_code>")
async def update_grade(request, grade_code):
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    if user["role"] != "writer":
        return sanic_json({"detail": "仅测量员可调整荷载分档"}, status=403)

    body = request.json or {}
    try:
        lower = _parse_bound(body.get("lower_bound"), "下限")
        upper = _parse_bound(body.get("upper_bound"), "上限")
    except ValueError as exc:
        return sanic_json({"detail": str(exc)}, status=400)
    # 与前端、数据库 CHECK 对齐：闭区间必须下限严格小于上限。
    if not lower < upper:
        return sanic_json(
            {"detail": "下限必须严格小于上限，闭区间不能为空"}, status=400
        )

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT grade_code, grade_name, lower_bound, upper_bound
                    FROM load_grades
                    WHERE grade_code = %s
                    FOR UPDATE
                    """,
                    (grade_code,),
                )
                current = await cur.fetchone()
                if not current:
                    return sanic_json({"detail": "荷载等级不存在"}, status=404)

                lower_before = float(current["lower_bound"])
                upper_before = float(current["upper_bound"])
                if lower_before == lower and upper_before == upper:
                    return sanic_json(
                        {"detail": "上下限与现行分档相同，未改档"}, status=400
                    )

                await cur.execute(
                    """
                    INSERT INTO grade_changes
                        (grade_code, grade_name,
                         lower_before, upper_before, lower_after, upper_after,
                         changed_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, changed_at
                    """,
                    (
                        grade_code,
                        current["grade_name"],
                        lower_before,
                        upper_before,
                        lower,
                        upper,
                        user["username"],
                    ),
                )
                change = await cur.fetchone()
                await cur.execute(
                    """
                    UPDATE load_grades
                    SET lower_bound = %s, upper_bound = %s,
                        updated_by = %s, updated_at = now()
                    WHERE grade_code = %s
                    RETURNING grade_code, grade_name, lower_bound, upper_bound,
                              updated_by, updated_at
                    """,
                    (lower, upper, user["username"], grade_code),
                )
                updated = await cur.fetchone()

    return sanic_json(
        {
            "grade": _grade_dict(updated),
            "change": {
                "id": change["id"],
                "changed_at": _iso(change["changed_at"]),
                "lower_before": lower_before,
                "upper_before": upper_before,
                "lower_after": lower,
                "upper_after": upper,
            },
            "message": (
                f"{updated['grade_name']}已改档："
                f"{lower_before:g}～{upper_before:g} → {lower:g}～{upper:g} με"
            ),
        }
    )


@app.get("/api/readings")
async def list_readings(request):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT r.id, r.span_code, r.microstrain,
                       r.load_grade, g.grade_name,
                       r.grade_lower, r.grade_upper,
                       r.verdict, r.reason, r.status,
                       r.created_by, r.created_at, r.processed_at
                FROM strain_readings r
                LEFT JOIN load_grades g ON g.grade_code = r.load_grade
                ORDER BY r.id DESC
                """
            )
            rows = await cur.fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "span_code": r["span_code"],
                "microstrain": r["microstrain"],
                "load_grade": r["load_grade"],
                "grade_name": r["grade_name"],
                "grade_lower": r["grade_lower"],
                "grade_upper": r["grade_upper"],
                "verdict": r["verdict"],
                "reason": r["reason"],
                "status": r["status"],
                "created_by": r["created_by"],
                "created_at": _iso(r["created_at"]),
                "processed_at": _iso(r["processed_at"]),
            }
        )
    return sanic_json(out)


@app.post("/api/readings")
async def create_reading(request):
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    if user["role"] != "writer":
        return sanic_json({"detail": "仅测量员可提交应变读数"}, status=403)
    body = request.json or {}
    span_code = str(body.get("span_code", "")).strip()
    if not span_code:
        return sanic_json({"detail": "跨段编号不能为空"}, status=400)

    # 荷载等级必选：空选整笔退回，不允许落入队列。
    load_grade = body.get("load_grade")
    if load_grade is None or str(load_grade).strip() == "":
        return sanic_json({"detail": "请点选荷载等级，空选整笔退回"}, status=400)
    load_grade = str(load_grade).strip()

    try:
        microstrain = float(body.get("microstrain"))
    except (TypeError, ValueError):
        return sanic_json({"detail": "微应变必须是数字"}, status=400)
    if not math.isfinite(microstrain):
        return sanic_json({"detail": "微应变必须是有限数字"}, status=400)

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 入库等级必须是分档表现行等级；未知等级同样整笔退回（FK 之外再加一道）。
            await cur.execute(
                "SELECT grade_code FROM load_grades WHERE grade_code = %s",
                (load_grade,),
            )
            if not await cur.fetchone():
                return sanic_json(
                    {"detail": f"未知荷载等级：{load_grade}，整笔退回"}, status=400
                )
            await cur.execute(
                """
                INSERT INTO strain_readings
                    (span_code, microstrain, load_grade, status, created_by, created_at)
                VALUES (%s, %s, %s, 'pending', %s, now())
                RETURNING id, span_code, microstrain, verdict, reason, status,
                          created_by, created_at, processed_at
                """,
                (span_code, microstrain, load_grade, user["username"]),
            )
            row = await cur.fetchone()
        await conn.commit()

    return sanic_json(
        {
            "id": row["id"],
            "span_code": row["span_code"],
            "microstrain": row["microstrain"],
            "load_grade": load_grade,
            "verdict": row["verdict"],
            "reason": row["reason"],
            "status": row["status"],
            "created_by": row["created_by"],
            "created_at": _iso(row["created_at"]),
            "processed_at": None,
            "message": "已入队，后台工人将认领并按该等级现行闭区间判定",
        },
        status=201,
    )
