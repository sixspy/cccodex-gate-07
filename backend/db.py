import json
import os
import time

import psycopg
from psycopg.rows import dict_row

from build import POLICY, clean_scan, group_by_container

SCHEMA = """
CREATE TABLE IF NOT EXISTS ods_scan (
    id BIGSERIAL PRIMARY KEY,
    biz_date DATE NOT NULL,
    terminal_code TEXT NOT NULL,
    raw_payload JSONB NOT NULL,
    landed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dwd_move (
    id BIGSERIAL PRIMARY KEY,
    biz_date DATE NOT NULL,
    source_id BIGINT NOT NULL REFERENCES ods_scan(id),
    terminal_code TEXT NOT NULL,
    container_no TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    event_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS build_runs (
    id BIGSERIAL PRIMARY KEY,
    biz_date DATE NOT NULL,
    accepted_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL,
    built_by TEXT NOT NULL,
    built_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS build_rejects (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES build_runs(id),
    source_id BIGINT NOT NULL REFERENCES ods_scan(id),
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reversal_settings (
    biz_date DATE PRIMARY KEY,
    policy TEXT NOT NULL,
    set_by TEXT NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS reversal_groups (
    id BIGSERIAL PRIMARY KEY,
    biz_date DATE NOT NULL,
    container_no TEXT NOT NULL,
    state TEXT NOT NULL,
    policy TEXT NOT NULL,
    actor TEXT NOT NULL,
    run_id BIGINT,
    resolved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (biz_date, container_no)
);

CREATE TABLE IF NOT EXISTS waiting_queue (
    id BIGSERIAL PRIMARY KEY,
    biz_date DATE NOT NULL,
    container_no TEXT NOT NULL,
    source_id BIGINT NOT NULL REFERENCES ods_scan(id),
    event_at TIMESTAMP NOT NULL,
    queued_by TEXT NOT NULL,
    queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS reversal_marks (
    id BIGSERIAL PRIMARY KEY,
    biz_date DATE NOT NULL,
    container_no TEXT NOT NULL,
    mark TEXT NOT NULL,
    carried_policy TEXT,
    actor TEXT NOT NULL,
    run_id BIGINT,
    marked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def init_db():
    last_error = None
    for _ in range(30):
        try:
            with connect() as conn:
                conn.execute(SCHEMA)
                count = conn.execute("SELECT COUNT(*) AS n FROM ods_scan").fetchone()["n"]
                if count == 0:
                    _seed(conn)
                conn.commit()
            return
        except psycopg.OperationalError as exc:
            last_error = exc
            time.sleep(1)
    raise last_error


def _land(conn, biz_date, terminal, payload):
    conn.execute(
        """
        INSERT INTO ods_scan (biz_date, terminal_code, raw_payload)
        VALUES (%s, %s, %s::jsonb)
        """,
        (biz_date, terminal, json.dumps(payload, ensure_ascii=False)),
    )


def _seed(conn):
    days = {
        # 一进一出，时刻顺着走，直接顺收
        "2026-11-11": [
            {"scan": "IN", "box": "CSNU1029384", "at": "2026-11-11T07:40:00", "gate": "T1"},
            {"scan": "OUT", "box": "CSNU1029384", "at": "2026-11-11T16:05:00", "gate": "T1"},
        ],
        # 字典外的扫描，留在剔除清单
        "2026-11-12": [
            {"scan": "IN", "box": "TCLU5566778", "at": "2026-11-12T09:15:00", "gate": "T2"},
            {"scan": "XX", "box": "TCLU5566778", "at": "2026-11-12T11:00:00", "gate": "T2"},
            {"scan": "RS", "box": "BAD", "at": "2026-11-12T12:00:00", "gate": "T2"},
        ],
        # WHSU7000001 出闸早于进闸，逆序办法还没写死，这一箱不准收成
        "2026-11-13": [
            {"scan": "OUT", "box": "WHSU7000001", "at": "2026-11-13T08:20:00", "gate": "T1"},
            {"scan": "IN", "box": "WHSU7000001", "at": "2026-11-13T10:00:00", "gate": "T1"},
            {"scan": "IN", "box": "MNBU3111111", "at": "2026-11-13T09:05:00", "gate": "T1"},
            {"scan": "OUT", "box": "MNBU3111111", "at": "2026-11-13T17:20:00", "gate": "T1"},
        ],
        # YMLU8000002 逆序，办法写死为停等候队列；SOCU9000003 缺进闸，不进等候队列
        "2026-11-14": [
            {"scan": "OUT", "box": "YMLU8000002", "at": "2026-11-14T09:10:00", "gate": "T2"},
            {"scan": "IN", "box": "YMLU8000002", "at": "2026-11-14T13:40:00", "gate": "T2"},
            {"scan": "OUT", "box": "SOCU9000003", "at": "2026-11-14T11:00:00", "gate": "T2"},
            {"scan": "IN", "box": "CRLU4222222", "at": "2026-11-14T08:00:00", "gate": "T2"},
            {"scan": "OUT", "box": "CRLU4222222", "at": "2026-11-14T15:30:00", "gate": "T2"},
        ],
        # UETU6000004 逆序，办法写死为整组不进明细，三条整组丢掉
        "2026-11-15": [
            {"scan": "OUT", "box": "UETU6000004", "at": "2026-11-15T07:50:00", "gate": "T1"},
            {"scan": "IN", "box": "UETU6000004", "at": "2026-11-15T12:30:00", "gate": "T1"},
            {"scan": "RS", "box": "UETU6000004", "at": "2026-11-15T14:00:00", "gate": "T1"},
        ],
    }
    for biz_date, payloads in days.items():
        for payload in payloads:
            _land(conn, biz_date, payload["gate"], payload)
    # 11-14 的办法是昨天写死的，今天还可以回调；11-15 是今天写死的，当日不许回调
    conn.execute(
        """
        INSERT INTO reversal_settings (biz_date, policy, set_by, set_at)
        VALUES ('2026-11-14', 'wait_in', 'admin', NOW() - INTERVAL '1 day')
        """
    )
    conn.execute(
        """
        INSERT INTO reversal_settings (biz_date, policy, set_by, set_at)
        VALUES ('2026-11-15', 'drop_group', 'admin', NOW())
        """
    )
    rebuild_day(conn, "2026-11-11", "admin")
    rebuild_day(conn, "2026-11-14", "admin", "wait_in")
    rebuild_day(conn, "2026-11-15", "admin", "drop_group")


def _lock_segment(conn, biz_date):
    # 抢段加锁：同一业务日同时只许一个建层员动手
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"gate07:{biz_date}",))


def _load_events(conn, biz_date):
    """读出一天的贴源扫描，返回 (清洗后的事件列表, 剔除列表)。"""
    rows = conn.execute(
        "SELECT id, terminal_code, raw_payload FROM ods_scan WHERE biz_date = %s ORDER BY id",
        (biz_date,),
    ).fetchall()
    events = []
    rejects = []
    for row in rows:
        payload = row["raw_payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        cleaned, reason = clean_scan(payload)
        if reason:
            rejects.append({"source_id": row["id"], "reason": reason})
            continue
        cleaned["source_id"] = row["id"]
        cleaned["terminal_code"] = row["terminal_code"]
        events.append(cleaned)
    return events, rejects


def _insert_move(conn, biz_date, event):
    conn.execute(
        """
        INSERT INTO dwd_move (biz_date, source_id, terminal_code, container_no, event_kind, event_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            biz_date,
            event["source_id"],
            event["terminal_code"],
            event["container_no"],
            event["event_kind"],
            event["event_at"],
        ),
    )


