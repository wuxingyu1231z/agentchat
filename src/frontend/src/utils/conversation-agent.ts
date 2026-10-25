import type { AgentResponse } from '../apis/agent';

export const DEFAULT_AGENT_ID = 'general';

export const DEFAULT_CONVERSATION_AGENT: AgentResponse = {
  agent_id: DEFAULT_AGENT_ID,
  name: '通用助手',
  description: '直接提问，使用默认模型进行对话。',
  logo_url: '',
  llm_id: '',
  system_prompt: '',
  enable_memory: false,
  tool_ids: [],
  mcp_ids: [],
  knowledge_ids: [],
};
