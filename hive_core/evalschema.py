"""Shared evaluation form schema (SDEF-02).

单一真源：服务端解析任务的有效表单字段，前端消费同一 schema
（GET /ui/api/task/:id 返回 eval_fields；前端兜底副本仅作离线显示）。
规范语义：未评分=null（0 是真实分值）；必填/可选与任务声明一致。
"""
from __future__ import annotations

from .errors import HiveError

# 与前端 content.js dualEvalFields 同一版本的兜底 schema（版本号随迁移更新）
DUAL_EVAL_SCHEMA_VERSION = 1

DUAL_EVAL_FIELDS = [
    {"id": "artifact_decision", "type": "single_select", "required": True,
     "label": "产物判决",
     "help": "通过 / 返工 / 放弃——拒绝发布是正常回答，不是任务失败",
     "options": [
         {"id": "pass", "label": "通过", "desc": "本轮产物达到采用标准"},
         {"id": "rework", "label": "返工", "desc": "方向可取，需按修改意见重做"},
         {"id": "abandon", "label": "放弃", "desc": "此路线不继续"}]},
    {"id": "artifact_score", "type": "score", "min": 0, "max": 100, "step": 1,
     "required": True, "label": "产物评分（0–100）",
     "help": "审核Agent预测分仅供参考，这里记录你的真实评分"},
    {"id": "review_decision", "type": "single_select", "required": True,
     "label": "审核评价", "help": "对审核意见本身的认可程度",
     "options": [
         {"id": "agree", "label": "认可", "desc": "审核判断准确、证据充分"},
         {"id": "partial", "label": "部分认可", "desc": "部分成立，有误判或遗漏"},
         {"id": "disagree", "label": "不认可", "desc": "审核结论不可靠"}]},
    {"id": "review_score", "type": "score", "min": 0, "max": 100, "step": 1,
     "required": True, "label": "审核评分（0–100）"},
    {"id": "keep", "type": "long_text", "required": False,
     "label": "应保留的内容", "placeholder": "每行一条；可留空",
     "multiline_list": True},
    {"id": "change", "type": "long_text", "required": False,
     "label": "需要修改的内容", "placeholder": "每行一条；可留空",
     "multiline_list": True},
    {"id": "review_agreements", "type": "long_text", "required": False,
     "label": "审核中判断对的要点", "placeholder": "每行一条；可留空",
     "multiline_list": True},
    {"id": "review_disagreements", "type": "long_text", "required": False,
     "label": "审核中判断错的地方", "placeholder": "每行一条；可留空",
     "multiline_list": True},
    {"id": "review_omissions", "type": "long_text", "required": False,
     "label": "审核漏掉的地方", "placeholder": "每行一条；可留空",
     "multiline_list": True},
]

DUAL_EVAL_IDS = {f["id"] for f in DUAL_EVAL_FIELDS}
DUAL_LIST_FIELDS = {"keep", "change", "review_agreements",
                    "review_disagreements", "review_omissions"}


def is_dual_eval(data: dict) -> bool:
    """判定任务是否按双评价合同作答（与前端 isDualEvalData 同规则）。"""
    if not isinstance(data, dict):
        return False
    if "artifact_score" in data and "review_score" in data:
        return True
    if data.get("request_type") == "dual_evaluation":
        return True
    if data.get("human_eval") is True and data.get("intent") in (
            "review_artifact", "feedback", "release"):
        return True
    return False


def effective_fields(task_data: dict) -> list[dict] | None:
    """有效表单解析规则（优先级）：显式合法 ui.fields > 双评价兜底 schema >
    普通任务（None，自由作答不误推断）。"""
    if not isinstance(task_data, dict):
        return None
    fields = (task_data.get("ui") or {}).get("fields")
    if isinstance(fields, list) and fields:
        return fields
    if is_dual_eval(task_data):
        return DUAL_EVAL_FIELDS
    return None


