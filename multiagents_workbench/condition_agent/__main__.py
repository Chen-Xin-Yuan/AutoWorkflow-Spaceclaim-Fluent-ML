"""Condition Agent 进程入口。

按 A2A SDK 1.1 发布 Agent Card 和 JSON-RPC。工况换算在同目录 agent_executor.py。
"""

import sys

import uvicorn

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)
from agent_executor import ConditionAgentExecutor
from starlette.applications import Starlette


# 输入 / 输出 / 参数配置
# 本文件无文件输入、无文件输出。cases.csv 由 agent_executor.py 在调用时读取：
# 仓库根是 multiagents_workbench 的上一级，路径来自该根目录 workspace.json 的 inputRoot。
# 下面四项是本课协议固定值，不是可改的本机安装路径；没有命令行或环境变量覆盖。
# AgentInterface.url 必须与 uvicorn 的 host、port 一致。不发布 extended agent card。
BIND_HOST = '127.0.0.1'
BIND_PORT = 10012
AGENT_NAME = 'Condition Agent'
INTERFACE_URL = f'http://{BIND_HOST}:{BIND_PORT}'
SKILL_ID = 'lookup_condition'


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            reconfigure(encoding='utf-8')


def main() -> None:
    """绑定本机端口并启动 Condition Agent。"""
    _configure_stdio()
    skill = AgentSkill(
        id=SKILL_ID,
        name='Lookup Condition',
        description='把 C01–C30 换成马赫数和攻角，并说明 cases.csv 里有没有该行。',
        input_modes=['text/plain'],
        output_modes=['text/plain'],
        tags=['condition', 'cases'],
        examples=['C07 的马赫数和攻角是多少'],
    )
    public_agent_card = AgentCard(
        name=AGENT_NAME,
        description='按仓库根 README 的来流网格解释 C01–C30，并只读核对 cases.csv。',
        version='0.1.0',
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(
                protocol_binding='JSONRPC',
                url=INTERFACE_URL,
                protocol_version='1.0',
            )
        ],
        skills=[skill],
    )
    request_handler = DefaultRequestHandler(
        agent_executor=ConditionAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=public_agent_card,
    )
    routes = []
    routes.extend(create_agent_card_routes(public_agent_card))
    routes.extend(create_jsonrpc_routes(request_handler, '/'))
    app = Starlette(routes=routes)
    uvicorn.run(app, host=BIND_HOST, port=BIND_PORT)


if __name__ == '__main__':
    main()
