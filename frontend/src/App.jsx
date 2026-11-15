import { Fragment, useEffect, useState } from "react";

const POLICY_NAMES = {
  drop_group: "整组不进明细",
  wait_in: "出闸停等候队列等进闸",
};
const STATE_NAMES = {
  unset: "未写死",
  waiting: "等候中",
  dropped: "整组已丢掉",
  ingested: "已放行入库",
};

function loadProfile() {
  try {
    return JSON.parse(localStorage.getItem("lake.profile") || "null");
  } catch {
    return null;
  }
}

async function request(path, options = {}) {
  const token = localStorage.getItem("lake.token") || "";
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "请求没有完成");
  }
  return data;
}

export default function App() {
  const [profile, setProfile] = useState(loadProfile);
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("123456");
  const [loginError, setLoginError] = useState("");
  const [dates, setDates] = useState([]);
  const [detail, setDetail] = useState(null);
  const [formError, setFormError] = useState("");
  const [settingError, setSettingError] = useState("");
  const [queueError, setQueueError] = useState("");
  const [buildPolicy, setBuildPolicy] = useState("");
  const [settingPolicy, setSettingPolicy] = useState("drop_group");
  const [openGroups, setOpenGroups] = useState({});
  const [busy, setBusy] = useState(false);

  async function refresh(prefer) {
    const rows = await request("/api/dates");
    setDates(rows);
    const target = prefer || detail?.biz_date || rows[0]?.biz_date;
    if (target) setDetail(await request(`/api/dates/${target}`));
  }

  useEffect(() => {
    if (!profile) return;
    refresh().catch(() => {
      localStorage.removeItem("lake.token");
      localStorage.removeItem("lake.profile");
      setProfile(null);
    });
  }, [profile]);

  useEffect(() => {
    setBuildPolicy("");
    setSettingPolicy("drop_group");
    setOpenGroups({});
    setFormError("");
    setSettingError("");
    setQueueError("");
  }, [detail?.biz_date]);

  async function submitLogin(event) {
    event.preventDefault();
    setLoginError("");
    try {
      const data = await request("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      localStorage.setItem("lake.token", data.access_token);
      const next = { username: data.username, role: data.role, title: data.title };
      localStorage.setItem("lake.profile", JSON.stringify(next));
      setProfile(next);
    } catch (error) {
      setLoginError(error.message);
    }
  }

  async function buildDay() {
    if (!detail) return;
    setBusy(true);
    setFormError("");
    try {
      const data = await request(`/api/dates/${detail.biz_date}/build`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ policy: buildPolicy || null }),
      });
      setDetail(data);
      setDates(await request("/api/dates"));
    } catch (error) {
      setFormError(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function saveSetting(event) {
    event.preventDefault();
    if (!detail) return;
    setBusy(true);
    setSettingError("");
    try {
      const data = await request(`/api/dates/${detail.biz_date}/reversal-setting`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ policy: settingPolicy }),
      });
      setDetail(data);
      setDates(await request("/api/dates"));
    } catch (error) {
      setSettingError(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function release(containerNo) {
    if (!detail) return;
    setBusy(true);
    setQueueError("");
    try {
      const data = await request(
        `/api/dates/${detail.biz_date}/waiting/${containerNo}/release`,
        { method: "POST" }
      );
      setDetail(data);
      setDates(await request("/api/dates"));
    } catch (error) {
      setQueueError(error.message);
    } finally {
      setBusy(false);
    }
  }

  function toggleGroup(box) {
    setOpenGroups((prev) => ({ ...prev, [box]: !prev[box] }));
  }

  function logout() {
    localStorage.removeItem("lake.token");
    localStorage.removeItem("lake.profile");
    setProfile(null);
    setDetail(null);
  }

  if (!profile) {
    return (
      <main className="sheet gate">
        <p className="kicker">贴源层 → 明细层</p>
        <h1>闸口事件湖仓台</h1>
        <p>扫描原文先落贴源。只有字典认得出的事件才收进明细；出闸早于进闸的逆序箱，按写死的逆序办法整组处理。</p>
        <form onSubmit={submitLogin}>
          <label>用户<input value={username} onChange={(event) => setUsername(event.target.value)} /></label>
          <label>口令<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          <button>打开这一层</button>
          {loginError ? <p className="warn">{loginError}</p> : null}
        </form>
      </main>
    );
  }

  const isWriter = profile.role === "writer";
  const reversal = detail?.reversal;
  const setting = reversal?.setting;
  const pendingGroups = (reversal?.groups || []).filter(
    (group) => group.state === "unset" || group.state === "waiting"
  );

  return (
    <>
      <header className="bar">
        <div>
          <p className="kicker">闸口扫描 · 分层</p>
          <h1>闸口事件湖仓台</h1>
        </div>
        <div className="who">
          <span>{profile.title} {profile.username}</span>
          <button className="line" onClick={logout}>离开</button>
        </div>
      </header>
      <main className="layout">
        <aside>
          {dates.map((row) => (
            <button
              key={row.biz_date}
              className={detail?.biz_date === row.biz_date ? "day on" : "day"}
              onClick={() => request(`/api/dates/${row.biz_date}`).then(setDetail)}
            >
              <strong>{row.biz_date}</strong>
              <em>{row.built ? "明细已收" : "尚未构建"}</em>
              <span>贴源 {row.ods_count} 条 · 明细 {row.dwd_count} 条</span>
              {row.pending_reversals ? (
                <em className="alert">逆序 {row.pending_reversals} 箱未收</em>
              ) : null}
            </button>
          ))}
        </aside>
        {detail ? (
          <section className="sheet">
            <div className="split-head">
              <h2>{detail.biz_date}</h2>
              <div className="build-controls">
                <select
                  value={buildPolicy}
                  onChange={(event) => setBuildPolicy(event.target.value)}
                  disabled={!isWriter || busy}
                >
                  <option value="">跟随逆序设置</option>
                  <option value="drop_group">{POLICY_NAMES.drop_group}</option>
                  <option value="wait_in">{POLICY_NAMES.wait_in}</option>
                </select>
                <button disabled={!isWriter || busy} onClick={buildDay}>
                  按字典构建明细层
                </button>
              </div>
            </div>
            <p className="note">IN 进闸，OUT 出闸，RS 倒箱。箱号要是四位字母加七位数字。同一箱号出闸早于进闸就是逆序，整组按写死的办法处理。</p>
            {!isWriter ? <p className="note">核对员不能重新构建，也不能写死逆序办法。</p> : null}
            {formError ? <p className="warn">{formError}</p> : null}

            <section className="panel">
              <h3>逆序设置</h3>
              {setting ? (
                <p className="note">
                  办法：{setting.policy_name} · {setting.set_by} 写死于 {setting.set_at}
                  {setting.locked_today ? <em className="alert"> 当日不许回调</em> : null}
                </p>
              ) : (
                <p className="note">逆序办法还没写死，逆序箱不准收成。</p>
              )}
              {isWriter && !setting?.locked_today ? (
                <form className="setting-form" onSubmit={saveSetting}>
                  <select
                    value={settingPolicy}
                    onChange={(event) => setSettingPolicy(event.target.value)}
                  >
                    <option value="drop_group">{POLICY_NAMES.drop_group}</option>
                    <option value="wait_in">{POLICY_NAMES.wait_in}</option>
                  </select>
                  <button disabled={busy}>{setting ? "回调办法" : "写死办法"}</button>
                </form>
              ) : null}
              {settingError ? <p className="warn">{settingError}</p> : null}
            </section>

            <section className="panel">
              <h3>逆序列表</h3>
              {reversal.groups.length ? (
                <table>
                  <thead>
                    <tr>
                      <th>箱号</th><th>进闸时刻</th><th>出闸时刻</th><th>状态</th>
                      <th>等候条数</th><th>丢掉条数</th><th>办法名</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reversal.groups.map((group) => (
                      <Fragment key={group.container_no}>
                        <tr
                          className="pick"
                          onClick={() => toggleGroup(group.container_no)}
                        >
                          <td>{group.container_no}</td>
                          <td>{group.in_at}</td>
                          <td>{group.out_at}</td>
                          <td><em className={group.state === "ingested" || group.state === "dropped" ? "" : "alert"}>{group.state_name}</em></td>
                          <td>{group.waiting_count}</td>
                          <td>{group.dropped_count}</td>
                          <td>{group.policy_name || "未写死"}</td>
                        </tr>
                        {openGroups[group.container_no] ? (
                          <tr className="timing">
                            <td colSpan={7}>
                              <p>
                                时序详情：进闸 {group.in_at}，出闸 {group.out_at}，
                                出闸早了 {group.gap_minutes} 分钟。
                                {group.actor ? ` 由 ${group.actor} 定局。` : ""}
                              </p>
                              <ul>
                                {group.events.map((event) => (
                                  <li key={event.source_id}>
                                    <span>来源 {event.source_id} · {event.event_kind}</span>
                                    <em>{event.event_at}</em>
                                  </li>
                                ))}
                              </ul>
                            </td>
                          </tr>
                        ) : null}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="note">这一天没有逆序箱。</p>
              )}
            </section>

            <section className="panel">
              <h3>等候队列</h3>
              {reversal.queue.length ? (
                <ul>
                  {reversal.queue.map((row) => (
                    <li key={row.id} className="queue-row">
                      <span>箱号 {row.container_no} · 出闸 {row.event_at} · 来源 {row.source_id} · {row.queued_by} 收进队列 {row.queued_at}</span>
                      {isWriter ? (
                        <button disabled={busy} onClick={() => release(row.container_no)}>
                          进闸已落贴源，放行入库
                        </button>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="note">等候队列是空的。缺进闸或缺出闸的箱号不会进来。</p>
              )}
              {queueError ? <p className="warn">{queueError}</p> : null}
            </section>

            {reversal.marks.length ? (
              <section className="panel">
                <h3>逆序记录</h3>
                <ul>
                  {reversal.marks.map((mark) => (
                    <li key={mark.id}>
                      <span>
                        <em className="alert">{mark.mark_name}</em> · {mark.container_no} · {mark.actor} · {mark.marked_at}
                      </span>
                      {mark.carried_policy_name ? <span className="note">带上的办法：{mark.carried_policy_name}</span> : null}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            <div className="layers">
              <article>
                <h3>贴源层</h3>
                <ul>
                  {detail.ods.map((row) => (
                    <li key={row.id}>
                      <span>{row.terminal_code}</span>
                      <code>{JSON.stringify(row.raw_payload)}</code>
                    </li>
                  ))}
                </ul>
              </article>
              <article>
                <h3>明细层 · 已顺收</h3>
                {detail.moves.length ? (
                  <table>
                    <thead>
                      <tr><th>来源</th><th>箱号</th><th>事件</th><th>时间</th><th>收法</th></tr>
                    </thead>
                    <tbody>
                      {detail.moves.map((row) => (
                        <tr key={`${row.source_id}-${row.event_kind}`}>
                          <td>{row.source_id}</td>
                          <td>{row.container_no}</td>
                          <td>{row.event_kind}</td>
                          <td>{row.event_at}</td>
                          <td><em className={row.flow === "顺收" ? "" : "alert"}>{row.flow}</em></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : <p>这一天的明细还是空的。</p>}
                <h3>逆序未收</h3>
                {pendingGroups.length ? (
                  <ul>
                    {pendingGroups.map((group) => (
                      <li key={group.container_no}>
                        <span>{group.container_no}</span>
                        <em className="alert">{group.state_name}</em>
                      </li>
                    ))}
                  </ul>
                ) : <p>没有逆序未收的箱号。</p>}
                <h3>剔除</h3>
                <ul>
                  {detail.rejects.map((row) => (
                    <li key={row.source_id}><span>来源 {row.source_id}</span><em>{row.reason}</em></li>
                  ))}
                  {!detail.rejects.length ? <li>没有被剔除的扫描</li> : null}
                </ul>
              </article>
            </div>
          </section>
        ) : null}
      </main>
    </>
  );
}
