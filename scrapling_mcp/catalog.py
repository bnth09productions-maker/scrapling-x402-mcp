"""Build the MCP tool list from the captured catalog plus documentation.

Prices, enums, defaults, required flags, and property names stay as captured
from the live server. Only description strings are replaced.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from scrapling_mcp.arg_docs import API_KEY, ARGS, TOOL_RETURNS

_PRICE_PREFIX = {
    "bot_history": "PAID $0.01 USDC. ",
    "odds_history": "PAID $0.01 USDC. ",
}


def baseline_tools() -> list[dict[str, Any]]:
    raw = files("scrapling_mcp").joinpath("baseline_catalog.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, list):
        raise TypeError("baseline catalog must be a list of tools")
    return data


def argument_description(tool: str, name: str, spec: dict[str, Any]) -> str:
    if name == "api_key":
        text = API_KEY
    else:
        try:
            text = ARGS[tool][name]
        except KeyError as exc:
            raise KeyError(f"missing documentation for {tool}.{name}") from exc
    if spec.get("enum"):
        allowed = ", ".join(str(item) for item in spec["enum"])
        if "Allowed values:" not in text:
            text = text.rstrip() + f" Allowed values: {allowed}."
    if "default" in spec and "Default:" not in text:
        text = text.rstrip() + f" Default: {spec['default']}."
    return text


def tool_description(tool: dict[str, Any]) -> str:
    name = tool["name"]
    try:
        returns = TOOL_RETURNS[name]
    except KeyError as exc:
        raise KeyError(f"missing return sentence for {name}") from exc
    if not returns.startswith("Returns "):
        raise ValueError(f"{name} return sentence must start with 'Returns '")
    desc = tool["description"].rstrip()
    prefix = _PRICE_PREFIX.get(name, "")
    if prefix and prefix.strip().rstrip(".") not in desc and "$0.01" not in desc:
        desc = prefix + desc
    if returns not in desc:
        desc = f"{desc} {returns}"
    return desc


def documented_tools() -> list[dict[str, Any]]:
    tools = []
    for tool in baseline_tools():
        properties: dict[str, Any] = {}
        for name, spec in tool["properties"].items():
            prop = {"type": spec["type"]} if "type" in spec else {}
            prop["description"] = argument_description(tool["name"], name, spec)
            for key, value in spec.items():
                if key not in ("type", "description"):
                    prop[key] = value
            properties[name] = prop
        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": tool.get("additionalProperties", False),
        }
        if tool.get("required"):
            schema["required"] = list(tool["required"])
        tools.append(
            {
                "name": tool["name"],
                "title": tool["title"],
                "description": tool_description(tool),
                "inputSchema": schema,
            }
        )
    return tools
