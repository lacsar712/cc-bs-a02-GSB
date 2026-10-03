import m from "mithril";

const TOKEN_KEY = "bridge_strain_token";
const USER_KEY = "bridge_strain_user";

function verdictClass(verdict, status) {
  if (verdict === "合格") return "tag pass";
  if (verdict === "越界") return "tag fail";
  if (status === "pending" || status === "processing") return "tag wait";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "待处理";
  if (row.status === "processing") return "处理中";
  return "—";
}

function fmt(n) {
  return n === null || n === undefined ? "—" : Number(n).toString();
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  const p = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(
    d.getHours()
  )}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// 与后端 _parse_bound / 数据库 CHECK 同一套校验：有限数字且下限严格小于上限。
function validateBounds(lowerText, upperText) {
  const lower = Number(lowerText);
  const upper = Number(upperText);
  if (lowerText === "" || !Number.isFinite(lower))
    return "下限必须是有限数字";
  if (upperText === "" || !Number.isFinite(upper))
    return "上限必须是有限数字";
  if (!(lower < upper)) return "下限必须严格小于上限，闭区间不能为空";
  return "";
}

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  user: null,
  view: "overview",
  loginForm: { username: "surveyor", password: "surv123456" },
  submitForm: { span_code: "", microstrain: "", load_grade: "" },
  rows: [],
  grades: [],
  changes: [],
  gradeDrafts: {},
  error: "",
  msg: "",
  loading: false,
  timer: null,
};

try {
  state.user = JSON.parse(localStorage.getItem(USER_KEY) || "null");
} catch {
  state.user = null;
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(path, { ...opts, headers });
  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { detail: text };
  }
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

async function loadReadings() {
  if (!state.token) return;
  try {
    state.rows = await api("/api/readings");
    state.error = "";
  } catch {
    state.error = "加载列表失败，请重新登录";
  }
  m.redraw();
}

async function loadGrades() {
  try {
    state.grades = await api("/api/grades");
    for (const g of state.grades) {
      if (!state.gradeDrafts[g.grade_code]) {
        state.gradeDrafts[g.grade_code] = {
          lower: String(g.lower_bound),
          upper: String(g.upper_bound),
        };
      }
    }
    state.changes = await api("/api/grades/changes");
    state.error = "";
  } catch {
    state.error = "加载分档数据失败";
  }
  m.redraw();
}

function startOverviewPolling() {
  if (state.timer) clearInterval(state.timer);
  if (!state.token) return;
  state.timer = setInterval(loadReadings, 3000);
}

function startGradesPolling() {
  if (state.timer) clearInterval(state.timer);
  state.timer = setInterval(loadGrades, 3000);
}

function switchView(view) {
  state.view = view;
  state.error = "";
  state.msg = "";
  if (view === "overview") {
    startOverviewPolling();
    loadReadings();
    loadGrades();
  } else {
    startGradesPolling();
    loadGrades();
  }
}

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  state.token = "";
  state.user = null;
  state.rows = [];
  state.grades = [];
  state.changes = [];
  if (state.timer) clearInterval(state.timer);
}

