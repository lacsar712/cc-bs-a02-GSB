import m from "mithril";

const TOKEN_KEY = "bridge_strain_token";
const USER_KEY = "bridge_strain_user";

function verdictClass(verdict, status) {
  if (verdict === "合格") return "tag pass";
  if (verdict === "越界") return "tag fail";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "待处理";
  if (row.status === "processing") return "处理中";
  return "—";
}

function statusText(status) {
  if (status === "pending") return "待处理";
  if (status === "processing") return "处理中";
  if (status === "done") return "已判定";
  return status || "—";
}

function fmtNum(v) {
  if (v === null || v === undefined || v === "") return "—";
  return `${Number(v)}`;
}

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  user: null,
  page: "main", // main | grades
  loginForm: { username: "surveyor", password: "surv123456" },
  submitForm: { span_code: "", microstrain: "", load_grade: "" },
  rows: [],
  grades: [],
  history: [],
  // 每个等级一份改档草稿：{ lower: "", upper: "", note: "" }
  gradeEdits: {},
  error: "",
  msg: "",
  gradesError: "",
  gradesMsg: "",
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

function gradeName(key) {
  const g = state.grades.find((x) => x.grade_key === key);
  return g ? g.grade_name : key || "—";
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
  if (!state.token) return;
  try {
    const [grades, history] = await Promise.all([
      api("/api/grades"),
      api("/api/grades/history"),
    ]);
    state.grades = grades;
    state.history = history;
    // 首次进入时用现行值预填改档草稿。
    grades.forEach((g) => {
      if (!state.gradeEdits[g.grade_key]) {
        state.gradeEdits[g.grade_key] = {
          lower: fmtNum(g.lower_bound),
          upper: fmtNum(g.upper_bound),
          note: "",
        };
      }
    });
    state.gradesError = "";
  } catch (err) {
    state.gradesError = err.message || "加载分档失败";
  }
  m.redraw();
}

function startPolling() {
  if (state.timer) clearInterval(state.timer);
  if (!state.token) return;
  state.timer = setInterval(() => {
    loadReadings();
    if (state.page === "grades") loadGrades();
  }, 3000);
}

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  state.token = "";
  state.user = null;
  state.rows = [];
  state.grades = [];
  state.history = [];
  state.page = "main";
  if (state.timer) clearInterval(state.timer);
}

