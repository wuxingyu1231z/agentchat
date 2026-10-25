"""工具执行前校验及保守规范化；不推测缺失参数，不触发工具副作用。"""
import json
from copy import deepcopy

from langchain_core.messages import ToolMessage
from pydantic import ValidationError
from pydantic.v1 import ValidationError as V1ValidationError

from agentchat.utils.logging import redact

MAX_ARGUMENT_FAILURES = 3  # 初始失败后最多允许两次模型纠错。
NULL_WORDS = {"", "null", "none", "undefined"}


def argument_error(tool_call, errors, *, exhausted=False):
    data = {"error": "argument_correction_limit" if exhausted else "invalid_tool_arguments", "tool": tool_call["name"],
            "fields": errors,
            "instruction": "参数纠错次数已用完，请说明限制或向用户澄清，不要继续调用该工具。" if exhausted else
                "工具尚未执行。请根据字段要求修正参数后再次调用，不要原样重试。可选空值用 JSON null，不要写字符串 \"null\"；缺少的必填信息应向用户澄清，不能编造。"}
    return ToolMessage(content=json.dumps(redact(data), ensure_ascii=False), name=tool_call["name"],
                       tool_call_id=tool_call["id"], status="error")


def prepare_tool_request(request):
    """返回新 request、修正字段和错误列表；只使用工具公开 schema。"""
    tool = request.tool
    schema = tool.tool_call_schema if tool else None
    if schema is None or isinstance(schema, dict):
        return request, [], []
    original_schema = tool.get_input_schema()
    public_fields = set(getattr(schema, "model_fields", None) or getattr(schema, "__fields__", {}))
    original_fields = set(getattr(original_schema, "model_fields", None) or getattr(original_schema, "__fields__", {}))
    if original_fields == public_fields:
        schema = original_schema  # 保留工具自身的字段校验器；注入运行时字段时只校验公开子集。
    args = request.tool_call.get("args", {})
    if not isinstance(args, dict):
        return request, [], [{"field": "args", "reason": "参数必须为 JSON 对象"}]
    incoming = deepcopy(args)
    description = schema.model_json_schema() if hasattr(schema, "model_json_schema") else schema.schema()
    fields = description.get("properties", {})
    for name, value in incoming.items():
        choices = fields.get(name, {}).get("anyOf", [])
        nullable = any(choice.get("type") == "null" for choice in choices)
        accepts_arbitrary_string = any(choice.get("type") == "string" and "enum" not in choice and "const" not in choice for choice in choices)
        # Optional[str] 的字面字符串可能有真实含义，不能泛化为 None。
        if nullable and not accepts_arbitrary_string and isinstance(value, str) and value.strip().casefold() in NULL_WORDS:
            incoming[name] = None
    try:
        validated = schema.model_validate(incoming) if hasattr(schema, "model_validate") else schema.parse_obj(incoming)
        output = validated.model_dump(mode="python", exclude_unset=True, by_alias=True) if hasattr(validated, "model_dump") else validated.dict(exclude_unset=True, by_alias=True)
        # 忽略未知字段会隐藏参数拼写问题，要求模型明确修正。
        unknown = set(args) - set(fields)
        if unknown:
            return request, [], [{"field": name, "reason": "未知字段，请按工具 schema 修正参数名称"} for name in sorted(unknown)]
    except (ValidationError, V1ValidationError) as error:
        return request, [], [{"field": ".".join(map(str, row["loc"])), "reason": row["msg"]} for row in error.errors()]
    corrected = [name for name in args if output.get(name) != args[name] or type(output.get(name)) is not type(args[name])]
    return request.override(tool_call={**request.tool_call, "args": output}), corrected, []
