<script setup lang="ts">
import AgentIdentity from "../../../components/AgentIdentity.vue";
import ChatAttachmentCard from "../../../components/ChatAttachmentCard.vue";
import type { ChatAttachment } from "../../../type";
import UserAvatar from "../../../components/UserAvatar.vue";
import { computed, inject, nextTick, onBeforeUnmount, ref, watch, type Ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage, type ElScrollbar } from 'element-plus';
import { MdPreview } from 'md-editor-v3';
import 'md-editor-v3/lib/style.css';
import UiIcon from '../../../components/UiIcon.vue';
import ChatToolProcess from '../../../components/ChatToolProcess.vue';
import ChatActionIcon from '../../../components/ChatActionIcon.vue';
import { visibleChatEvents, updateChatEvent } from '../../../utils/chat-tool-events';
import { useHistoryChatStore } from '../../../store/history_chat_msg';
import { useUserStore } from '../../../store/user';
import { uploadFileAPI } from '../../../apis/tool';
import { MsgLikeCreateAPI, MsgDisLikeAPI } from '../../../apis/history';
import type { AgentResponse } from '../../../apis/agent';
import CapabilityIcon from '../../../components/CapabilityIcon.vue';
import { copyChatText, isSendKey } from '../../../utils/chat-ui';
import { fitComposer } from '../../../utils/chat-ui';
import { safeResourceUrl } from '../../../utils/data-pages';
import { responseError } from '../../../utils/resource-visuals';
import type { ChatMessage } from '../../../type';
import ChatTaskStatus from '../../../components/ChatTaskStatus.vue';
import { activeRunAPI, latestRunAPI, createRunAPI, getRunAPI, resumeRunAPI, controlRunAPI, watchRun, exportRun, exportResearch, type AgentRun, type ToolDecisions } from '../../../apis/runs';
const route = useRoute(), router = useRouter(), store = useHistoryChatStore(), user = useUserStore();
const agent = inject<Ref<AgentResponse | undefined>>('activeConversationAgent', ref());
const attachment = inject<Ref<{
    url: string;
    name: string;
    size?: number;
    mime?: string;
} | undefined>>('conversationAttachment', ref()), refreshConversations = inject<() => Promise<void>>('refreshConversations', async () => { });
const agents = inject<Ref<AgentResponse[]>>('conversationAgents', ref([]));
const agentsLoading = inject<Ref<boolean>>('conversationAgentsLoading', ref(false));
const refreshAgents = inject<() => Promise<void>>('refreshConversationAgents', async () => {});
const selectedAgent = ref('');
const creating = inject<Ref<boolean>>('conversationCreating', ref(false));
const switchingAgent = ref(false);
const switchAgent = inject<(id:string)=>Promise<boolean>>('switchConversationAgent', async()=>false);
watch(agent, value=>selectedAgent.value=String((value as any)?.id||value?.agent_id||''),{immediate:true});
async function changeAgent(id: string) {
    if (switchingAgent.value || id === String((agent.value as any)?.id || agent.value?.agent_id || '')) return;
    switchingAgent.value = true;
    try { await switchAgent(id); }
    finally { switchingAgent.value = false; selectedAgent.value = String((agent.value as any)?.id || agent.value?.agent_id || ''); }
}

