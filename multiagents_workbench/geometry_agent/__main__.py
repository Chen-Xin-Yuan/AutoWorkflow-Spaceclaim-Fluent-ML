"""Geometry Agent 进程入口。

按 A2A SDK 1.1 发布 Agent Card 和 JSON-RPC。查询逻辑在同目录 agent_executor.py。
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
from agent_executor import GeometryAgentExecutor
from starlette.applications import Starlette


# 输入 / 输出 / 参数配置
# 本文件无文件输入、无文件输出。cases.csv 由 agent_executor.py 在调用时读取：
# 仓库根是 multiagents_workbench 的上一级，路径来自该根目录 workspace.json 的 inputRoot。
# 下面四项是本课协议固定值，不是可改的本机安装路径；没有命令行或环境变量覆盖。
# AgentInterface.url 必须与 uvicorn 的 host、port 一致。不发布 extended agent card。
BIND_HOST = '127.0.0.1'
BIND_PORT = 10011
AGENT_NAME = 'Geometry Agent'
INTERFACE_URL = f'http://{BIND_HOST}:{BIND_PORT}'
SKILL_ID = 'lookup_geometry'


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            reconfigure(encoding='utf-8')


def main() -> None:
    """绑定本机端口并启动 Geometry Agent。"""
    _configure_stdio()
    skill = AgentSkill(
        id=SKILL_ID,
        name='Lookup Geometry',
        description='按 geometry_id 从 cases.csv 读取 11 个主尺寸，单位是米。',
        input_modes=['text/plain'],
        output_modes=['text/plain'],
        tags=['geometry', 'cases'],
        examples=['geometry_id 1 的机身直径是多少'],
    )
    public_agent_card = AgentCard(
        name=AGENT_NAME,
        description='只读 cases.csv，返回指定 geometry_id 的 11 个主尺寸（米）。',
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
        agent_executor=GeometryAgentExecutor(),
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