const GradesPage = {
  oninit() {
    loadGrades();
  },
  view() {
    const isWriter = state.user?.role === "writer";
    return [
      m("div.card", [
        m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "分等级表"),
        m(
          "p.sub",
          { style: { margin: "0 0 0.75rem" } },
          isWriter
            ? "测量员维护各荷载等级的微应变合格闭区间，判定吃该等级现行上下限，边界值本身合格。"
            : "复核员仅可查看分档，不可改档、不可报送。"
        ),
        m("table", [
          m("thead", [
            m("tr", [
              m("th", "荷载等级"),
              m("th", "下限（με）"),
              m("th", "上限（με）"),
              m("th", "合格闭区间"),
              m("th", { colspan: isWriter ? 2 : 1 }, isWriter ? "改档" : "最近维护"),
            ]),
          ]),
          m(
            "tbody",
            state.grades.length
              ? state.grades.map((g) => {
                  const edit = state.gradeEdits[g.grade_key] || {
                    lower: "",
                    upper: "",
                    note: "",
                  };
                  const cells = [
                    m("td", g.grade_name),
                    isWriter
                      ? m("td", [
                          m("input.bound", {
                            type: "number",
                            step: "0.1",
                            value: edit.lower,
                            "aria-label": `${g.grade_name}下限`,
                            oninput: (e) => {
                              edit.lower = e.target.value;
                            },
                          }),
                        ])
                      : m("td", fmtNum(g.lower_bound)),
                    isWriter
                      ? m("td", [
                          m("input.bound", {
                            type: "number",
                            step: "0.1",
                            value: edit.upper,
                            "aria-label": `${g.grade_name}上限`,
                            oninput: (e) => {
                              edit.upper = e.target.value;
                            },
                          }),
                        ])
                      : m("td", fmtNum(g.upper_bound)),
                    m(
                      "td",
                      `${fmtNum(g.lower_bound)} ～ ${fmtNum(g.upper_bound)}`
                    ),
                  ];
                  if (isWriter) {
                    cells.push(
                      m("td.note-cell", [
                        m("input.note", {
                          placeholder: "改档原因（留痕）",
                          value: edit.note,
                          oninput: (e) => {
                            edit.note = e.target.value;
                          },
                        }),
                      ]),
                      m("td", [
                        m(
                          "button",
                          {
                            type: "button",
                            onclick: async () => {
                              state.gradesError = "";
                              state.gradesMsg = "";
                              const lower = Number(edit.lower);
                              const upper = Number(edit.upper);
                              if (
                                edit.lower.trim() === "" ||
                                edit.upper.trim() === "" ||
                                !Number.isFinite(lower) ||
                                !Number.isFinite(upper)
                              ) {
                                state.gradesError = "上下限必须都是数字";
                                m.redraw();
                                return;
                              }
                              if (lower > upper) {
                                state.gradesError = "下限不能大于上限";
                                m.redraw();
                                return;
                              }
                              try {
                                await api(`/api/grades/${g.grade_key}`, {
                                  method: "PUT",
                                  body: JSON.stringify({
                                    lower_bound: lower,
                                    upper_bound: upper,
                                    note: edit.note,
                                  }),
                                });
                                state.gradesMsg = `${g.grade_name}分档已更新并留痕`;
                                await loadGrades();
                              } catch (err) {
                                state.gradesError = err.message || "改档失败";
                              }
                              m.redraw();
                            },
                          },
                          "保存改档"
                        ),
                      ])
                    );
                  } else {
                    cells.push(
                      m(
                        "td",
                        g.updated_by
                          ? `${g.updated_by} · ${g.updated_at ? new Date(g.updated_at).toLocaleString() : ""}`
                          : "初始值"
                      )
                    );
                  }
                  return m("tr", { key: g.grade_key }, cells);
                })
              : [m("tr", m("td", { colspan: isWriter ? 6 : 5 }, "暂无分档"))]
          ),
        ]),
        state.gradesError ? m("p.err", state.gradesError) : null,
        state.gradesMsg ? m("p.ok", state.gradesMsg) : null,
      ]),

      m("div.card", [
        m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "改档记录"),
        m("table", [
          m("thead", [
            m("tr", [
              m("th", "#"),
              m("th", "等级"),
              m("th", "原闭区间"),
              m("th", "新闭区间"),
              m("th", "改档人"),
              m("th", "原因"),
              m("th", "时间"),
            ]),
          ]),
          m(
            "tbody",
            state.history.length
              ? state.history.map((h) =>
                  m("tr", { key: h.id }, [
                    m("td", h.id),
                    m("td", gradeName(h.grade_key)),
                    m("td", `${fmtNum(h.old_lower)} ～ ${fmtNum(h.old_upper)}`),
                    m("td", `${fmtNum(h.new_lower)} ～ ${fmtNum(h.new_upper)}`),
                    m("td", h.changed_by),
                    m("td", h.note || "—"),
                    m("td", h.changed_at ? new Date(h.changed_at).toLocaleString() : "—"),
                  ])
                )
              : [m("tr", m("td", { colspan: 7 }, "暂无改档记录"))]
          ),
        ]),
      ]),

      m("div.card", [
        m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "领取抄档说明"),
        m("ul.note-list", [
          m("li", "新报送必须点选荷载等级；空选等级的报送整笔退回，不入队。"),
          m(
            "li",
            "判定按该等级现行闭区间（含边界）执行：微应变落在下限与上限之间判合格，否则越界。"
          ),
          m(
            "li",
            "读数被后台工人认领（进入处理中）的瞬间，会把当时该等级的现行上下限抄录到该笔读数上。"
          ),
          m(
            "li",
            "已被领走、尚在处理中的单始终沿用领取瞬间抄下的那一档；此后再改档，不影响这些在途单，只影响改档后新领取的单。"
          ),
          m(
            "li",
            "每次改档都会在改档记录中留痕（原区间、新区间、改档人、原因与时间）；复核员只能翻看分档与记录，不能改档也不能报送。"
          ),
        ]),
      ]),
    ];
  },
};

