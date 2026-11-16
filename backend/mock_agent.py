import json
import re
import sys
from uuid import uuid4


def work(context):
    workflow = context.get("workflow") or {}
    profile = workflow.get("profile", "sdd-default")
    payload = workflow.get("input")
    if profile == "sdd-default":
        if context["wake_reason"] == "agent_wakeup":
            return {"outcome": "completed", "summary": "已到预约时间，在原会话完成模拟复查。", "question": None}
        timer = re.search(r"预约\s*(-?[0-9]+)\s*秒", context["input_summary"])
        if timer:
            return {
                "outcome": "schedule_wakeup", "summary": "等待到点后在原会话复查合成任务。",
                "question": None, "after_seconds": int(timer[1]),
            }
    if context["wake_reason"] == "manual_resume":
        return {
            "outcome": "completed", "summary": f"已在原会话处理补充指令：{context['input_summary']}"[:12000],
            "question": None,
        }
    replied = context["wake_reason"] == "human_replied"
    if profile == "review":
        sha = payload["diff"]["head_sha"]
        changed = re.search(r"^\+\+\+ b/(.+)$", payload["diff"]["text"], re.MULTILINE)
        findings = [{"path": changed[1][:300], "detail": "样例检查项：确认变更后的输入边界。"}] if changed else []
        return {
            "outcome": "completed",
            "summary": f"样例评审已处理 head {sha[:8]}，请对照变更确认输入边界。",
            "question": None,
            "output": {
                "reviewed_sha": sha,
                "findings": findings,
            },
        }
    if profile == "document" and replied:
        return {
            "outcome": "completed",
            "summary": "已根据人工回复保存文档更新建议。",
            "question": None,
            "output": {
                "commit_sha": payload["sha"], "document": "样例用户使用说明",
                "suggestion": f"根据「{payload['summary']}」更新对应说明。人工确认：{context['input_summary']}"[:12000],
            },
        }
    if profile == "document":
        return {
            "outcome": "needs_human",
            "summary": f"已分析合并提交 {payload['sha'][:8]} 的文档影响。",
            "question": f"「{payload['summary']}」是否需要写入用户使用说明？请确认适用范围。",
        }
    return {
        "outcome": "completed" if replied else "needs_human",
        "summary": (
            "已收到人工回复，模拟任务执行完成。" if replied else "模拟分析完成，请在待办中给出本次验收范围。"
        ),
        "question": None if replied else "请确认本次任务的验收范围。",
    }


def main():
    payload = sys.stdin.buffer.read()
    if not payload:
        return
    context = json.loads(payload)
    resume_id = context["resume_id"]
    if context["wake_reason"] in {"human_replied", "manual_resume", "agent_wakeup"} and not resume_id:
        raise ValueError("接续指令必须恢复已登记的会话")
    report = {
        "contract_version": "1",
        "task_id": context["task_id"],
        "run_id": context["run_id"],
        "generation": context["generation"],
        "session_id": resume_id or str(uuid4()),
        **work(context),
    }
    sys.stderr.buffer.write(f"Mock Agent 收到 {context['wake_reason']}，代次 {context['generation']}。\n".encode("utf-8"))
    sys.stdout.buffer.write(json.dumps(report, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
