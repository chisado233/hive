"""Minimal human web page: list human-port tasks, show detail, submit answer."""
from __future__ import annotations

import html
import json
import urllib.parse

from .errors import HiveError


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def render_human_page(svc, token: str, qs: dict) -> tuple[bytes, str]:
    """Very small page driven by the token query param (channel auth stand-in
    for V1; production deployments should use proper session auth)."""
    from .auth import Subject, Authenticator
    if not token:
        body = "<h1>Hive 人工处理</h1><p>缺少 token 参数。用法: /human/?token=&lt;凭据token&gt;</p>"
        return _page(body).encode("utf-8"), "text/html; charset=utf-8"
    subject = Authenticator(svc.store).authenticate(token)
    if subject.subject_type not in ("human", "owner", "system"):
        raise HiveError("FORBIDDEN", "该身份不能使用人工页面")

    action = qs.get("action", "")
    base = "/human/?token=" + urllib.parse.quote(token)
    if action == "answer" and qs.get("task"):
        return _do_answer(svc, subject, qs, base)
    # list
    items = _list_items(svc, subject)
    rows = []
    for it in items:
        rows.append(
            f"<tr><td><code>{_esc(it['task_id'])}</code></td>"
            f"<td>{_esc(it['title'])}</td><td>{_esc(it['state'])}</td>"
            f"<td>v{_esc(it['spec_version'])}</td>"
            f"<td><a href='{base}&task={urllib.parse.quote(it['task_id'])}'>打开</a>"
            f"</td></tr>")
    body = [f"<h1>Hive 人工处理</h1><p>身份: {_esc(subject.ref)}</p>",
            "<table border=1 cellpadding=4><tr><th>Task</th><th>标题</th>"
            "<th>状态</th><th>版本</th><th></th></tr>", *rows, "</table>"]
    if qs.get("task"):
        body.append(_render_detail(svc, subject, qs["task"], base))
    return _page("\n".join(body)).encode("utf-8"), "text/html; charset=utf-8"


def _list_items(svc, subject):
    from .handlers_ext import h_human_list
    data = h_human_list(svc, subject, {"state": "queued"}, None)
    return data["items"]


def _render_detail(svc, subject, task_id: str, base: str) -> str:
    from .tasks import get_task, can_read_task
    row = get_task(svc, task_id)
    if not can_read_task(svc, subject, row):
        return "<p>任务不可见</p>"
    data = json.loads(row["data"])
    opts = data.get("options") or []
    opt_buttons = "".join(
        f"<button type='submit' name='choice' value='{_esc(o.get('id'))}'>"
        f"{_esc(o.get('label') or o.get('id'))}</button> " for o in opts)
    return f"""
<h2>{_esc(row['title'])} <small>(spec_version {row['spec_version']})</small></h2>
<pre>{_esc(row['body'])}</pre>
<p><b>data</b>: <code>{_esc(json.dumps(data, ensure_ascii=False))}</code></p>
<form method='get' action='/human/answer'>
  <input type='hidden' name='token' value='{_esc(base.split('token=')[1])}'>
  <input type='hidden' name='task' value='{_esc(task_id)}'>
  <input type='hidden' name='spec_version' value='{row['spec_version']}'>
  <label>回答: <input name='text' size='60'></label>
  {opt_buttons}
  <button type='submit' name='decision' value='approve-done'>提交(done)</button>
  <button type='submit' name='decision' value='deny-done'>否决(done)</button>
  <button type='submit' name='decision' value='fail'>无法回答(fail)</button>
</form>
<p><small>否决(deny)是正常回答（done + data.decision=deny），不是任务失败。</small></p>
"""


def _do_answer(svc, subject, qs, base) -> tuple[bytes, str]:
    from .handlers_ext import h_human_answer, h_human_fail
    task_id = qs.get("task", "")
    spec_version = int(qs.get("spec_version", "0"))
    decision = qs.get("decision", "approve-done")
    choice = qs.get("choice")
    text = qs.get("text", "")
    args = {"task": task_id, "spec_version": spec_version}
    if decision == "fail":
        args["reason"] = text or "无法回答"
        data = h_human_fail(svc, subject, args, None)
        msg = "已提交 fail"
    else:
        if text or not choice:
            args["text"] = text
        d = {}
        if decision == "deny-done":
            d["decision"] = "deny"
        if choice:
            d["choice"] = choice
        if d:
            args["data"] = d
        data = h_human_answer(svc, subject, args, None)
        msg = "已提交回答 (done)"
    state = data.get("task", {}).get("state")
    body = f"<h1>{msg}</h1><p>task={_esc(task_id)} state={_esc(state)}</p>" \
           f"<p><a href='{base}'>返回列表</a></p>"
    return _page(body).encode("utf-8"), "text/html; charset=utf-8"


def _page(body: str) -> str:
    return f"""<!doctype html><html><head><meta charset='utf-8'>
<title>Hive 人工处理</title></head><body>{body}</body></html>"""
