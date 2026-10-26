"""把 C01–C30 换成马赫数和攻角，并只读核对 cases.csv 里有没有该行。"""

import csv
import json
import re
from pathlib import Path

from a2a.helpers import (
    get_message_text,
    new_task_from_user_message,
    new_text_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState


# 脚本根路径先定义。本文件在 multiagents_workbench/condition_agent/ 下，
# 仓库根是再向上两级。
_REPO_ROOT = Path(__file__).resolve().parents[2]

# 输入 / 输出 / 参数配置
# 相对路径基准是仓库根。cases.csv 在 invoke 时打开，导入本模块时不读文件。
# 路径 = {仓库根}/{workspace.json 的 inputRoot}/{CASES_CSV_NAME}，只读。
# 无命令行、无环境变量覆盖；不在这里写 ANSYS 安装路径。
# 输出：无磁盘文件。结果是纯文本任务产物。
# MACH_TEXT 是外层轴，ALPHA_TEXT 是内层轴，按仓库根 README 的来流表编号 C01–C30。
# 这是工况约定，不是可改的本机参数。C07 对应 Ma=2.04、alpha=4.5。
WORKSPACE_JSON_NAME = 'workspace.json'
CASES_CSV_NAME = 'cases.csv'
MACH_TEXT = ('2.00', '2.04', '2.08', '2.12', '2.16', '2.20')
ALPHA_TEXT = ('4.0', '4.5', '5.0', '5.5', '6.0')
_GEOMETRY_ID_RE = re.compile(r'geometry_id\s*[=:：]?\s*(\d+)', re.IGNORECASE)
_CASE_ID_RE = re.compile(r'(?<![A-Za-z0-9])C(\d{2})(?!\d)', re.IGNORECASE)


def cases_csv_path() -> Path:
    """按 workspace.json 的 inputRoot 解析 cases.csv，且必须落在仓库根内。"""
    workspace_path = _REPO_ROOT / WORKSPACE_JSON_NAME
    with workspace_path.open(encoding='utf-8') as handle:
        payload = json.load(handle)
    input_root = payload['inputRoot']
    path = (_REPO_ROOT / input_root / CASES_CSV_NAME).resolve()
    if not path.is_relative_to(_REPO_ROOT.resolve()):
        raise ValueError('cases.csv 解析到了仓库根之外')
    return path


def read_cases() -> list[dict[str, str]]:
    """读取总表全部行。调用方不得写回该文件。"""
    with cases_csv_path().open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def flow_for_case(case_id: str) -> tuple[str, str] | None:
    """返回 (马赫数字符串, 攻角字符串)。编号超出 C01–C30 时返回 None。"""
    number = int(case_id[1:])
    count = len(MACH_TEXT) * len(ALPHA_TEXT)
    if not 1 <= number <= count:
        return None
    index = number - 1
    return (
        MACH_TEXT[index // len(ALPHA_TEXT)],
        ALPHA_TEXT[index % len(ALPHA_TEXT)],
    )


class ConditionAgent:
    """解释工况编号，并说明总表里是否有对应行。"""

    async def invoke(self, user_request: str) -> str:
        """马赫数和攻角来自网格；总表只用来判断行在不在。"""
        tokens = _CASE_ID_RE.findall(user_request)
        if not tokens:
            return '错误：没有解析到工况编号。请写成例如 C07。'
        geometry_match = _GEOMETRY_ID_RE.search(user_request)
        geometry_id = geometry_match.group(1) if geometry_match else None
        try:
            rows = read_cases()
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return '错误：读取 cases.csv 失败。请确认 workspace.json 的 inputRoot 下存在 cases.csv。'

        lines: list[str] = []
        for digits in tokens:
            case_id = f'C{digits}'
            flow = flow_for_case(case_id)
            if flow is None:
                lines.append(f'错误：{case_id} 不在 C01–C30 工况网格内。')
                continue
            mach, alpha = flow
            matched = [
                row
                for row in rows
                if row.get('condition_id', '').strip().upper() == case_id
            ]
            if geometry_id is not None:
                matched = [
                    row
                    for row in matched
                    if row.get('geometry_id', '').strip() == geometry_id
                ]
                scope = f'geometry_id={geometry_id}'
            else:
                scope = '未按 geometry_id 过滤'
            if matched:
                ids = ', '.join(
                    dict.fromkeys(row.get('geometry_id', '').strip() for row in matched)
                )
                if geometry_id is not None:
                    existence = f'cases.csv 中存在该行（geometry_id={geometry_id}）'
                else:
                    existence = (
                        f'cases.csv 中存在该行（未按 geometry_id 过滤，匹配 geometry_id: {ids}）'
                    )
            else:
                existence = f'cases.csv 中不存在该行（{scope}）'
            lines.append(f'{case_id}: Ma={mach}, alpha={alpha}，{existence}')
        return '\n'.join(lines)


class ConditionAgentExecutor(AgentExecutor):
    """把 ConditionAgent 的文本结果写成已完成任务的产物。"""

    def __init__(self) -> None:
        self.agent = ConditionAgent()

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """处理一次用户消息。编号无效时仍把错误文本作为产物并完成任务。"""
        if context.current_task:
            task = context.current_task
        else:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        task_updater = TaskUpdater(
            event_queue=event_queue, task_id=task.id, context_id=task.context_id
        )
        await task_updater.update_status(
            state=TaskState.TASK_STATE_WORKING,
            message=new_text_message('Processing request...'),
        )

        query = get_message_text(context.message)
        if query:
            result = await self.agent.invoke(user_request=query)
        else:
            result = '错误：没有文本输入。'
        await task_updater.add_artifact(
            parts=[new_text_part(text=result, media_type='text/plain')]
        )
        print('Result: ', result)

        await task_updater.update_status(
            state=TaskState.TASK_STATE_COMPLETED,
            message=new_text_message('Request is completed!'),
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """本课不支持取消。"""
        raise NotImplementedError('Cancel is not supported.')
