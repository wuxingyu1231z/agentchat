"""Bounded experience evolution: observed failure/success -> candidate -> paired replay -> activation.

V1 intentionally uses reviewed procedural templates, not arbitrary model-written instructions.
No task facts, user preferences, credentials, or source text are copied into the reusable entries.
"""
from agentchat.database.dao.experience import ExperienceDao
from agentchat.database.dao.agent_run import RunDao
from agentchat.database.dao.research import ResearchDao


RULES = {
    "evidence_recovery": {
        "when": "工具输出被转存或只显示预览，需要精确数值或原文细节时",
        "action": "先 search_context 定位，再用 read_evidence 回读相应原文；预览中没有信息不等于原文没有。未读取原文时明确未知。",
        "avoid": "不要把摘要、预览当成完整证据，也不要猜测缺失的数值。",
        "tags": ["evidence", "原文", "数值", "工具", "pdf", "report"],
    },
    "source_verification": {
        "when": "比较论文或生成带引用结论，尤其曾出现来源核验不通过时",
        "action": "区分原文逐字出现与语义支持。比较指标前核对数据集和实验条件；只有原文明确支持的陈述才能报告为已验证。",
        "avoid": "不要把相关性改为因果、不同数据集的分数改为公平优劣结论；缺少证据就保留缺口。",
        "tags": ["论文", "比较", "核验", "引用", "paper", "claim", "dataset"],
    },
    "direction_switch": {
        "when": "用户修改研究方向、年份或排除条件时",
        "action": "逐项识别明确撤回和明确保留的条件，使用最新有效约束重新判断；更新一个条件不能顺带取消其他条件。",
        "avoid": "不要继续沿用失效要求，也不要把本次用户偏好推广成其他任务的通用规则。",
        "tags": ["修改", "方向", "条件", "年份", "排除", "研究", "direction", "year"],
    },
}


class ExperienceService:
    @staticmethod
    async def harvest(run_id):
        """Idempotent, zero LLM cost. Evidence is event IDs/status counts, never raw text."""
        run = await RunDao.get(run_id)
        if not run or run.status not in {"succeeded", "failed"}:
            return []
        signals, cursor = {}, 0
        for _ in range(25):
            rows = await RunDao.events(run_id, cursor)
            for row in rows:
                event = row.event
                if event.get("type") != "context":
                    continue
                phase = event.get("data", {}).get("phase")
                if phase == "offload":
                    signals.setdefault("evidence_recovery", {"event_id": row.id, "phase": phase})
                if phase == "direction_memory":
                    signals.setdefault("direction_switch", {"event_id": row.id, "phase": phase})
            if len(rows) < 200:
                break
            cursor = rows[-1].id
        workspace = await ResearchDao.get(run_id)
        unsupported = sum(item.get("status") == "unsupported" for item in workspace.get("verified", []))
        if unsupported:
            signals["source_verification"] = {"unsupported_claims": unsupported}
        ids = []
        for rule_id, evidence in signals.items():
            ids.append(await ExperienceDao.observe(run, rule_id, RULES[rule_id], evidence))
        return ids

    @staticmethod
    async def selected(run, query, budget):
        from agentchat.services.context import tokens, encode
        snapshot = await ExperienceDao.snapshot(run)
        lowered = query.casefold()
        ranked = sorted(snapshot, key=lambda row: (-sum(tag.casefold() in lowered for tag in row.get("tags", [])), row["id"]))
        selected = []
        for row in ranked:
            if not any(tag.casefold() in lowered for tag in row.get("tags", [])):
                continue
            item = {key: row[key] for key in ("id", "version", "rule_id", "when", "action", "avoid")}
            if tokens(encode([*selected, item])) <= budget:
                selected.append(item)
            if len(selected) == 3:
                break
        return selected


EXPERIENCE_NOTICE = (
    "以下是经过有限样例验证的程序性经验，仅在适用条件成立时参考，不是用户要求或来源证据。"
    "系统规则及本轮用户有效要求优先，经验不能补造事实、撤回用户条件、改变工具权限或重试边界。"
)