const App = {
  oninit() {
    loadReadings();
    loadGrades();
    startPolling();
  },
  onremove() {
    if (state.timer) clearInterval(state.timer);
  },
  view() {
    if (!state.token) {
      return m(
        "div.wrap",
        [
          m("h1", "桥梁应变班交台"),
          m(
            "p.sub",
            "测量员提交跨段编号与微应变读数并点选荷载等级，后台工人按该等级现行带认领判定。"
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
                    await Promise.all([loadReadings(), loadGrades()]);
                    startPolling();
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
                  m(
                    "button",
                    { type: "submit", disabled: state.loading },
                    "登录"
                  ),
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
        ]
      );
    }

    const isWriter = state.user?.role === "writer";

    return m("div.wrap", [
      m("div.topbar", [
        m("div", [
          m("h1", "桥梁应变班交台"),
          m("p.sub", "按荷载等级分档判定：微应变落入该等级现行闭区间为合格，否则越界。"),
        ]),
        m("div.top-actions", [
          m(
            "button.secondary",
            {
              type: "button",
              class: state.page === "grades" ? "active-nav" : "",
              onclick: () => {
                state.page = state.page === "grades" ? "main" : "grades";
                state.gradesError = "";
                state.gradesMsg = "";
                if (state.page === "grades") loadGrades();
              },
            },
            state.page === "grades" ? "← 返回总览" : "荷载分档"
          ),
          m("span.user-chip", `${state.user?.username}（${isWriter ? "测量员" : "复核员"}）`),
          m(
            "button.secondary",
            { type: "button", onclick: logout },
            "退出"
          ),
        ]),
      ]),
      state.page === "grades"
        ? m(GradesPage)
        : [
            isWriter
              ? m("div.card", [
                  m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "提交读数"),
                  m(
                    "form",
                    {
                      onsubmit: async (e) => {
                        e.preventDefault();
                        state.error = "";
                        state.msg = "";
                        // 前端校验与后端对齐：等级空选整笔不提交。
                        if (!state.submitForm.load_grade) {
                          state.error = "必须点选荷载等级";
                          return;
                        }
                        const microstrain = parseFloat(state.submitForm.microstrain);
                        if (!Number.isFinite(microstrain)) {
                          state.error = "微应变必须是数字";
                          return;
                        }
                        state.loading = true;
                        try {
                          const data = await api("/api/readings", {
                            method: "POST",
                            body: JSON.stringify({
                              span_code: state.submitForm.span_code,
                              microstrain,
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
                              m("option", { value: "" }, "请选择荷载等级…"),
                              ...state.grades.map((g) =>
                                m(
                                  "option",
                                  { value: g.grade_key },
                                  `${g.grade_name}（${fmtNum(g.lower_bound)}～${fmtNum(g.upper_bound)}）`
                                )
                              ),
                            ]
                          ),
                        ]),
                        m(
                          "button",
                          { type: "submit", disabled: state.loading },
                          "提交"
                        ),
                      ]),
                      state.error ? m("p.err", state.error) : null,
                      state.msg ? m("p.ok", state.msg) : null,
                    ]
                  ),
                ])
              : null,
            m("div.card", [
              m("h2", { style: { marginTop: 0, fontSize: "1.1rem" } }, "读数列表"),
              m("table", [
                m("thead", [
                  m("tr", [
                    m("th", "编号"),
                    m("th", "跨段"),
                    m("th", "微应变"),
                    m("th", "荷载等级"),
                    m("th", "判定带（领取抄档）"),
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
                          m("td", r.microstrain),
                          m("td", gradeName(r.load_grade)),
                          m(
                            "td",
                            r.grade_lower !== null && r.grade_lower !== undefined
                              ? `${fmtNum(r.grade_lower)} ～ ${fmtNum(r.grade_upper)}`
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
                          m("td", statusText(r.status)),
                          m("td", r.created_by),
                        ])
                      )
                    : [m("tr", m("td", { colspan: 9 }, "暂无数据"))]
                ),
              ]),
            ]),
          ],
    ]);
  },
};

export default App;
