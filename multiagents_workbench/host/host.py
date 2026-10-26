"""命令行客户端：发现两张 Agent Card，按句子分别提问。不是第四个服务。"""

import asyncio
import re
import sys

import httpx

from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_stream_response_text, new_text_message
from a2a.types import Role, SendMessageRequest


# 输入 / 输出 / 参数配置
# 无文件输入输出。不读 cases.csv，也不读 workspace.json；总表只由两个 Agent 读取。
# 问题优先取命令行参数（argv 从第 1 项起拼成一句）。
# 没有参数时，若标准输入不是终端且有一行非空文本，用那一行；否则用 DEMO_QUESTION。
# 两个 URL 和卡片名是本课协议固定值，不是可改的本机安装路径。没有环境变量覆盖。
GEOMETRY_AGENT_URL = 'http://127.0.0.1:10011'
CONDITION_AGENT_URL = 'http://127.0.0.1:10012'
GEOMETRY_AGENT_NAME = 'Geometry Agent'
CONDITION_AGENT_NAME = 'Condition Agent'
DEMO_QUESTION = 'geometry_id 1 的机身直径是多少，C07 的马赭数和攻角是多少？'
_ENDPOINTS = (
    (GEOMETRY_AGENT_URL, GEOMETRY_AGENT_NAME),
    (CONDITION_AGENT_URL, CONDITION_AGENT_NAME),
)
_CASE_ID_RE = re.compile(r'(?<![A-Za-z0-9])C\d{2}(?!\d)', re.IGNORECASE)
_GEOMETRY_HINTS = (
    'geometry_id',
    '机身',
    '直径',
    '鼻锥',
    '弦长',
    '展长',
    '几何',
    'L_N',
    'L_W',
    'C_RW',
    'C_TW',
    'B_W',
    'L_F',
    'C_RF',
    'C_TF',
    'B_F',
)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            reconfigure(encoding='utf-8')


def question_from_cli(argv: list[str]) -> str:
    """argv 优先；否则在非交互标准输入上读一行；再否则用演示问题。"""
    if len(argv) > 1:
        return ' '.join(argv[1:]).strip()
    if not sys.stdin.isatty():
        line = sys.stdin.readline().strip()
        if line:
            return line
    return DEMO_QUESTION


def wants_geometry(text: str) -> bool:
    """句子里有 geometry_id，或在问几何尺寸。"""
    lowered = text.lower()
    if 'geometry_id' in lowered:
        return True
    return any(hint.lower() in lowered for hint in _GEOMETRY_HINTS)


def wants_condition(text: str) -> bool:
    """句子里有 C01–C30 这种两位工况编号。"""
    return _CASE_ID_RE.search(text) is not None


def extract_reply_text(chunk: object) -> list[str]:
    """从 1.1 的 StreamResponse 取出文本。

    非流式响应是带 artifacts 的 task；流式增量是 artifact_update；
    直接回消息时是 message。状态里的进度句不当作答案。
    """
    kind = chunk.WhichOneof('payload')
    if kind == 'status_update':
        return []
    text = get_stream_response_text(chunk)
    return [text] if text else []


async def ask(card, question: str) -> str:
    """用非流式客户端发送一句文本，并拼出产物里的文本。"""
    config = ClientConfig(streaming=False)
    client = await create_client(agent=card, client_config=config)
    try:
        message = new_text_message(question, role=Role.ROLE_USER)
        request = SendMessageRequest(message=message)
        parts: list[str] = []
        async for chunk in client.send_message(request):
            parts.extend(extract_reply_text(chunk))
    finally:
        await client.close()
    if not parts:
        return '（响应里没有文本产物）'
    return '\n'.join(parts)


async def run(question: str) -> None:
    """先取两张卡片，再按句子路由。一个 Agent 连不上时仍询问另一个。"""
    by_name = {}
    async with httpx.AsyncClient() as http:
        for url, expected_name in _ENDPOINTS:
            resolver = A2ACardResolver(httpx_client=http, base_url=url)
            try:
                card = await resolver.get_agent_card()
            except httpx.ConnectError as exc:
                print(f'{expected_name} is down: {exc}', flush=True)
                continue
            by_name[card.name] = card
            print(f'card: {card.name}', flush=True)

    targets: list[str] = []
    if wants_geometry(question):
        targets.append(GEOMETRY_AGENT_NAME)
    if wants_condition(question):
        targets.append(CONDITION_AGENT_NAME)
    print(f'question: {question}', flush=True)
    if not targets:
        print('没有匹配到 Geometry Agent 或 Condition Agent。', flush=True)
        return

    for name in targets:
        card = by_name.get(name)
        if card is None:
            print(f'{name} is down: 启动时没有拿到 Agent Card，跳过发送。', flush=True)
            continue
        try:
            body = await ask(card, question)
        except httpx.ConnectError as exc:
            print(f'{card.name} is down: {exc}', flush=True)
            continue
        print(f'{card.name}:', flush=True)
        print(body, flush=True)


def main() -> None:
    """读取问题并运行一次协作。"""
    _configure_stdio()
    question = question_from_cli(sys.argv)
    asyncio.run(run(question))


if __name__ == '__main__':
    main()