const displayAnswer = (content: string) => content.replace(/^##[ \t]+(?:\d+[.、．][ \t]*)/gm, '## ');
const pendingAttachment = ref<ChatAttachment>();
const draft = ref(''), busy = ref(false), uploading = ref(false), uploadError = ref(''), streamError = ref(''), scrollbar = ref<InstanceType<typeof ElScrollbar>>(), follow = ref(true), activeRow = ref(-1), rowState = ref<Record<number, string>>({}), votes = ref<Record<number, string>>({}), feedbackBusy = ref(-1), brokenAvatars = ref(new Set<string>());
let controller: AbortController | undefined, disposed = false, operation = 0, scrollFrame = 0;
const task = ref<AgentRun>(), taskWorking = ref(false), disconnected = ref(false);
const runBlocked = computed(() => !!task.value && !['succeeded','cancelled'].includes(task.value.status));
let pendingRequest: { key: string; id: string } | undefined;
function messageAvatar(item: ChatMessage) {
    const identity = item.aiMessage.agent;
    if (!identity) return '';
    return agents.value.find(candidate => String((candidate as any).id || candidate.agent_id) === identity.id)?.logo_url || identity.logo_url || '';
}
const userAvatar = computed(() => user.userInfo?.avatar || '');
const validAvatar = (url: string) => !!safeResourceUrl(url) && !brokenAvatars.value.has(url);
function failAvatar(url: string) { brokenAvatars.value = new Set([...brokenAvatars.value, url]); }
function scrollBottom(force = false) { if (force)
    follow.value = true; if (!follow.value)
    return; cancelAnimationFrame(scrollFrame); nextTick(() => { if (disposed)
    return; scrollFrame = requestAnimationFrame(() => { const element = scrollbar.value?.wrapRef; if (element)
    element.scrollTo({ top: element.scrollHeight, behavior: 'auto' }); }); }); }
function onScroll() { const element = scrollbar.value?.wrapRef; if (element)
    follow.value = element.scrollHeight - element.scrollTop - element.clientHeight < 100; }
function stop() { operation++; controller?.abort(); controller = undefined; if (busy.value && activeRow.value >= 0)
    rowState.value[activeRow.value] = 'stopped'; busy.value = false; for (const event of store.chatArr[activeRow.value]?.eventInfo || [])
    if (event.status === 'START')
        event.status = 'STOPPED'; activeRow.value = -1; }
async function loadHistory() { stop(); task.value=undefined; disconnected.value=false; pendingAttachment.value=undefined; streamError.value = ''; uploadError.value = ''; rowState.value = {}; votes.value = {}; uploading.value = false; feedbackBusy.value = -1; draft.value = typeof route.query.initial_message === 'string' ? route.query.initial_message : ''; const id = typeof route.query.conversation_id === 'string' ? route.query.conversation_id : ''; if (!id) {
    store.clear();
    return;
} const current = ++operation; const loaded = await store.HistoryChat(id);
if (!selectedAgent.value) { await Promise.all([refreshConversations(), refreshAgents()]); await nextTick(); }
if (disposed || current !== operation)
    return;
try {
    const response = await activeRunAPI(id);
    if (disposed || current !== operation) return;
    if (response.data.data) {
        await router.replace({ path: route.path, query: { ...route.query, initial_message: undefined, message: undefined } });
        await attachTask(response.data.data);
        return;
    }
    if (selectedAgent.value === 'paper-research' && !route.query.initial_message && !route.query.message) {
        const latest = await latestRunAPI(id);
        if (disposed || current !== operation) return;
        if (latest.data.data?.id && latest.data.data.agent?.id === 'paper-research') {
            await attachTask(latest.data.data);
            return;
        }
    }
} catch (error) { streamError.value = responseError(error, '任务状态加载失败，请刷新重试'); return; }
scrollBottom(true); const initial = typeof route.query.initial_message === 'string' ? route.query.initial_message : typeof route.query.message === 'string' ? route.query.message : ''; if (loaded && initial.trim()) {
    draft.value = initial;
    await router.replace({ path: route.path, query: { ...route.query, initial_message: undefined, message: undefined } });
    if (!disposed && current === operation)
        await send();
} }
async function attachTask(value: AgentRun, file?: ChatAttachment) {
    controller?.abort();
    const current = ++operation;
    task.value = value; disconnected.value = false; streamError.value = '';
    let index = store.chatArr.findIndex(row => row.aiMessage.historyId === value.assistant_history_id);
    if (index < 0) {
        store.chatArr.push({ personMessage: { content: value.user_input, attachments: file ? [{...file}] : [] }, aiMessage: { content: '', historyId: value.assistant_history_id, agent: value.agent }, eventInfo: [] });
        index = store.chatArr.length - 1;
    }
    const row = store.chatArr[index];
    row.aiMessage.content = ''; row.aiMessage.agent = value.agent; row.eventInfo = [];
    activeRow.value = index; busy.value = ['queued','running'].includes(value.status);
    controller = new AbortController();
    try {
        await watchRun(value.id, event => {
            if (disposed || current !== operation) return;
            const data = event.data || {};
            if (event.type === 'run_reset') row.aiMessage.content = String(data.content || '');
            else if (event.type === 'response_chunk') row.aiMessage.content += String(data.chunk || '');
            else if (event.type === 'agent') row.aiMessage.agent = { id: String(data.agent_id), name: String(data.name), logo_url: String(data.logo_url || '') };
            else if (event.type === 'event') updateChatEvent(row.eventInfo ||= [], data.title || '工具执行', data.status || 'UNKNOWN', data.message || '', data.tool_call_id);
            else if (event.type === 'research_progress') task.value = { ...task.value!, research: data };
            else if (event.type === 'research_agent' && task.value?.research && data.revision === (task.value.research.revision || 0)) {
                const agents = [...(task.value.research.agents || [])].filter(agent => agent.id !== data.id);
                task.value = { ...task.value, research: { ...task.value.research, agents: [...agents, data].sort((a,b) => a.question_index-b.question_index) } };
            }
            else if (event.type === 'run_status') {
                task.value = { ...task.value!, status: data.status, error: data.message || '' };
                busy.value = ['queued','running'].includes(data.status);
            }
            scrollBottom();
        }, controller.signal);
        if (disposed || current !== operation) return;
        const response = await getRunAPI(value.id);
        if (disposed || current !== operation) return;
        task.value = response.data.data;
        busy.value = ['queued','running'].includes(task.value!.status);
        disconnected.value = busy.value;
        row.aiMessage.content = task.value!.answer;
        rowState.value[index] = task.value!.status === 'failed' ? 'error' : task.value!.status === 'succeeded' ? 'done' : 'paused';
        streamError.value = task.value!.status === 'failed' ? task.value!.error : '';
        void refreshConversations();
    } catch (error: any) {
        if (!disposed && current === operation && error.name !== 'AbortError') {
            disconnected.value = true;
            streamError.value = responseError(error, '进度连接已断开，可重新连接原任务');
        }
    } finally {
        if (!disposed && current === operation) { activeRow.value = -1; controller = undefined; scrollBottom(); }
    }
}
async function send(text?: string, retryFile?: ChatAttachment) {
    const query = (text ?? draft.value).trim(), conversationId = String(route.query.conversation_id || '');
    if (!query || !conversationId || busy.value || runBlocked.value || creating.value || uploading.value || store.loading || store.error || switchingAgent.value) return;
    const file = text === undefined ? attachment.value : retryFile;
    const key = JSON.stringify([query, file?.url, selectedAgent.value]);
    if (pendingRequest?.key !== key) pendingRequest = { key, id: crypto.randomUUID() };
    busy.value = true; streamError.value = '';
    const current = operation;
    try {
        const response = await createRunAPI({ request_id: pendingRequest.id, conversation_id: conversationId, agent_id: selectedAgent.value || undefined, user_input: query, ...(file ? { file_url: file.url, file_name: file.name, file_size: file.size, file_type: file.mime } : {}) });
        if (disposed || current !== operation) return;
        if (response.data.status_code !== 200) throw new Error(response.data.status_message || '创建任务失败');
        pendingRequest = undefined; draft.value = ''; if (text === undefined) attachment.value = undefined;
        await attachTask(response.data.data, file);
    } catch (error) {
        if (disposed || current !== operation) return;
        busy.value = false;
        streamError.value = responseError(error, '发送失败，请重试');
        // 创建请求可能已落库；先找回任务，避免再次提交相同问题。
        try {
            const active = await activeRunAPI(conversationId);
            if (!disposed && current === operation && active.data.data) await attachTask(active.data.data, file);
        } catch { /* 保留草稿与同一个请求标识，供网络恢复后重试。 */ }
    }
}
async function pauseTask() {
    if (!task.value || taskWorking.value) return;
    const id = task.value.id;
    taskWorking.value = true;
    try { const response = await controlRunAPI(id, 'pause'); if (task.value?.id === id) task.value = response.data.data; }
    catch (error) { ElMessage.error(responseError(error, '暂停请求失败')); }
    finally { taskWorking.value = false; }
}
async function resumeTask(decisions: ToolDecisions, direction?: string) {
    if (!task.value || taskWorking.value) return;
    const id = task.value.id;
    taskWorking.value = true;
    try {
        const response = await resumeRunAPI(id, decisions, direction);
        if (disposed || task.value?.id !== id) return;
        taskWorking.value = false;
        await attachTask(response.data.data);
    } catch (error) { ElMessage.error(responseError(error, '继续任务失败')); }
    finally { taskWorking.value = false; }
}
async function cancelTask() {
    if (!task.value || taskWorking.value) return;
    const id = task.value.id;
    taskWorking.value = true;
    try {
        const response = await controlRunAPI(id, 'cancel');
        if (disposed || task.value?.id !== id) return;
        task.value = response.data.data;
        busy.value = ['queued','running'].includes(task.value!.status);
    } catch (error) { ElMessage.error(responseError(error, '结束任务失败')); }
    finally { taskWorking.value = false; }
}
async function reconnectTask() { if (task.value) await attachTask(task.value); }
async function exportTask() { if (task.value) { try { await exportRun(task.value.id); } catch (error) { ElMessage.error(responseError(error, '导出失败')); } } }
async function exportResearchTask(format: 'markdown' | 'json') { if (task.value) { try { await exportResearch(task.value.id, format); } catch (error) { ElMessage.error(responseError(error, '研究资料导出失败')); } } }
function keydown(event: KeyboardEvent) { if (isSendKey(event)) {
    event.preventDefault();
    send();
} }
async function upload(event: Event) { const input = event.target as HTMLInputElement, file = input.files?.[0]; input.value = ''; if (!file)
    return; if (file.size > 20 * 1024 * 1024) {
    uploadError.value = '附件不能超过 20MB';
    return;
} uploadError.value = ''; uploading.value = true; pendingAttachment.value={url:'',name:file.name,size:file.size,mime:file.type}; const id = operation; try {
    const response = await uploadFileAPI(file);
    if (disposed || id !== operation)
        return;
    if (response.data.status_code !== 200 || typeof response.data.data !== 'string')
        throw new Error(response.data.status_message || '上传失败');
    attachment.value = { url: response.data.data, name: file.name, size: file.size, mime: file.type };
}
catch (error) {
    if (id === operation && !disposed)
        uploadError.value = responseError(error, '附件上传失败');
}
finally {
    if (id === operation && !disposed)
        uploading.value = false; pendingAttachment.value=undefined;
} }
async function copy(value: string) { try {
    await copyChatText(value);
    ElMessage.success('内容已复制');
}
catch {
    ElMessage.error('复制失败，请选中内容复制');
} }
async function feedback(item: ChatMessage, index: number, positive: boolean) { if (feedbackBusy.value >= 0 || votes.value[index])
    return; feedbackBusy.value = index; const conversationId = route.query.conversation_id; try {
    const response = await (positive ? MsgLikeCreateAPI : MsgDisLikeAPI)({ userInput: item.personMessage.content, agentOutput: item.aiMessage.content });
    if (response.data.status_code !== 200)
        throw new Error(response.data.status_message || '反馈失败');
    if (conversationId !== route.query.conversation_id || disposed)
        return;
    votes.value[index] = positive ? 'like' : 'dislike';
    ElMessage.success('感谢你的反馈');
}
catch (error) {
    ElMessage.error(responseError(error, '反馈提交失败'));
}
finally {
    if (conversationId === route.query.conversation_id && !disposed)
        feedbackBusy.value = -1;
} }
watch(() => route.query.conversation_id, (id, previous) => { if (previous && id !== previous)
    attachment.value = undefined; loadHistory(); }, { immediate: true });
watch(() => store.chatArr, () => scrollBottom(), { deep: true });
onBeforeUnmount(() => { disposed = true; stop(); store.clear(); cancelAnimationFrame(scrollFrame); });
const composerInput=ref<HTMLTextAreaElement>();
watch(draft,()=>nextTick(()=>fitComposer(composerInput.value)));
</script>
<template><div class="b5-page b5-chat"><ChatTaskStatus v-if="task" :task="task" :disconnected="disconnected" :working="taskWorking" @resume="resumeTask" @cancel="cancelTask" @reconnect="reconnectTask" @export="exportTask" @research-export="exportResearchTask"/><div class="chat-scroll-area"><el-scrollbar ref="scrollbar" @scroll="onScroll"><div class="chat-messages" v-loading="store.loading"><div v-if="store.error" class="chat-error" role="alert"><span>{{store.error}}</span><button @click="loadHistory">重新加载</button></div><div v-if="!store.loading&&!store.error&&!store.chatArr.length" class="empty-chat"><UiIcon name="chat"/><h2>开始与{{agent?.name||'智能体'}}对话</h2><p>输入问题，使用它已配置的模型和能力。</p></div><article v-for="(item,index) in store.chatArr" :key="index" class="chat-turn"><div v-if="item.personMessage.content||item.personMessage.attachments?.length" class="user-message"><div class="user-message-content"><div v-if="item.personMessage.content" class="user-bubble">{{item.personMessage.content}}</div><div v-if="item.personMessage.attachments?.length" class="message-attachments"><ChatAttachmentCard v-for="file in item.personMessage.attachments" :key="file.url" :file="file"/></div></div><span class="message-avatar"><UserAvatar :username="user.userInfo?.username" :src="userAvatar"/></span></div><div v-if="item.aiMessage.content||visibleChatEvents(item.eventInfo).length||activeRow===index||rowState[index]==='error'||rowState[index]==='stopped'" class="assistant-message"><span class="message-avatar" :title="item.aiMessage.agent?.name || '智能体'"><CapabilityIcon class="chat-agent-mark" kind="coding" robot/><img v-if="validAvatar(messageAvatar(item))" :src="safeResourceUrl(messageAvatar(item))" alt="智能体头像" @error="failAvatar(messageAvatar(item))"></span><div class="assistant-bubble" :class="item.aiMessage.type"><ChatToolProcess :events="visibleChatEvents(item.eventInfo)" :active="busy&&activeRow===index"/><div v-if="!item.aiMessage.content&&busy&&activeRow===index" class="chat-working" role="status"><span class="chat-waiting-dots"><i/><i/><i/></span><span>{{visibleChatEvents(item.eventInfo).some(event=>event.status==='START')?'正在获取资料，工具返回后继续生成回答…':'正在生成回答…'}}</span></div><MdPreview v-if="item.aiMessage.content" :editorId="`conversation-answer-${index}`" :modelValue="displayAnswer(item.aiMessage.content)"/><span v-if="busy&&activeRow===index&&item.aiMessage.content" class="chat-stream-caret" aria-label="回答生成中"/><span v-if="rowState[index]==='stopped'" class="response-state">已停止生成，保留已收到的内容</span><span v-if="rowState[index]==='error'" class="response-state error">{{streamError}}</span><button v-if="rowState[index]==='error'&&item.personMessage.content&&!runBlocked" class="chat-outline retry-message" :disabled="busy||store.loading||runBlocked" @click="send(item.personMessage.content,item.personMessage.attachments?.[0])">重试这条消息</button><footer v-if="item.aiMessage.content&&activeRow!==index" class="answer-actions"><button aria-label="复制回答" title="复制回答" @click="copy(item.aiMessage.content)"><ChatActionIcon name="copy"/></button><button v-if="item.personMessage.content" :disabled="busy||store.loading||runBlocked" aria-label="再问一次" title="重新回答这个问题" @click="send(item.personMessage.content,item.personMessage.attachments?.[0])"><ChatActionIcon name="retry"/></button><button v-if="item.personMessage.content" :disabled="feedbackBusy>=0||!!votes[index]" :class="{active:votes[index]==='like'}" aria-label="回答有帮助" title="回答有帮助" @click="feedback(item,index,true)"><ChatActionIcon name="like"/></button><button v-if="item.personMessage.content" :disabled="feedbackBusy>=0||!!votes[index]" :class="{active:votes[index]==='dislike'}" aria-label="回答需要改进" title="回答需要改进" @click="feedback(item,index,false)"><ChatActionIcon name="dislike"/></button></footer></div></div></article></div></el-scrollbar><button v-if="!follow" class="near-bottom" @click="scrollBottom(true)"><UiIcon name="arrow-down"/>回到最新消息</button></div><form class="chat-composer conversation-composer welcome-composer" @submit.prevent="send()"><p v-if="uploadError||streamError" class="chat-error" role="alert">{{uploadError||streamError}}</p><div v-if="pendingAttachment||attachment" class="composer-attachments"><ChatAttachmentCard :file="(pendingAttachment||attachment)!" :uploading="uploading" removable :disabled="creating" @remove="attachment=undefined"/></div><textarea ref="composerInput" v-model="draft" rows="3" :disabled="store.loading||creating" placeholder="输入你的问题，Enter 发送，Shift + Enter 换行…" aria-label="输入问题" @keydown="keydown"/><div class="composer-footer"><div class="composer-tools"><label class="upload-label" :class="{busy:uploading}" :title="uploading?'上传中…':'添加附件'"><UiIcon :name="uploading?'clock':'paperclip'"/><input type="file" :disabled="busy||uploading||store.loading||!!attachment" @change="upload"></label></div><div class="composer-send"><el-select v-model="selectedAgent" filterable class="welcome-agent-select" placeholder="选择智能体" aria-label="选择智能体" :loading="agentsLoading" :disabled="switchingAgent||creating||store.loading||runBlocked" title="在当前会话中切换智能体，保留聊天记录" @change="changeAgent"><template #prefix><AgentIdentity v-if="agent" class="dropdown-option-logo" :agent="agent"/><UiIcon v-else name="bot"/></template><el-option v-for="item in agents" :key="String((item as any).id||item.agent_id)" :value="String((item as any).id||item.agent_id)" :label="item.name"><span class="dropdown-option-row"><AgentIdentity class="dropdown-option-logo" :agent="item"/><span>{{item.name}}</span></span></el-option></el-select><button type="button" class="chat-primary" :aria-label="busy?'暂停任务':'发送消息'" :disabled="!busy&&(!draft.trim()||uploading||store.loading||!!store.error||runBlocked)" @click="busy?pauseTask():send()"><UiIcon :name="busy?'stop':'send'"/>{{busy?'暂停':'发送'}}</button></div></div></form></div></template>
<style scoped src="./chat.css"></style>

<style scoped src="../defaultPage/conversation-welcome.css"></style>