def canonicalize_answers(task_data: dict, data: dict,
                         fields: list[dict] | None = None) -> dict:
    """TDEF-03：以有效 schema 的全部声明字段 ID 统一解析两种答复表示
    （data.answers.<field> 与 data.<field>）——显式 fields 与兜底同规则，
    不再只用 DUAL_EVAL_IDS 白名单（显式表单的 name/rating/date 等同样归一）。
    同键两处且不同 → 400 冲突；相同 → 合法。"""
    if fields is None:
        fields = effective_fields(task_data) or []
    ids = {f.get("id") for f in fields if f.get("id")}
    # 自主授权对象（显式双评契约键）始终参与归一
    if is_dual_eval(task_data):
        ids.add("explicit_autonomy_grant")
    answers = {}
    if isinstance(data, dict) and isinstance(data.get("answers"), dict):
        answers = dict(data["answers"])
    top = {k: v for k, v in (data or {}).items() if k in ids}
    canon = dict(answers)
    for k, v in top.items():
        if k in answers and answers[k] != v:
            raise HiveError("INVALID_ARGUMENT",
                            f"字段 {k} 在 answers 与顶层同时出现且取值不同",
                            {"field_errors": [f"{k}: 两处取值冲突"]})
        canon[k] = v
    if isinstance(data, dict) and isinstance(
            data.get("explicit_autonomy_grant"), dict):
        canon["explicit_autonomy_grant"] = data["explicit_autonomy_grant"]
    return canon


def canonical_result(task_data: dict, canon: dict,
                     fields: list[dict] | None = None) -> dict:
    """TDEF-04：结构化表单的结果合同——只保留 schema 声明字段的规范值 +
    显式契约键（explicit_autonomy_grant）。未声明键（grant/authorize/
    _verified 等）一律不进规范结果；普通自由 data 任务不经此函数，
    合法业务 metadata 不受影响。"""
    if fields is None:
        fields = effective_fields(task_data) or []
    ids = {f.get("id") for f in fields if f.get("id")}
    ids |= DUAL_EVAL_IDS if is_dual_eval(task_data) else set()
    ids.add("explicit_autonomy_grant")
    out = {}
    for k, v in canon.items():
        if k not in ids:
            continue
        if k in DUAL_LIST_FIELDS:
            out[k] = (str(v).split("\n") if isinstance(v, str) else
                      list(v) if isinstance(v, list) else [])
            out[k] = [x.strip() for x in out[k] if x and x.strip()]
        elif k == "explicit_autonomy_grant":
            g = v if isinstance(v, dict) else None
            if g and g.get("grant") is True and (g.get("scope") or "").strip():
                out[k] = g
            else:
                out[k] = None
        elif isinstance(v, dict) and "choice" in v:
            out[k] = v["choice"]
            if isinstance(v.get("text"), str) and v["text"].strip():
                out[k + "_note"] = v["text"].strip()
        else:
            out[k] = v
    return out


def normalize_canonical(canon: dict) -> dict:
    """校验通过后的规范输出：列表字段拆行、剥离 _note、补契约 null 键。"""
    out = {}
    for k, v in canon.items():
        if k not in DUAL_EVAL_IDS and k != "explicit_autonomy_grant":
            continue
        if k in DUAL_LIST_FIELDS:
            out[k] = (str(v).split("\n") if isinstance(v, str) else
                      list(v) if isinstance(v, list) else [])
            out[k] = [x.strip() for x in out[k] if x and x.strip()]
        elif k == "explicit_autonomy_grant":
            g = v if isinstance(v, dict) else None
            if g and g.get("grant") is True and (g.get("scope") or "").strip():
                out[k] = g
            else:
                out[k] = None
        elif isinstance(v, dict) and "choice" in v:
            out[k] = v["choice"]
            if isinstance(v.get("text"), str) and v["text"].strip():
                out[k + "_note"] = v["text"].strip()
        else:
            out[k] = v
    # 契约键显式 null（未评分保留 null，不推断）
    out.setdefault("artifact_score", None)
    out.setdefault("review_score", None)
    out.setdefault("artifact_decision", None)
    out.setdefault("review_decision", None)
    out.setdefault("explicit_autonomy_grant", None)
    return out
