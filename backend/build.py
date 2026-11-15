import re
from datetime import datetime

KIND = {"IN": "进闸", "OUT": "出闸", "RS": "倒箱"}
BOX = re.compile(r"^[A-Z]{4}\d{7}$")

# 逆序办法只能二选一
POLICY = {
    "drop_group": "整组不进明细",
    "wait_in": "出闸停等候队列等进闸",
}

STATE_NAMES = {
    "unset": "逆序未写死",
    "waiting": "等候中",
    "dropped": "整组已丢掉",
    "ingested": "已放行入库",
}

MARK_NAMES = {
    "policy_mismatch": "逆序办法不符",
    "contention": "逆序争用",
}


def clean_scan(payload: dict):
    scan = str(payload.get("scan") or "").strip().upper()
    box = str(payload.get("box") or "").strip().upper()
    stamp = str(payload.get("at") or "").strip()
    if scan not in KIND:
        return None, "事件码不在字典"
    if not BOX.match(box):
        return None, "箱号不是四位字母加七位数字"
    try:
        event_at = datetime.fromisoformat(stamp)
    except ValueError:
        return None, "扫描时间无法读取"
    return {
        "container_no": box,
        "event_kind": KIND[scan],
        "event_at": event_at,
    }, None


def group_by_container(events):
    """把清洗后的事件按箱号分组，标出逆序组（出闸时刻早于进闸时刻）。

    缺进闸或缺出闸的箱号不构成逆序，也不会进等候队列。
    """
    groups = {}
    for event in events:
        group = groups.setdefault(
            event["container_no"], {"events": [], "in_at": None, "out_at": None}
        )
        group["events"].append(event)
        if event["event_kind"] == "进闸":
            group["in_at"] = _min(group["in_at"], event["event_at"])
        elif event["event_kind"] == "出闸":
            group["out_at"] = _min(group["out_at"], event["event_at"])
    for group in groups.values():
        group["reversed"] = (
            group["in_at"] is not None
            and group["out_at"] is not None
            and group["out_at"] < group["in_at"]
        )
    return groups


def _min(left, right):
    return right if left is None or right < left else left
