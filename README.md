# 桥梁应变班交台

测量员上报跨段编号、微应变读数与**荷载等级**，后台工人用 `FOR UPDATE SKIP LOCKED` 认领待处理队列，按该等级**现行闭区间**（默认轻载 80～220 με、重载 100～300 με）判定 **合格** 或 **越界**。

## 荷载分档规则

- 顶栏「荷载分档」入口进入专页，页面分三块：**分等级表**、**改档记录**、**领取抄档说明**（总览页不塞这些）。
- 测量员可在分等级表为轻载/重载各设闭区间上下限；每次保存写一条改档记录（改前/改后区间、改档人、时间）。
- 新报送**必须点选荷载等级**；空选或未知等级整笔退回（前端拦截 + 接口 400 + 数据库外键）。
- 工人领取读数的瞬间锁定等级行，把当时的上下限抄进单据快照（`grade_lower`/`grade_upper`）再判定；已领走、处理中的单继续沿用领取瞬间那一档，之后改档不影响它。
- 闭区间，上下限都算合格；下限必须严格小于上限，前端、接口、数据库 CHECK 三处同规则校验。
- 复核员只能翻阅分档表与改档记录、查看读数列表：不可改档，也不可报送。

## 技术栈

| 层 | 选型 |
|----|------|
| 接口 | Python Sanic + psycopg（异步连接池） |
| 工人 | `worker.py`（psycopg 同步，`FOR UPDATE SKIP LOCKED`） |
| 页面 | Mithril.js + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3198 |
| 接口 | http://localhost:8198 |
| PostgreSQL | localhost:54398（库名 `bridgestrain`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| surveyor | surv123456 | 测量员，可提交读数、可改档 |
| reviewer | rev123456 | 复核员，只读（含分档与改档记录） |

## 启动

```bash
docker compose up --build
```

健康检查：`GET http://localhost:8198/api/health` → `{"status":"ok","service":"bridge-strain-shift"}`

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/grades` | 现行分档（轻载/重载闭区间） |
| PUT | `/api/grades/{light\|heavy}` | 改档（仅测量员；写改档记录） |
| GET | `/api/grades/changes` | 改档记录（倒序） |
| POST | `/api/readings` | 报送读数，必须带 `load_grade`，空选整笔退回 |
| GET | `/api/readings` | 读数列表（含等级与领取快照区间） |

## 种子数据

| 跨段 | 荷载等级 | 微应变 | 结论 |
|------|----------|--------|------|
| 跨中S1 | 轻载 80～220 | 150 με | 合格 |
| 支座S2 | 轻载 80～220 | 40 με | 越界 |

## 本地开发（可选）

```bash
cd backend && pip install -r requirements.txt
python -m sanic api.app --host=0.0.0.0 --port=8000 --single-process
python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8198**。
