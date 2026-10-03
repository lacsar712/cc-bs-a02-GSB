# 桥梁应变班交台

测量员上报跨段编号、微应变读数并**点选荷载等级（轻载/重载）**，后台工人用 `FOR UPDATE SKIP LOCKED` 认领待处理队列，按**该等级现行闭区间**判定 **合格** 或 **越界**。

## 荷载分档

- 顶栏「荷载分档」专页含三块：**分等级表**、**改档记录**、**领取抄档说明**（不塞进总览）。
- 仅测量员可在分等级表为各等级设微应变上下限，每次改档在同一事务内写入改档记录（原区间/新区间/改档人/原因/时间）；复核员只能翻看分档与记录，不可改档也不可报送。
- 新报送必须点选荷载等级，空选（或非法等级）整笔退回：前端拦截、接口 400、入库另有 `CHECK` + 外键约束，三层对齐。
- 判定为**闭区间**（边界值合格）。工人在领取（`SKIP LOCKED`）的同一事务里把当时该等级的现行上下限**抄录**到读数上；已被领走、尚在处理中的单始终沿用领取瞬间那一档，之后改档只影响新领取的单。
- 默认分档：轻载 **80～220 με**、重载 **60～260 με**。例：轻载上限改为 160 后报 180 判越界；改回 220 后再报 180 判合格。

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
| surveyor | surv123456 | 测量员，可提交读数 |
| reviewer | rev123456 | 复核员，只读列表 |

## 启动

```bash
cd projects/19-bridge-strain-shift
docker compose up --build
```

健康检查：`GET http://localhost:8198/api/health` → `{"status":"ok","service":"bridge-strain-shift"}`

## 种子数据

| 跨段 | 微应变 | 结论 |
|------|--------|------|
| 跨中S1 | 150 με | 合格 |
| 支座S2 | 40 με | 越界 |

## 本地开发（可选）

```bash
cd backend && pip install -r requirements.txt
python -m sanic api.app --host=0.0.0.0 --port=8000 --single-process
python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8198**。
