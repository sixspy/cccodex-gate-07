import os

from flask import Flask, jsonify, request

from auth import authenticate, issue_token, read_user
from build import MARK_NAMES, POLICY, STATE_NAMES
from db import (
    connect,
    init_db,
    pending_reversals,
    rebuild_day,
    release_container,
    reversal_overview,
    set_reversal_policy,
)

app = Flask(__name__)
app.json.ensure_ascii = False


def _stamp(value):
    if value is None:
        return None
    return value.isoformat(sep=" ", timespec="minutes")


def _user():
    return read_user(request.headers.get("Authorization"))


def _writer():
    user = _user()
    if not user:
        return None, (jsonify({"detail": "未登录"}), 401)
    if user["role"] != "writer":
        return None, (jsonify({"detail": "核对员不能动手建层"}), 403)
    return user, None


def _shape_reversal(reversal):
    setting = reversal["setting"]
    if setting:
        setting = {
            **setting,
            "set_at": _stamp(setting["set_at"]),
            "policy_name": POLICY[setting["policy"]],
        }
    groups = []
    for group in reversal["groups"]:
        groups.append(
            {
                **group,
                "in_at": _stamp(group["in_at"]),
                "out_at": _stamp(group["out_at"]),
                "resolved_at": _stamp(group["resolved_at"]),
                "state_name": STATE_NAMES[group["state"]],
                "policy_name": POLICY.get(group["policy"]),
                "events": [
                    {
                        "source_id": event["source_id"],
                        "event_kind": event["event_kind"],
                        "event_at": _stamp(event["event_at"]),
                    }
                    for event in group["events"]
                ],
            }
        )
    for row in reversal["queue"]:
        row["event_at"] = _stamp(row["event_at"])
        row["queued_at"] = _stamp(row["queued_at"])
    for row in reversal["marks"]:
        row["marked_at"] = _stamp(row["marked_at"])
        row["mark_name"] = MARK_NAMES[row["mark"]]
        row["carried_policy_name"] = POLICY.get(row["carried_policy"])
    return {
        "setting": setting,
        "groups": groups,
        "queue": reversal["queue"],
        "marks": reversal["marks"],
        "reversed_boxes": reversal["reversed_boxes"],
    }


@app.get("/api/health")
def health():
    with connect() as conn:
        conn.execute("SELECT 1")
    return jsonify({"status": "ok", "service": "gate-event-lake"})


@app.post("/api/login")
def login():
    body = request.get_json(silent=True) or {}
    user = authenticate(str(body.get("username", "")).strip(), str(body.get("password", "")))
    if not user:
        return jsonify({"detail": "用户名或密码错误"}), 401
    return jsonify(
        {
            "access_token": issue_token(user),
            "username": user["username"],
            "role": user["role"],
            "title": user["title"],
        }
    )


@app.get("/api/dates")
def list_dates():
    if not _user():
        return jsonify({"detail": "未登录"}), 401
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT d.biz_date,
                   (SELECT COUNT(*) FROM ods_scan o WHERE o.biz_date = d.biz_date) AS ods_count,
                   (SELECT COUNT(*) FROM dwd_move m WHERE m.biz_date = d.biz_date) AS dwd_count,
                   EXISTS(SELECT 1 FROM build_runs r WHERE r.biz_date = d.biz_date) AS built
            FROM (SELECT DISTINCT biz_date FROM ods_scan) d
            ORDER BY d.biz_date
            """
        ).fetchall()
        for row in rows:
            row["biz_date"] = row["biz_date"].isoformat()
            row["pending_reversals"] = pending_reversals(conn, row["biz_date"])
    return jsonify(rows)


@app.get("/api/dates/<biz_date>")
def date_detail(biz_date):
    if not _user():
        return jsonify({"detail": "未登录"}), 401
    with connect() as conn:
        ods = conn.execute(
            """
            SELECT id, terminal_code, raw_payload
            FROM ods_scan WHERE biz_date = %s ORDER BY id
            """,
            (biz_date,),
        ).fetchall()
        moves = conn.execute(
            """
            SELECT source_id, terminal_code, container_no, event_kind, event_at
            FROM dwd_move WHERE biz_date = %s ORDER BY event_at, id
            """,
            (biz_date,),
        ).fetchall()
        run = conn.execute(
            """
            SELECT id, accepted_count, rejected_count, built_by, built_at
            FROM build_runs WHERE biz_date = %s ORDER BY id DESC LIMIT 1
            """,
            (biz_date,),
        ).fetchone()
        rejects = []
        if run:
            rejects = conn.execute(
                """
                SELECT source_id, reason FROM build_rejects
                WHERE run_id = %s ORDER BY source_id
                """,
                (run["id"],),
            ).fetchall()
        reversal = _shape_reversal(reversal_overview(conn, biz_date))
    if not ods:
        return jsonify({"detail": "这一天没有贴源扫描"}), 404
    reversed_boxes = set(reversal["reversed_boxes"])
    for move in moves:
        move["event_at"] = _stamp(move["event_at"])
        move["flow"] = "逆序放行" if move["container_no"] in reversed_boxes else "顺收"
    if run:
        run["built_at"] = _stamp(run["built_at"])
    return jsonify(
        {
            "biz_date": biz_date,
            "ods": ods,
            "moves": moves,
            "run": run,
            "rejects": rejects,
            "reversal": reversal,
        }
    )


@app.post("/api/dates/<biz_date>/build")
def build_date(biz_date):
    user, error = _writer()
    if error:
        return error
    body = request.get_json(silent=True) or {}
    carried_policy = body.get("policy") or None
    if carried_policy is not None and carried_policy not in POLICY:
        return jsonify({"detail": "逆序办法只能二选一"}), 400
    with connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM ods_scan WHERE biz_date = %s LIMIT 1",
            (biz_date,),
        ).fetchone()
        if not exists:
            return jsonify({"detail": "这一天没有贴源扫描"}), 404
        rebuild_day(conn, biz_date, user["username"], carried_policy)
        conn.commit()
    return date_detail(biz_date)


@app.put("/api/dates/<biz_date>/reversal-setting")
def write_reversal_setting(biz_date):
    user, error = _writer()
    if error:
        return error
    body = request.get_json(silent=True) or {}
    policy = body.get("policy")
    with connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM ods_scan WHERE biz_date = %s LIMIT 1",
            (biz_date,),
        ).fetchone()
        if not exists:
            return jsonify({"detail": "这一天没有贴源扫描"}), 404
        problem = set_reversal_policy(conn, biz_date, str(policy or ""), user["username"])
        if problem:
            conn.rollback()
            status = 400 if "二选一" in problem else 409
            return jsonify({"detail": problem}), status
        conn.commit()
    return date_detail(biz_date)


@app.post("/api/dates/<biz_date>/waiting/<container_no>/release")
def release_waiting(biz_date, container_no):
    user, error = _writer()
    if error:
        return error
    with connect() as conn:
        problem = release_container(conn, biz_date, container_no, user["username"])
        if problem:
            conn.rollback()
            return jsonify({"detail": problem}), 409
        conn.commit()
    return date_detail(biz_date)


init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
