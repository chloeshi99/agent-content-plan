"""工具契约定义。

每个工具有严格的输入/输出 schema，模型只能通过这些契约与系统交互。
**工具层不做任何决策**，只提供事实与受控写入。
"""

from __future__ import annotations

from typing import Any

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "search_materials",
        "description": "按主题/关键词检索候选素材，返回候选素材及其费用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "活动主题或关键词"},
                "limit": {"type": "integer", "description": "返回候选数量上限"},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "generate_script",
        "description": "基于主题与选定的文案类素材生成完整脚本内容。",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "活动主题"},
                "assets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "本次方案选用的素材 id 列表",
                },
            },
            "required": ["topic", "assets"],
        },
    },
    {
        "name": "estimate_cost",
        "description": "按选定素材计算方案的总费用与总耗时，并判断是否超出预算。",
        "input_schema": {
            "type": "object",
            "properties": {
                "assets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "待估算的素材 id 列表",
                },
                "budget": {"type": "integer", "description": "预算（模拟单位），可为空表示不限制"},
                "deadline": {"type": "string", "description": "截止时间，可为空表示不限制"},
            },
            "required": ["assets"],
        },
    },
    {
        "name": "save_plan",
        "description": "保存内容活动方案草稿（仅保存草稿，不做任何真实提交）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "活动主题"},
                "assets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "方案选用的素材 id 列表",
                },
                "script": {"type": "string", "description": "脚本正文"},
                "total_cost": {"type": "integer", "description": "方案总费用"},
                "total_duration_min": {"type": "integer", "description": "方案总耗时（分钟）"},
            },
            "required": ["topic", "assets", "total_cost"],
        },
    },
]

TOOL_NAMES = [t["name"] for t in TOOL_SPECS]
