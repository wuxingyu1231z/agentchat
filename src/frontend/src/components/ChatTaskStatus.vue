<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { AgentRun, ToolDecisions } from '../apis/runs';
import { downloadSandboxArtifact } from '../apis/runs';
const props = defineProps<{ task: AgentRun; disconnected: boolean; working: boolean }>();
const emit = defineEmits<{ resume: [decisions: ToolDecisions, direction?: string]; cancel: []; reconnect: []; export: []; researchExport: [format: 'markdown' | 'json'] }>();
const decisions = ref<ToolDecisions>({});
const direction = ref('');
const downloading = ref('');
const downloadError = ref('');
async function downloadFile(jobId: string, name: string) {
  downloading.value = jobId;
  downloadError.value = '';
  try { await downloadSandboxArtifact(props.task.id, jobId, name); }
  catch { downloadError.value = '文件下载失败，请检查登录状态后重试。'; }
  finally { downloading.value = ''; }
}
const isResearch = computed(() => props.task.agent.id === 'paper-research');
const phases: Record<string,string> = { plan: '规划问题', search: '检索来源', read: '阅读材料', compare: '比较研究', verify: '核对证据', completed: '报告已生成' };
const agentStates: Record<string,string> = {running:'执行中',paused:'已暂停',completed:'已完成',failed:'已由主助手接管',interrupted:'可恢复',cancelled:'已结束'};
watch(() => props.task.id, () => direction.value = '');
watch(() => props.task.status, status => { if (['queued','running'].includes(status)) direction.value = ''; });
watch(() => `${props.task.id}:${props.task.status}`, () => decisions.value = {});
const labels = { queued: '任务准备中', running: '正在执行任务', paused: '任务已暂停', interrupted: '任务已中断，可继续', failed: '任务执行遇到问题', awaiting_input: '需要你确认工具执行结果', succeeded: '任务已完成', cancelled: '任务已结束' };
const resumable = computed(() => ['paused', 'interrupted', 'failed', 'awaiting_input'].includes(props.task.status));
const canResume = computed(() => props.task.unknown_tools.every(tool => !!decisions.value[tool.id]));
</script>
<template>
  <section class="task-status" :class="task.status" aria-label="任务状态" role="status">
    <div class="task-heading"><strong>{{ task.cancel_requested && !['cancelled','succeeded'].includes(task.status) ? '正在结束任务，等待当前操作返回…' : task.pause_requested && task.status==='running' ? '正在暂停，等待当前步骤完成…' : labels[task.status] }}</strong><span v-if="disconnected">进度连接已断开，任务可能仍在后台执行</span><span v-else-if="task.error">{{ task.error }}</span></div>
    <details v-if="isResearch && task.research" class="research-details">
      <summary>{{ phases[task.research.phase] || '论文调研' }} · {{ task.research.source_count || 0 }} 个来源 · {{ task.research.verified_count || 0 }} 条核实结论</summary>
      <p>{{ task.research.plan?.scope || task.research.direction }}</p>
      <ol><li v-for="question in task.research.plan?.questions || []" :key="question.query">{{ question.question }} <small>{{ question.query }}</small></li></ol>
      <p v-if="task.research.delegation?.reason">{{ task.research.delegation.reason }}</p>
      <ul v-if="task.research.agents?.length" class="research-agents"><li v-for="agent in task.research.agents" :key="agent.id"><strong>检索子 Agent {{ agent.question_index + 1 }}</strong> · {{ agentStates[agent.status] || agent.status }}<small>{{ agent.question }}<template v-if="agent.source_count !== undefined"> · {{ agent.source_count }} 个候选来源</template></small></li></ul>
    </details>
    <label v-if="isResearch && resumable" class="research-direction">调整研究方向（可选）<textarea v-model="direction" rows="2" maxlength="4000" :disabled="working" aria-label="调整研究方向" placeholder="例如：重点比较多智能体强化学习，排除纯提示词方案。留空则沿原方向继续。"/><small>继续后重新规划检索，已取得的来源和旧执行记录会保留。</small></label>
    <div v-for="tool in resumable ? task.unknown_tools : []" :key="tool.id" class="task-unknown"><span>{{ tool.name }} 的结果尚未确认，请先核实是否已生效。</span><button type="button" :class="{chosen:decisions[tool.id]==='skip'}" :disabled="working" @click="decisions[tool.id]='skip'">跳过此步骤</button><button type="button" :class="{chosen:decisions[tool.id]==='retry'}" :disabled="working" @click="decisions[tool.id]='retry'">核实后重试</button></div>
    <div class="task-controls"><button v-if="disconnected" type="button" :disabled="working" @click="emit('reconnect')">重新连接</button><button v-if="resumable" type="button" :disabled="working||!canResume" @click="emit('resume',decisions,direction)">继续任务</button><button v-if="!['succeeded','cancelled'].includes(task.status)" type="button" :disabled="working||task.cancel_requested" @click="emit('cancel')">结束任务</button><button type="button" @click="emit('export')">导出执行记录</button><button v-if="isResearch" type="button" @click="emit('researchExport','json')">导出研究资料</button><button v-if="isResearch && task.research?.phase === 'completed'" type="button" @click="emit('researchExport','markdown')">下载调研报告</button></div>
    <div v-if="task.artifacts?.length" class="task-controls"><button v-for="file in task.artifacts" :key="file.job_id" type="button" :disabled="!!downloading" @click="downloadFile(file.job_id,file.name)">{{downloading===file.job_id?'正在下载…':`下载 ${file.name}`}}</button></div>
    <p v-if="downloadError" role="alert">{{downloadError}}</p>
  </section>
</template>
<style scoped>
.task-status{margin:8px 18px 0;padding:12px 15px;border:1px solid #dfe9f8;border-radius:12px;background:#f6f9fe;color:#3a5278;font-size:13px;flex-shrink:0}.task-heading{display:flex;gap:10px;flex-wrap:wrap}.task-heading span{overflow-wrap:anywhere;color:#7484a0}.task-controls,.task-unknown{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px}.task-status button{padding:5px 10px;border:1px solid #cbdcf6;border-radius:7px;background:white;color:#2468cd;cursor:pointer;font:inherit}.task-status button:disabled{opacity:.5;cursor:default}.task-status .chosen{background:#e3efff;border-color:#699ff1}.awaiting_input,.failed{background:#fffaf0;border-color:#efdfbd}.succeeded{background:#f2faf6}
</style>
<style scoped>
.research-details{margin-top:8px}.research-details summary{cursor:pointer}.research-details p{margin:8px 0}.research-details ol{margin:8px 0;padding-left:20px;max-height:130px;overflow:auto}.research-details small{display:block;color:#7484a0}.research-direction{display:block;margin-top:10px}.research-direction textarea{display:block;box-sizing:border-box;width:100%;margin:6px 0;padding:8px 10px;border:1px solid #cbdcf6;border-radius:8px;font:inherit;resize:vertical}.research-direction small{color:#7484a0}
.research-agents{padding-left:20px;max-height:150px;overflow:auto}.research-agents li{margin:6px 0}.research-agents strong{font-weight:550}
</style>
