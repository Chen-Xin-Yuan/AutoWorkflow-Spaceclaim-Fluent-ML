"""按 geometry_id 从 cases.csv 读取 11 个主尺寸（米）。只读，不写总表。"""

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


# 脚本根路径先定义。本文件在 multiagents_workbench/geometry_agent/ 下，
# 仓库根是再向上两级。
_REPO_ROOT = Path(__file__).resolve().parents[2]

# 输入 / 输出 / 参数配置
# 相对路径基准是仓库根。输入文件在运行 invoke 时解析，导入本模块时不打开文件。
# 路径 = {仓库根}/{workspace.json 的 inputRoot}/{CASES_CSV_NAME}，只读。
# 无命令行、无环境变量覆盖；不在这里写 ANSYS 安装路径。
# 输出：无磁盘文件。结果是纯文本任务产物。
# LENGTH_FIELDS 是总表里的 11 个主尺寸列名，单位米，不是可随意改名的用户参数。
WORKSPACE_JSON_NAME = 'workspace.json'
CASES_CSV_NAME = 'cases.csv'
LENGTH_FIELDS = (
    'L_N',
    'L',
    'D',
    'L_W',
    'C_RW',
    'C_TW',
    'B_W',
    'L_F',
    'C_RF',
    'C_TF',
    'B_F',
)
_GEOMETRY_ID_RE = re.compile(r'geometry_id\s*[=:：]?\s*(\d+)', re.IGNORECASE)


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


class GeometryAgent:
    """从用户文本里的 geometry_id 查 11 个主尺寸。"""

    async def invoke(self, user_request: str) -> str:
        """返回纯文本。找不到编号时也返回说明，不抛给传输层。"""
        match = _GEOMETRY_ID_RE.search(user_request)
        if not match:
            return '错误：没有解析到 geometry_id。请写成例如 geometry_id 1。'
        geometry_id = match.group(1)
        try:
            rows = read_cases()
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return '错误：读取 cases.csv 失败。请确认 workspace.json 的 inputRoot 下存在 cases.csv。'
        matched = [row for row in rows if row.get('geometry_id', '').strip() == geometry_id]
        if not matched:
            return f'错误：cases.csv 中没有 geometry_id={geometry_id} 的行。'
        chosen = matched[0]
        lines = [f'geometry_id={geometry_id}']
        for field in LENGTH_FIELDS:
            lines.append(f'{field}={chosen.get(field, "")} m')
        differs = any(
            row.get(field, '') != chosen.get(field, '')
            for row in matched[1:]
            for field in LENGTH_FIELDS
        )
        if differs:
            lines.append('注意：同一 geometry_id 的多行主尺寸不完全相同，上面取第一行。')
        return '\n'.join(lines)


class GeometryAgentExecutor(AgentExecutor):
    """把 GeometryAgent 的文本结果写成已完成任务的产物。"""

    def __init__(self) -> None:
        self.agent = GeometryAgent()

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """处理一次用户消息。缺少编号时仍把错误文本作为产物并完成任务。"""
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