const LoginPage = {
  view: () =>
    m("div.wrap", [
      m("h1", "桥梁应变班交台"),
      m(
        "p.sub",
        "测量员提交跨段编号与微应变读数，按荷载等级现行闭区间判定合格或越界。"
      ),
      m("div.card", [
        m(
          "form",
          {
            onsubmit: async (e) => {
              e.preventDefault();
              state.error = "";
              state.loading = true;
              try {
                const data = await api("/api/auth/login", {
                  method: "POST",
                  body: JSON.stringify(state.loginForm),
                });
                state.token = data.access_token;
                state.user = { username: data.username, role: data.role };
                localStorage.setItem(TOKEN_KEY, state.token);
                localStorage.setItem(USER_KEY, JSON.stringify(state.user));
                state.view = "overview";
                await loadReadings();
                await loadGrades();
                startOverviewPolling();
              } catch {
                state.error = "用户名或密码错误";
              } finally {
                state.loading = false;
                m.redraw();
              }
            },
          },
          [
            m("div.row", [
              m("label", [
                "用户名",
                m("input", {
                  value: state.loginForm.username,
                  oninput: (e) => {
                    state.loginForm.username = e.target.value;
                  },
                }),
              ]),
              m("label", [
                "密码",
                m("input", {
                  type: "password",
                  value: state.loginForm.password,
                  oninput: (e) => {
                    state.loginForm.password = e.target.value;
                  },
                }),
              ]),
              m("button", { type: "submit", disabled: state.loading }, "登录"),
            ]),
            state.error ? m("p.err", state.error) : null,
          ]
        ),
        m(
          "p.sub",
          { style: { marginBottom: 0 } },
          "测量员 surveyor / surv123456 · 复核员 reviewer / rev123456"
        ),
      ]),
    ]),
};

function topbar(isWriter) {
  return m("div.topbar", [
    m("div", [
      m("h1", "桥梁应变班交台"),
      m(
        "p.sub",
        "按荷载等级闭区间判定：测量员维护分档，复核员只可翻阅分档与改档记录。"
      ),
    ]),
    m("div.topright", [
      m("div.nav", [
        m(
          `button${state.view === "overview" ? ".navbtn.on" : ".navbtn"}`,
          { type: "button", onclick: () => switchView("overview") },
          "总览"
        ),
        // 顶栏挂荷载分档入口，测量员与复核员都可进入（复核只读）。
        m(
          `button${state.view === "grades" ? ".navbtn.on" : ".navbtn"}`,
          { type: "button", onclick: () => switchView("grades") },
          "荷载分档"
        ),
      ]),
      m("div.who", [
        `${state.user?.username}（${isWriter ? "测量员" : "复核员"}） `,
        m(
          "button.secondary",
          {
            type: "button",
            onclick: () => {
              logout();
              m.redraw();
            },
          },
          "退出"
        ),
      ]),
    ]),
  ]);
}

const SubmitCard = {
  view: () =>
    m("div.card", [
      m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "提交读数"),
      m(
        "form",
        {
          onsubmit: async (e) => {
            e.preventDefault();
            state.error = "";
            state.msg = "";
            // 前端先拦一道：荷载等级空选整笔退回，不发请求。
            if (!state.submitForm.load_grade) {
              state.error = "请点选荷载等级，空选整笔退回";
              return;
            }
            state.loading = true;
            try {
              const data = await api("/api/readings", {
                method: "POST",
                body: JSON.stringify({
                  span_code: state.submitForm.span_code,
                  microstrain: parseFloat(state.submitForm.microstrain),
                  load_grade: state.submitForm.load_grade,
                }),
              });
              state.msg = data.message || "已提交";
              state.submitForm = {
                span_code: "",
                microstrain: "",
                load_grade: "",
              };
              await loadReadings();
            } catch (err) {
              state.error = err.message || "提交失败";
            } finally {
              state.loading = false;
              m.redraw();
            }
          },
        },
        [
          m("div.row", [
            m("label", [
              "跨段编号",
              m("input", {
                required: true,
                placeholder: "例如 跨中S3",
                value: state.submitForm.span_code,
                oninput: (e) => {
                  state.submitForm.span_code = e.target.value;
                },
              }),
            ]),
            m("label", [
              "微应变（με）",
              m("input", {
                required: true,
                type: "number",
                step: "0.1",
                value: state.submitForm.microstrain,
                oninput: (e) => {
                  state.submitForm.microstrain = e.target.value;
                },
              }),
            ]),
            m("label", [
              "荷载等级（必选）",
              m(
                "select",
                {
                  required: true,
                  value: state.submitForm.load_grade,
                  onchange: (e) => {
                    state.submitForm.load_grade = e.target.value;
                  },
                },
                [
                  m(
                    "option",
                    { value: "", disabled: true },
                    "请选择荷载等级"
                  ),
                  ...state.grades.map((g) =>
                    m(
                      "option",
                      { value: g.grade_code },
                      `${g.grade_name}（${fmt(g.lower_bound)}～${fmt(
                        g.upper_bound
                      )} με）`
                    )
                  ),
                ]
              ),
            ]),
            m("button", { type: "submit", disabled: state.loading }, "提交"),
          ]),
          state.error ? m("p.err", state.error) : null,
          state.msg ? m("p.ok", state.msg) : null,
        ]
      ),
    ]),
};

