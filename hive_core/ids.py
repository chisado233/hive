"""ID generation per DESIGN §2.1."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

_PREFIXES = {
    "agent": "agt_", "instance": "ins_", "port": "prt_", "task": "tsk_",
    "comment": "cmt_", "wait": "wtt_", "operation": "op_", "event": "evt_",
    "producer": "prd_", "trigger": "trg_", "channel": "chn_", "subject": "subj_",
    "grant": "gnt_", "claim": "clm_", "request": "req_", "delivery": "dlv_",
    "audit": "aud_", "view": "view_", "decline": "dcl_", "transfer": "xfr_",
    "credential": "crd_", "backup": "bkp_", "file": "file_",
    "input": "inp_", "turn": "trn_", "row": "row_", "toolcall": "tcl_",
    "binding": "bnd_",
}


def new_id(kind: str) -> str:
    prefix = _PREFIXES[kind]
    return prefix + secrets.token_hex(10)


def new_token() -> str:
    return "hvt_" + secrets.token_urlsafe(32)


def new_request_id() -> str:
    return _PREFIXES["request"] + secrets.token_hex(12)


def machine_ts(dt: datetime | None = None) -> float:
    return (dt or datetime.now(timezone.utc)).timestamp()