def _mark(conn, biz_date, container_no, mark, carried_policy, actor, run_id):
    conn.execute(
        """
        INSERT INTO reversal_marks (biz_date, container_no, mark, carried_policy, actor, run_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (biz_date, container_no, mark, carried_policy, actor, run_id),
    )


def rebuild_day(conn, biz_date: str, username: str, carried_policy: str | None = None):
    _lock_segment(conn, biz_date)
    setting = conn.execute(
        "SELECT policy FROM reversal_settings WHERE biz_date = %s",
        (biz_date,),
    ).fetchone()
    conn.execute("DELETE FROM dwd_move WHERE biz_date = %s", (biz_date,))
    events, rejects = _load_events(conn, biz_date)
    run = conn.execute(
        """
        INSERT INTO build_runs (biz_date, accepted_count, rejected_count, built_by)
        VALUES (%s, 0, 0, %s)
        RETURNING id
        """,
        (biz_date, username),
    ).fetchone()
    accepted = 0
    for reject in rejects:
        conn.execute(
            "INSERT INTO build_rejects (run_id, source_id, reason) VALUES (%s, %s, %s)",
            (run["id"], reject["source_id"], reject["reason"]),
        )
    for container_no, group in group_by_container(events).items():
        if not group["reversed"]:
            for event in group["events"]:
                _insert_move(conn, biz_date, event)
                accepted += 1
            continue
        if setting is None:
            # 逆序尚未写死，不准收成这一箱
            continue
        if carried_policy and carried_policy != setting["policy"]:
            # 带上的办法跟逆序设置不合，这组停写
            _mark(conn, biz_date, container_no, "policy_mismatch", carried_policy, username, run["id"])
            continue
        state = conn.execute(
            """
            SELECT state, actor FROM reversal_groups
            WHERE biz_date = %s AND container_no = %s
            """,
            (biz_date, container_no),
        ).fetchone()
        if state:
            if state["actor"] != username:
                # 另一侧建层员已经定过局，这一边记逆序争用
                _mark(conn, biz_date, container_no, "contention", carried_policy, username, run["id"])
            if state["state"] == "ingested":
                for event in group["events"]:
                    _insert_move(conn, biz_date, event)
                    accepted += 1
            continue
        if setting["policy"] == "drop_group":
            conn.execute(
                """
                INSERT INTO reversal_groups (biz_date, container_no, state, policy, actor, run_id)
                VALUES (%s, %s, 'dropped', %s, %s, %s)
                """,
                (biz_date, container_no, setting["policy"], username, run["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO reversal_groups (biz_date, container_no, state, policy, actor, run_id)
                VALUES (%s, %s, 'waiting', %s, %s, %s)
                """,
                (biz_date, container_no, setting["policy"], username, run["id"]),
            )
            for event in group["events"]:
                if event["event_kind"] == "出闸":
                    conn.execute(
                        """
                        INSERT INTO waiting_queue (biz_date, container_no, source_id, event_at, queued_by)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (biz_date, container_no, event["source_id"], event["event_at"], username),
                    )
    conn.execute(
        "UPDATE build_runs SET accepted_count = %s, rejected_count = %s WHERE id = %s",
        (accepted, len(rejects), run["id"]),
    )
    return run["id"]


def set_reversal_policy(conn, biz_date: str, policy: str, username: str):
    if policy not in POLICY:
        return "逆序办法只能二选一"
    locked = conn.execute(
        """
        SELECT 1 FROM reversal_settings
        WHERE biz_date = %s AND set_at::date = CURRENT_DATE
        """,
        (biz_date,),
    ).fetchone()
    if locked:
        return "逆序办法当日已写死，当日不许回调"
    conn.execute(
        """
        INSERT INTO reversal_settings (biz_date, policy, set_by)
        VALUES (%s, %s, %s)
        ON CONFLICT (biz_date) DO UPDATE
        SET policy = EXCLUDED.policy, set_by = EXCLUDED.set_by, set_at = NOW()
        """,
        (biz_date, policy, username),
    )
    return None


def release_container(conn, biz_date: str, container_no: str, username: str):
    _lock_segment(conn, biz_date)
    group = conn.execute(
        """
        SELECT state FROM reversal_groups
        WHERE biz_date = %s AND container_no = %s
        FOR UPDATE
        """,
        (biz_date, container_no),
    ).fetchone()
    if not group or group["state"] != "waiting":
        return "这箱不在等候队列里"
    events, _ = _load_events(conn, biz_date)
    own = [event for event in events if event["container_no"] == container_no]
    if not any(event["event_kind"] == "进闸" for event in own):
        return "进闸还没落到贴源，不能放行"
    for event in own:
        _insert_move(conn, biz_date, event)
    conn.execute(
        """
        UPDATE reversal_groups SET state = 'ingested', actor = %s, resolved_at = NOW()
        WHERE biz_date = %s AND container_no = %s
        """,
        (username, biz_date, container_no),
    )
    conn.execute(
        """
        UPDATE waiting_queue SET released_at = NOW()
        WHERE biz_date = %s AND container_no = %s AND released_at IS NULL
        """,
        (biz_date, container_no),
    )
    return None


def reversal_overview(conn, biz_date: str):
    setting = conn.execute(
        """
        SELECT policy, set_by, set_at, (set_at::date = CURRENT_DATE) AS locked_today
        FROM reversal_settings WHERE biz_date = %s
        """,
        (biz_date,),
    ).fetchone()
    events, _ = _load_events(conn, biz_date)
    groups = group_by_container(events)
    reversed_groups = {box: group for box, group in groups.items() if group["reversed"]}
    states = conn.execute(
        """
        SELECT container_no, state, policy, actor, resolved_at
        FROM reversal_groups WHERE biz_date = %s
        """,
        (biz_date,),
    ).fetchall()
    state_by_box = {row["container_no"]: row for row in states}
    waiting_counts = conn.execute(
        """
        SELECT container_no, COUNT(*) AS n FROM waiting_queue
        WHERE biz_date = %s AND released_at IS NULL
        GROUP BY container_no
        """,
        (biz_date,),
    ).fetchall()
    waiting_by_box = {row["container_no"]: row["n"] for row in waiting_counts}
    overview_groups = []
    for box, group in sorted(reversed_groups.items()):
        state = state_by_box.get(box)
        overview_groups.append(
            {
                "container_no": box,
                "in_at": group["in_at"],
                "out_at": group["out_at"],
                "gap_minutes": int((group["in_at"] - group["out_at"]).total_seconds() // 60),
                "state": state["state"] if state else "unset",
                "policy": (state or {}).get("policy") or (setting or {}).get("policy"),
                "actor": (state or {}).get("actor"),
                "resolved_at": (state or {}).get("resolved_at"),
                "waiting_count": waiting_by_box.get(box, 0),
                "dropped_count": len(group["events"]) if state and state["state"] == "dropped" else 0,
                "events": sorted(group["events"], key=lambda event: event["event_at"]),
            }
        )
    queue = conn.execute(
        """
        SELECT id, container_no, source_id, event_at, queued_by, queued_at
        FROM waiting_queue
        WHERE biz_date = %s AND released_at IS NULL
        ORDER BY id
        """,
        (biz_date,),
    ).fetchall()
    marks = conn.execute(
        """
        SELECT id, container_no, mark, carried_policy, actor, marked_at
        FROM reversal_marks WHERE biz_date = %s
        ORDER BY id DESC
        """,
        (biz_date,),
    ).fetchall()
    return {
        "setting": setting,
        "groups": overview_groups,
        "queue": queue,
        "marks": marks,
        "reversed_boxes": sorted(reversed_groups),
    }


def pending_reversals(conn, biz_date: str):
    events, _ = _load_events(conn, biz_date)
    reversed_boxes = [
        box for box, group in group_by_container(events).items() if group["reversed"]
    ]
    if not reversed_boxes:
        return 0
    done = conn.execute(
        """
        SELECT container_no FROM reversal_groups
        WHERE biz_date = %s AND state IN ('dropped', 'ingested')
        """,
        (biz_date,),
    ).fetchall()
    done_boxes = {row["container_no"] for row in done}
    return sum(1 for box in reversed_boxes if box not in done_boxes)