const ReadingsCard = {
  view: () =>
    m("div.card", [
      m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "读数列表"),
      m("table", [
        m("thead", [
          m("tr", [
            m("th", "编号"),
            m("th", "跨段"),
            m("th", "微应变"),
            m("th", "荷载等级"),
            m("th", "判定区间（领取抄档）"),
            m("th", "结论"),
            m("th", "说明"),
            m("th", "状态"),
            m("th", "提交人"),
          ]),
        ]),
        m(
          "tbody",
          state.rows.length
            ? state.rows.map((r) =>
                m("tr", { key: r.id }, [
                  m("td", r.id),
                  m("td", r.span_code),
                  m("td", fmt(r.microstrain)),
                  m("td", r.grade_name || "—"),
                  m(
                    "td",
                    r.grade_lower !== null && r.grade_upper !== null
                      ? `${fmt(r.grade_lower)}～${fmt(r.grade_upper)}`
                      : "领取时抄录"
                  ),
                  m("td", [
                    m(
                      "span",
                      { class: verdictClass(r.verdict, r.status) },
                      displayVerdict(r)
                    ),
                  ]),
                  m("td", r.reason || "—"),
                  m("td", r.status),
                  m("td", r.created_by),
                ])
              )
            : [m("tr", m("td", { colspan: 9 }, "暂无数据"))]
        ),
      ]),
    ]),
};

function gradeRow(g, isWriter) {
  const draft = state.gradeDrafts[g.grade_code] || {
    lower: String(g.lower_bound),
    upper: String(g.upper_bound),
  };
  const localError = isWriter
    ? validateBounds(draft.lower, draft.upper)
    : "";
  return m("tr", { key: g.grade_code }, [
    m("td", g.grade_name),
    m("td", g.grade_code),
    isWriter
      ? m("td", [
          m("input.bound", {
            type: "number",
            step: "0.1",
            value: draft.lower,
            oninput: (e) => {
              draft.lower = e.target.value;
            },
          }),
        ])
      : m("td", fmt(g.lower_bound)),
    isWriter
      ? m("td", [
          m("input.bound", {
            type: "number",
            step: "0.1",
            value: draft.upper,
            oninput: (e) => {
              draft.upper = e.target.value;
            },
          }),
        ])
      : m("td", fmt(g.upper_bound)),
    m("td", g.updated_by ? `${g.updated_by} · ${fmtTime(g.updated_at)}` : "初始默认"),
    isWriter
      ? m("td", [
          m(
            "button",
            {
              type: "button",
              disabled: !!localError || state.loading,
              title: localError || "按此档写入并留下改档记录",
              onclick: async () => {
                state.error = "";
                state.msg = "";
                const err = validateBounds(draft.lower, draft.upper);
                if (err) {
                  state.error = err;
                  return;
                }
                state.loading = true;
                try {
                  const data = await api(`/api/grades/${g.grade_code}`, {
                    method: "PUT",
                    body: JSON.stringify({
                      lower_bound: Number(draft.lower),
                      upper_bound: Number(draft.upper),
                    }),
                  });
                  state.msg = data.message || "已改档";
                  await loadGrades();
                } catch (e) {
                  state.error = e.message || "改档失败";
                } finally {
                  state.loading = false;
                  m.redraw();
                }
              },
            },
            "改档"
          ),
          localError ? m("p.err.cellerr", localError) : null,
        ])
      : m("td.readonly", "只读"),
  ]);
}

