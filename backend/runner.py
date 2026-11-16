import logging
import subprocess
from pathlib import Path
from time import monotonic

import psutil
from pydantic import ValidationError

from .agent import AgentOutputError, WINDOW_HIDDEN
from .contracts import MAX_WAKEUP_CHAIN, MAX_WAKEUP_SECONDS, MIN_WAKEUP_SECONDS, TaskContext
from .workspace import Conflict

logger = logging.getLogger(__name__)
RUNNER_UNAVAILABLE = "原会话的 Runner 未启用。请按运行手册启用后重新排队。"


def probe(run):
    if run["pid"] is None:
        return "unstarted", None
    try:
        process = psutil.Process(run["pid"])
        if process.create_time() != run["process_created_at"] or process.status() == psutil.STATUS_ZOMBIE:
            return "exited", None
    except psutil.NoSuchProcess:
        return "exited", None
    except (psutil.Error, OSError):
        return "unknown", None
    return "alive", process


class Runner:
    def __init__(self, tasks, stop, agents, refresh_subject=None):
        self.tasks = tasks
        self.stop = stop
        self.agents = {agent.mode: agent for agent in agents}
        self.timeout = max(agent.timeout for agent in agents)
        self.refresh_subject = refresh_subject

    def check_interrupted(self):
        messages = {
            "unstarted": "服务中断于进程登记前。确认工作区后可重新排队。",
            "exited": "服务中断，已确认旧进程退出。检查工作区后可重新排队。",
            "alive": "旧进程仍在运行。停止该进程或等待其退出后再恢复。",
            "unknown": "无法确认旧进程已退出。请检查运行账号和系统权限后重试。",
        }
        for run in self.tasks.unfinished():
            state, _ = probe(run)
            self.tasks.require_recovery(run, messages[state], process_exited=state in {"exited", "unstarted"})

    def recover(self, run_id, operation):
        run = self.tasks.recovery_run(run_id)
        if operation == "retry" and run["runner"] and run["runner"] not in self.agents:
            raise Conflict(RUNNER_UNAVAILABLE)
        state, process = probe(run)
        if operation == "stop" and process is not None:
            try:
                process.kill()
                process.wait(timeout=3)
            except psutil.NoSuchProcess:
                pass
            except (psutil.Error, OSError) as error:
                raise Conflict("无法安全停止旧进程，请检查运行账号和系统权限。") from error
            state, _ = probe(run)
        if state in {"alive", "unknown"}:
            raise Conflict("旧进程仍在运行或无法确认退出，无法恢复任务。")
        self.tasks.recover(run, retry=operation == "retry")

    def run(self, run):
        agent = self.agents.get(run["runner"])
        if agent is None:
            self.tasks.require_recovery(run, RUNNER_UNAVAILABLE)
            return
        try:
            self.tasks.verify_workspace(run)
        except Conflict as error:
            self.tasks.require_recovery(run, str(error))
            return

        context = TaskContext(
            task_id=run["task_id"], run_id=run["run_id"], generation=run["generation"],
            resume_id=run["resume_id"], wake_reason=run["kind"], title=run["title"],
            prompt=run["prompt"], input_summary=run["summary"],
            workflow=run["workflow"],
            scheduled_wakeups_left=MAX_WAKEUP_CHAIN - run["chain_depth"],
        )
        with Path(run["log_path"]).open("w", encoding="utf-8") as log:
            try:
                process = subprocess.Popen(
                    agent.command(context),
                    cwd=run["workspace"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=log if agent.mode == "mock" else subprocess.DEVNULL,
                    env=agent.environment, creationflags=WINDOW_HIDDEN,
                )
            except OSError:
                self.tasks.require_recovery(run, "无法启动 Agent。检查工作区和运行环境后重试。")
                return

            try:
                # 子进程读到上下文前只等待输入。父进程退出时管道关闭，尚未登记的子进程不会执行任务。
                created_at = psutil.Process(process.pid).create_time()
                if process.poll() is not None:
                    raise Conflict("Agent 在进程登记前退出。")
                if not self.tasks.attach_process(run, process.pid, created_at):
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=3)
                    return
                self.tasks.verify_workspace(run)
                output = self.communicate(process, context.model_dump_json().encode("utf-8"), agent.timeout)
            except BaseException:
                if process.poll() is None:
                    try:
                        process.kill()
                        process.wait(timeout=3)
                    except (OSError, subprocess.TimeoutExpired):
                        self.tasks.require_recovery(run, "无法确认本轮进程退出，恢复前必须核对进程。")
                        raise
                raise
            finally:
                process.stdin.close()
                process.stdout.close()

        if output is None:
            self.tasks.require_recovery(run, "运行因服务关闭或超时而停止。检查后可重新排队。", process_exited=True)
            return
        if process.returncode:
            self.tasks.finish(run, error="Agent 异常退出。请检查模型授权、网关或 CLI 配置。", exit_code=process.returncode)
            return
        try:
            result = agent.result(output, context)
        except (ValueError, AgentOutputError) as error:
            summary = "Agent 输出缺失或不符合运行协议。"
            if isinstance(error, ValidationError) and any(
                issue["loc"] == ("after_seconds",) for issue in error.errors(include_input=False)
            ):
                summary = f"预约唤醒须使用 {MIN_WAKEUP_SECONDS} 至 {MAX_WAKEUP_SECONDS} 的整数秒。"
            self.tasks.finish(run, error=summary, exit_code=0)
            return
        if self.refresh_subject is not None and not (context.workflow or {}).get("manual_follow_up"):
            try:
                self.refresh_subject(run)
            except Exception:
                logger.exception("提交运行结果前检查工作流输入失败")
                self.tasks.require_recovery(run, "无法核验主题的最新版本。恢复数据源后可重新排队。", process_exited=True)
                return
        if agent.mode != "mock":
            with Path(run["log_path"]).open("a", encoding="utf-8") as log:
                print(f"Claude CLI 完成第 {context.generation} 轮，结果通过结构校验。", file=log)
        try:
            accepted = self.tasks.finish(run, result, exit_code=0)
        except ValueError:
            self.tasks.finish(run, error="工作流结果与约定的数据或版本不匹配。", exit_code=0)
            return
        if not accepted:
            self.tasks.finish(run, error="Agent 结果与本轮运行或绑定会话不匹配。", exit_code=0)

    def communicate(self, process, payload, timeout):
        deadline = monotonic() + timeout
        pending_input = payload
        while not self.stop.is_set() and monotonic() < deadline:
            try:
                output, _ = process.communicate(input=pending_input, timeout=0.25)
                return output
            except subprocess.TimeoutExpired:
                pending_input = None
        process.kill()
        process.wait(timeout=3)
        return None
