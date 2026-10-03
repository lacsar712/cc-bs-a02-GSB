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


def _reading_dict(r) -> dict:
    return {
        "id": r["id"],
        "span_code": r["span_code"],
        "microstrain": r["microstrain"],
        "load_grade": r["load_grade"],
        "grade_lower": r["grade_lower"],
        "grade_upper": r["grade_upper"],
        "grade_snapshot_at": _iso(r["grade_snapshot_at"]),
        "verdict": r["verdict"],
        "reason": r["reason"],
        "status": r["status"],
        "created_by": r["created_by"],
        "created_at": _iso(r["created_at"]),
        "processed_at": _iso(r["processed_at"]),
    }


def _grade_dict(r) -> dict:
    return {
        "grade_key": r["grade_key"],
        "grade_name": r["grade_name"],
        "lower_bound": r["lower_bound"],
        "upper_bound": r["upper_bound"],
        "sort_order": r["sort_order"],
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
    # 分档表对复核员同样开放（只读），便于其复核与对照。
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT grade_key, grade_name, lower_bound, upper_bound,
                       sort_order, updated_by, updated_at
                FROM load_grades
                ORDER BY sort_order, grade_key
                """
            )
            rows = await cur.fetchall()
    return sanic_json([_grade_dict(r) for r in rows])


@app.get("/api/grades/history")
async def list_grade_history(request):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    grade_key = request.args.get("grade_key")
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            if grade_key:
                await cur.execute(
                    """
                    SELECT id, grade_key, old_lower, old_upper,
                           new_lower, new_upper, changed_by, note, changed_at
                    FROM grade_change_log
                    WHERE grade_key = %s
                    ORDER BY id DESC
                    LIMIT 200
                    """,
                    (grade_key,),
                )
            else:
                await cur.execute(
                    """
                    SELECT id, grade_key, old_lower, old_upper,
                           new_lower, new_upper, changed_by, note, changed_at
                    FROM grade_change_log
                    ORDER BY id DESC
                    LIMIT 200
                    """
                )
            rows = await cur.fetchall()
    return sanic_json(
        [
            {
                "id": r["id"],
                "grade_key": r["grade_key"],
                "old_lower": r["old_lower"],
                "old_upper": r["old_upper"],
                "new_lower": r["new_lower"],
                "new_upper": r["new_upper"],
                "changed_by": r["changed_by"],
                "note": r["note"],
                "changed_at": _iso(r["changed_at"]),
            }
            for r in rows
        ]
    )


def _parse_bound(value, label: str) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}必须是数字")
    if not math.isfinite(num):
        raise ValueError(f"{label}必须是有限数字")
    return num


@app.put("/api/grades/<grade_key>")
async def update_grade(request, grade_key):
    user = _require_user(request)
    if not user:
        return sanic_json({"detail": "未登录"}, status=401)
    # 仅测量员可改档；复核员只读分档与记录。
    if user["role"] != "writer":
        return sanic_json({"detail": "仅测量员可修改荷载分档"}, status=403)
    body = request.json or {}
    try:
        lower = _parse_bound(body.get("lower_bound"), "下限")
        upper = _parse_bound(body.get("upper_bound"), "上限")
    except ValueError as exc:
        return sanic_json({"detail": str(exc)}, status=400)
    if lower > upper:
        return sanic_json(
            {"detail": "下限不能大于上限"}, status=400
        )
    note = str(body.get("note", "")).strip() or None

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                # FOR UPDATE：与工人领取抄档互斥，避免改档/抄档竞态。
                await cur.execute(
                    """
                    SELECT grade_key, lower_bound, upper_bound
                    FROM load_grades
                    WHERE grade_key = %s
                    FOR UPDATE
                    """,
                    (grade_key,),
                )
                current = await cur.fetchone()
                if not current:
                    return sanic_json({"detail": "未知荷载等级"}, status=404)
                old_lower = float(current["lower_bound"])
                old_upper = float(current["upper_bound"])
                if (old_lower, old_upper) == (lower, upper):
                    return sanic_json(
                        {"detail": "上下限未变化，无需改档"}, status=400
                    )
                await cur.execute(
                    """
                    UPDATE load_grades
                    SET lower_bound = %s, upper_bound = %s,
                        updated_by = %s, updated_at = now()
                    WHERE grade_key = %s
                    RETURNING grade_key, grade_name, lower_bound,
                              upper_bound, sort_order, updated_by,
                              updated_at
                    """,
                    (lower, upper, user["username"], grade_key),
                )
                updated = await cur.fetchone()
                await cur.execute(
                    """
                    INSERT INTO grade_change_log
                        (grade_key, old_lower, old_upper,
                         new_lower, new_upper, changed_by, note)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        grade_key,
                        old_lower,
                        old_upper,
                        lower,
                        upper,
                        user["username"],
                        note,
                    ),
                )

    return sanic_json(_grade_dict(updated))


@app.get("/api/readings")
async def list_readings(request):
    if not _require_user(request):
        return sanic_json({"detail": "未登录"}, status=401)
    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, span_code, microstrain, load_grade,
                       grade_lower, grade_upper, grade_snapshot_at,
                       verdict, reason, status,
                       created_by, created_at, processed_at
                FROM strain_readings
                ORDER BY id DESC
                """
            )
            rows = await cur.fetchall()
    return sanic_json([_reading_dict(r) for r in rows])


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
    try:
        microstrain = float(body.get("microstrain"))
    except (TypeError, ValueError):
        return sanic_json({"detail": "微应变必须是数字"}, status=400)
    if not math.isfinite(microstrain):
        return sanic_json({"detail": "微应变必须是有限数字"}, status=400)

    # 新报送必须点选荷载等级；空选整笔退回，不做任何兜底。
    load_grade = body.get("load_grade")
    if load_grade is None or str(load_grade).strip() == "":
        return sanic_json(
            {"detail": "必须点选荷载等级，未选等级的报送整笔退回"}, status=400
        )
    load_grade = str(load_grade).strip()

    pool = request.app.ctx.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 入库前与分档表对齐：等级不存在同样整笔退回
            # （另有外键/CHECK 约束兜底，前端校验与入库保持一致）。
            await cur.execute(
                "SELECT grade_key FROM load_grades WHERE grade_key = %s",
                (load_grade,),
            )
            if await cur.fetchone() is None:
                return sanic_json(
                    {"detail": "荷载等级无效，请从分档表中选择"}, status=400
                )
            await cur.execute(
                """
                INSERT INTO strain_readings
                    (span_code, microstrain, load_grade, status,
                     created_by, created_at)
                VALUES (%s, %s, %s, 'pending', %s, now())
                RETURNING id, span_code, microstrain, load_grade,
                          grade_lower, grade_upper, grade_snapshot_at,
                          verdict, reason, status,
                          created_by, created_at, processed_at
                """,
                (span_code, microstrain, load_grade, user["username"]),
            )
            row = await cur.fetchone()
        await conn.commit()

    result = _reading_dict(row)
    result["message"] = "已入队，后台工人将认领并按该等级现行带判定"
    return sanic_json(result, status=201)