const GradesPage = {
  view: () => {
    const isWriter = state.user?.role === "writer";
    return [
      // 第一块：分等级表（测量员可设上下限，复核只读）
      m("div.card", [
        m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, [
          "分等级表 · 现行闭区间",
          m(
            "span.badge",
            isWriter ? "可改档（闭区间，含上下限）" : "复核侧只读，不可改档"
          ),
        ]),
        m("table", [
          m("thead", [
            m("tr", [
              m("th", "等级"),
              m("th", "代码"),
              m("th", "下限（με）"),
              m("th", "上限（με）"),
              m("th", "最近改档"),
              m("th", "操作"),
            ]),
          ]),
          m(
            "tbody",
            state.grades.length
              ? state.grades.map((g) => gradeRow(g, isWriter))
              : [m("tr", m("td", { colspan: 6 }, "暂无分档"))]
          ),
        ]),
        state.error ? m("p.err", state.error) : null,
        state.msg ? m("p.ok", state.msg) : null,
      ]),

      // 第二块：改档记录（只翻记录，谁都不可改）
      m("div.card", [
        m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "改档记录"),
        m("table", [
          m("thead", [
            m("tr", [
              m("th", "时间"),
              m("th", "等级"),
              m("th", "改前闭区间（με）"),
              m("th", "改后闭区间（με）"),
              m("th", "改档人"),
            ]),
          ]),
          m(
            "tbody",
            state.changes.length
              ? state.changes.map((c) =>
                  m("tr", { key: c.id }, [
                    m("td", fmtTime(c.changed_at)),
                    m("td", c.grade_name),
                    m("td", `${fmt(c.lower_before)}～${fmt(c.upper_before)}`),
                    m("td", `${fmt(c.lower_after)}～${fmt(c.upper_after)}`),
                    m("td", c.changed_by),
                  ])
                )
              : [m("tr", m("td", { colspan: 5 }, "暂无改档记录"))]
          ),
        ]),
      ]),

      // 第三块：领取抄档说明
      m("div.card.note", [
        m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "领取抄档说明"),
        m("ul", [
          m(
            "li",
            "新报送必须点选荷载等级；空选或未知等级整笔退回，不入队。"
          ),
          m(
            "li",
            "后台工人领取待处理单的瞬间，锁定该等级行并把当时的上下限抄进单据快照；判定吃该等级的现行闭区间 [下限, 上限]，上下限都算合格。"
          ),
          m(
            "li",
            "已被领走、尚在处理中的单继续沿用领取瞬间抄下的那一档，之后改档不影响它；改档只影响之后领取的新单。"
          ),
          m(
            "li",
            "每次改档都在改档记录中留痕（改前/改后区间、改档人、时间）；下限必须严格小于上限，前端、接口、数据库三处同规则校验。"
          ),
          m(
            "li",
            "复核侧只能翻阅分档表与改档记录，不可改档，也不可报送读数。"
          ),
        ]),
      ]),
    ];
  },
};

const App = {
  oninit() {
    if (state.token) {
      loadReadings();
      loadGrades();
      startOverviewPolling();
    }
  },
  onremove() {
    if (state.timer) clearInterval(state.timer);
  },
  view() {
    if (!state.token) return m(LoginPage);
    const isWriter = state.user?.role === "writer";
    return m("div.wrap", [
      topbar(isWriter),
      state.view === "grades"
        ? m(GradesPage)
        : [
            // 分等级表/改档记录/说明只在荷载分档专页，不塞总览。
            isWriter ? m(SubmitCard) : null,
            m(ReadingsCard),
          ],
    ]);
  },
};

export default App;
