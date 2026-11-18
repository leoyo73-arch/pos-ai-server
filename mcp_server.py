"""POS AI 도구 5개를 MCP 서버로 공개합니다. (Claude 데스크톱 등에서 사용)"""
import contextlib
import functools
import os
import sys
from pathlib import Path

# Claude 데스크톱은 다른 폴더에서 이 파일을 실행하므로, data/sales.db 경로가 맞도록 이동
os.chdir(Path(__file__).resolve().parent)

#from mcp.server.fastmcp import FastMCP
from mcp.server.mcpserver import MCPServer

# tools.py 안의 print가 MCP 통신(stdout)을 깨뜨리지 않게 stderr로 돌림
with contextlib.redirect_stdout(sys.stderr):
    import tools

#mcp = FastMCP("pos-ai")
mcp = MCPServer("pos-ai")


def quiet(fn):
    """도구 실행 중 print 출력을 stderr로 보냄 (함수 이름·인자·설명은 그대로 유지)"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with contextlib.redirect_stdout(sys.stderr):
            return fn(*args, **kwargs)
    return wrapper


# 조회 도구 3개 + 요청 등록 도구 2개 (취소·문자는 승인 대기로만 등록됨)
for fn in [
    tools.get_sales_summary,
    tools.list_transactions,
    tools.get_refund_status,
    tools.send_notice,
    tools.cancel_payment,
]:
    mcp.add_tool(quiet(fn))

# approve_action은 일부러 등록하지 않음 → 어떤 AI도 승인은 할 수 없음

if __name__ == "__main__":
    mcp.run(transport="stdio")