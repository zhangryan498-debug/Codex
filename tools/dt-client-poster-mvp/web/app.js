"use strict";

const el = (id) => document.getElementById(id);
const sites = { CN: "抖音中国", PH: "菲律宾 TikTok Shop" };
const viewLabels = { home: "首页", poster: "商品任务", video: "视频任务", transcription: "视频转写",
  role: "岗位工具", tasks: "任务记录", models: "模型连接", settings: "设置" };
const cloudMode = "openai_compatible";
const mebibyte = 1024 * 1024;
const defaultRoleMediaBytes = 24 * mebibyte;
const roleVideoFilename = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.mp4$/i;
const mainImageTool = {
  id: "main-image-set",
  label: "商品主图套组",
  description: "按五个槽位提交本机图片、来源编号和已有文案，生成主图与分层源档。",
  execution_scope: "仅支持本机离线合成任务。"
};
const longDetailTool = {
  id: "long-product-detail",
  label: "商品长商详",
  description: "按站点和模块提交已有文案、证据编号及配图，生成长页、切片和分层源档。",
  execution_scope: "本机合成测试。菲律宾文案使用英文，价格使用 PHP。"
};
const mainImageSlotLabels = {
  feature: "核心卖点", advantage: "产品优势", benefit: "使用收益", proof: "证据展示", scene: "使用场景"
};
const modeLabels = { demo: "模拟演示", ollama: "Ollama", [cloudMode]: "自带云端模型" };
const states = {
  queued: "排队中", drafting: "生成文案中", rendering: "渲染中", ready: "已完成",
  revising: "生成新版中", failed: "任务失败", interrupted: "任务已中断", processing: "处理中",
  awaiting_selling_points: "待提炼", awaiting_layout_plan: "待排版计划", awaiting_layout_review: "待计划审校"
};
const activeStates = new Set(["queued", "drafting", "rendering", "revising", "processing"]);
const tokenPlanActiveStates = new Set(["starting", "drafting", "waiting_for_review", "receiving"]);
const workflows = {
  transcription: { task: null, pending: null, formRevision: 0, poll: 0, failures: 0, submission: null },
  role: { task: null, pending: null, formRevision: 0, poll: 0, failures: 0, submission: null }
};
const context = {
  session: null, jobs: [], task: null, version: null, followLatest: true, fixture: null,
  page: "home", pollId: 0, pollFailures: 0, settingsDirty: false, isEditing: false,
  submitting: false, editing: false, saving: false, testing: false, tokenPlanStarting: null,
  navigation: 0, taskOpen: null, artworkReturn: null, draftRevision: 0, productSubmission: null
};

class ServiceError extends Error {
  constructor(code, message, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function request(path, data, signal) {
  const options = { cache: "no-store", credentials: "same-origin", signal };
  if (data !== undefined) {
    options.method = "POST";
    options.headers = { "Content-Type": "application/json", "X-DT-Local": "1" };
    options.body = JSON.stringify(data);
  }
  let response;
  try { response = await fetch(path, options); }
  catch { throw new ServiceError("LOCAL_UNREACHABLE", "无法连接本机工作台。请确认启动窗口仍在运行。", 0); }
  let body;
  try { body = await response.json(); }
  catch { throw new ServiceError("INVALID_RESPONSE", "本机工作台返回了无法读取的结果。", response.status); }
  if (!response.ok) {
    const err = body?.error;
    throw new ServiceError(err?.code || "REQUEST_ERROR", err?.message || "本机操作未完成。", response.status);
  }
  return body;
}

function notice(id, text = "", error = false) {
  const box = el(id);
  if (box.textContent !== text) box.textContent = text;
  box.classList.toggle("error", error);
  box.hidden = !text;
}

function usageNotice(mode, runtime) {
  if (mode !== cloudMode) return "";
  if (runtime?.usage_status === "partial") return "服务商返回的用量不完整，费用以服务商账单为准。";
  if (runtime?.usage_status === "unknown") return "服务商未提供可用的用量信息，费用以服务商账单为准。";
  return "";
}

function customerModelWorkspace() {
  return context.session?.model_workspace_customer === true;
}

function v2ModelWorkspace() {
  return context.session?.model_workspace_v2 === true;
}

function redesignedModelWorkspace() {
  return v2ModelWorkspace() || customerModelWorkspace() || context.session?.model_workspace_preview === true;
}

function updateModeBadge() {
  const badge = el("mode-badge");
  const local = { transcription: "本机转写", role: "本机岗位工具" }[context.page];
  const label = v2ModelWorkspace()
    ? (context.session.model_workspace_catalog_fixture ? "本机协议替身" : "本机工作区")
    : customerModelWorkspace()
    ? (context.session.model_workspace_protocol_fixture ? "本机协议替身" : "本人点验")
    : context.session?.model_workspace_preview ? "隔离预览" : local || modeLabels[context.session?.settings?.mode];
  badge.hidden = !label;
  badge.textContent = label || "";
}

function markTaskOpenControl(active, control) {
  active.control = control;
  active.text = control.textContent;
  active.label = control.getAttribute("aria-label");
  control.textContent = "取消打开";
  control.setAttribute("aria-label", "取消打开");
  control.setAttribute("aria-pressed", "true");
}

function replaceTaskOpenRows(list, rows) {
  const focused = list.contains(document.activeElement) ? document.activeElement.dataset.taskId : null;
  const active = context.taskOpen?.list === list ? context.taskOpen : null;
  list.replaceChildren(...rows);
  if (!active && !focused) return;
  for (const button of list.querySelectorAll("[data-task-id]")) {
    if (button.dataset.taskId === active?.id) markTaskOpenControl(active, button);
    if (button.dataset.taskId === focused) button.focus({preventScroll:true});
  }
}

function cancelTaskOpen(announce = false) {
  const active = context.taskOpen;
  if (!active) return;
  context.taskOpen = null;
  active.controller.abort();
  if (active.control?.isConnected) {
    active.control.textContent = active.text;
    if (active.label === null) active.control.removeAttribute("aria-label");
    else active.control.setAttribute("aria-label", active.label);
    active.control.removeAttribute("aria-pressed");
  }
  notice("workspace-open-status", announce ? "已取消打开。可以继续当前工作。" : "");
}

function setPage(view, moveFocus = false) {
  if (!Object.hasOwn(viewLabels, view)) return;
  cancelTaskOpen();
  context.navigation += 1;
  notice("workspace-open-status");
  clearWorkflowProgress(context.page);
  context.page = view;
  if (view === "role" && workflows.role.pending) updateRoleControls();
  for (const name of Object.keys(viewLabels)) el(name + "-view").hidden = name !== view;
  for (const item of document.querySelectorAll("button[data-view]")) {
    if (item.dataset.view === view) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  }
  el("page-title").textContent = viewLabels[view];
  updateModeBadge();
  el("top-settings").hidden = view === "models";
  if (view === "tasks") refreshTasks();
  if (moveFocus) {
    window.scrollTo(0, 0);
    el("workspace").focus({ preventScroll: true });
  }
  document.dispatchEvent(new CustomEvent("dt-view", {detail: view}));
}

function settingsMode() {
  const mode = el("model-mode").value;
  const cloud = mode === cloudMode;
  el("model-fields").hidden = mode === "demo";
  el("model-base").required = mode !== "demo";
  el("model-name").required = mode !== "demo";
  el("model-base").placeholder = cloud ? "https://你的供应商地址/v1" : "http://127.0.0.1:11434";
  el("model-name").placeholder = cloud ? "服务商提供的模型标识" : "输入服务中已安装的模型";
  el("mode-note").textContent = cloud
    ? "要求 HTTPS、Chat Completions 和 JSON 对象响应。各服务商与模型的兼容性不同。"
    : mode === "ollama"
      ? "Ollama 将写稿所需文字发送到所选模型服务，商品图片留在本机。"
      : "模拟演示不发出模型请求。";
  const saved = context.session?.settings;
  const sameTarget = mode === saved?.mode && el("model-base").value.trim() === saved?.base_url;
  el("forget-control").hidden = !sameTarget || !saved?.has_key || mode === "demo";
  el("key-note").textContent = sameTarget && saved?.has_key
    ? "密钥已受保护地保存在本机。留空以保留当前密钥。"
    : "服务无需鉴权时可留空。更换模式或服务地址会清除已输入的密钥。";
}

function settingsTargetChanged() {
  el("model-key").value = "";
  el("forget-key").checked = false;
  settingsMode();
}

function setSettings(settings) {
  context.session.settings = settings;
  context.settingsDirty = false;
  el("model-mode").value = Object.hasOwn(modeLabels, settings.mode) ? settings.mode : "demo";
  el("model-base").value = settings.base_url || "";
  el("model-name").value = settings.model || "";
  el("model-key").value = "";
  el("forget-key").checked = false;
  settingsMode();
  updateModeBadge();
}

function confirmCloudAction(settings, action) {
  if (typeof settings.revision !== "string" || !settings.revision) {
    throw new ServiceError("missing_settings_revision", "无法核对已保存的云端配置，请刷新工作台后再试。", 0);
  }
  const dialog = el("cloud-consent-dialog");
  const testing = action === "test";
  const posterCopy = action === "poster-copy";
  const fixture = posterCopy && settings.posterCopyScope === "customer_cloud_http_mock";
  el("cloud-consent-title").textContent = testing ? "确认测试云端连接" : "确认使用云端模型生成海报";
  el("cloud-consent-cost").textContent = fixture
    ? "此隔离实例只向本机模拟接口发送请求，无实际云端调用或费用。"
    : "本次会发出一次请求，模型服务商可能依账户套餐收费。";
  el("cloud-consent-detail").textContent = testing
    ? "本次仅发送一条固定的简短验证提示。商品信息、图片和文件留在本机。"
    : "本次发送商品名称、至多两条已填商品事实、站点、简短卖点指令及文案 JSON 格式要求。原图、项目文件及海报留在本机。";
  el("cloud-consent-target").textContent = "发送至 " + settings.base_url + "，模型 " + settings.model + "。";
  el("cloud-consent-confirm").textContent = testing ? "确认并测试" : "确认并生成";
  dialog.returnValue = "";
  return new Promise((resolve, reject) => {
    if (dialog.open) {
      reject(new ServiceError("confirmation_in_progress", "请先完成当前确认。", 0));
      return;
    }
    dialog.showModal();
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
  });
}

function updateSettingsControls() {
  const locked = !context.session || context.saving || context.testing;
  el("save-settings").disabled = locked;
  el("test-connection").disabled = locked;
}

function selectedFixture() {
  return context.session?.fixtures?.[context.fixture];
}

function showSource() {
  const file = el("product-file").files[0];
  el("source-note").textContent = selectedFixture() ?
    ("正在使用" + sites[context.fixture] + "授权模拟素材。此任务在本机演示，不发出模型请求。") :
    file ? ("选中的本机文件：" + file.name) : "选择一张本机 PNG 或 JPG，最大 4 MiB。";
}

function loadFixture(site) {
  const data = context.session?.fixtures?.[site];
  if (!data) return;
  context.fixture = site;
  el("site").value = site;
  el("product-title").value = data.title || "";
  const facts = data.facts;
  el("product-facts").value = Array.isArray(facts) ? facts.join("\n") : facts || data.subtitle || "";
  el("product-file").value = "";
  el("poster-combined-detail").checked = false;
  updateCombinedForm();
  showSource();
  notice("poster-status");
}

function fileData(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const encoded = String(reader.result || "");
      const comma = encoded.indexOf(",");
      if (comma < 0) reject(new Error("无法读取选中的图片。"));
      else resolve(encoded.slice(comma + 1));
    };
    reader.onerror = () => reject(new Error("无法读取选中的图片。"));
    reader.readAsDataURL(file);
  });
}

function requestId() {
  return crypto.randomUUID ? crypto.randomUUID() : Array.from(crypto.getRandomValues(new Uint8Array(16)),
    (b) => b.toString(16).padStart(2, "0")).join("");
}

function timestamp(value) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(date);
}

function storeTask(task) {
  if (!task?.id) return;
  const known = context.jobs.findIndex((item) => item.id === task.id);
  if (known < 0) context.jobs.push(task);
  else context.jobs[known] = task;
  context.jobs.sort((a, b) => String(b.updated_at || b.created_at || "").localeCompare(String(a.updated_at || a.created_at || "")));
}

function pageForTask(task) {
  if (task?.kind === "transcription") return "transcription";
  if (task?.kind === "role_tool") return "role";
  return "poster";
}

function taskStatus(task) {
  if (task.source?.tool_id === "competitor-two-shop-compare-readiness" &&
      task.status === "ready" && task.result?.state === "PENDING_COMPARABLE_EVIDENCE") {
    return "待补比较证据";
  }
  if (pageForTask(task) !== "poster" && task.status === "drafting") return states.processing;
  return states[task.status] || "状态未知";
}

let taskListSignature = "";
function renderTaskList() {
  const signature = JSON.stringify(context.jobs.map(t => [t.id, t.title, t.site, pageForTask(t), taskStatus(t), t.updated_at || t.created_at]));
  if (signature === taskListSignature) return;
  taskListSignature = signature;
  const items = [];
  for (const task of context.jobs) {
    const row = document.createElement("tr");
    const fields = [task.title || "未命名任务", viewLabels[pageForTask(task)],
      sites[task.site] || task.site || "—", taskStatus(task), timestamp(task.updated_at || task.created_at)];
    for (const text of fields) {
      const cell = document.createElement("td");
      cell.textContent = text;
      row.append(cell);
    }
    if (task.source?.tool_id === longDetailTool.id) {
      const number = document.createElement("small");
      number.className = "muted";
      number.textContent = task.id;
      number.title = "任务编号";
      row.firstElementChild.append(document.createElement("br"), number);
    }
    const cell = document.createElement("td");
    const open = document.createElement("button");
    open.className = "button neutral";
    open.type = "button";
    open.textContent = "查看";
    open.setAttribute("aria-label", "查看" + fields[0]);
    open.dataset.taskId = task.id;
    open.addEventListener("click", () => openTask(task.id, true, {control: open}));
    cell.append(open);
    row.append(cell);
    items.push(row);
  }
  replaceTaskOpenRows(el("task-rows"), items);
  el("task-list-empty").hidden = items.length > 0;
  el("task-table-wrap").hidden = !items.length;
}

async function refreshTasks() {
  try {
    const body = await request("/api/tasks");
    context.jobs = [];
    if (Array.isArray(body.tasks)) body.tasks.forEach(storeTask);
    renderTaskList();
    notice("task-list-status");
    document.dispatchEvent(new Event("dt-tasks"));
    return true;
  } catch (error) {
    notice("task-list-status", error.message, true);
    return false;
  }
}

function orderedVersions(task) {
  return Array.isArray(task?.versions) ? task.versions.filter((v) => Number.isInteger(v.version) && v.version > 0)
    .sort((a, b) => a.version - b.version) : [];
}

function currentVersion() {
  return orderedVersions(context.task).find((version) => version.version === context.version);
}

function latestVersion() { return orderedVersions(context.task).at(-1)?.version || null; }

function setOptions(select, values, selected) {
  const list = Array.from(select.options, (item) => [item.value, item.textContent]);
  if (JSON.stringify(list) !== JSON.stringify(values)) {
    select.replaceChildren(...values.map(([value, text]) => new Option(text, value)));
  }
  if (selected !== undefined && selected !== null) select.value = String(selected);
}

function renderLiveListening(panel, result) {
  if (!result) return;
  const list = document.createElement("ul");
  list.className = "transcript-preview";
  if (Array.isArray(result.rows)) {
    for (const row of result.rows) {
      const item = document.createElement("li");
      item.textContent = `${row.window} #${row.line} · 原机稿：${row.machine} · 人工更正：${row.human_correction ?? "未标注"}`;
      list.append(item);
    }
  } else if (Array.isArray(result.decisions)) {
    const scope = document.createElement("p");
    scope.textContent = "合成案例。仅检查结构化判断逻辑。";
    panel.append(scope);
    const roles = {host: "主播", viewer: "评论", unknown: "待核说话人"};
    const contexts = {affirmed: "直接陈述", quoted: "引用", negated: "否定", unknown: "待核语境"};
    const prices = {actual_mismatch_in_fixture: "同商品同条件价不一致", matches_fixture: "同商品同条件价一致",
      awaiting_same_product_price_evidence: "待核同商品同条件同时间窗价格",
      not_direct_host_claim: "非主播直接报价格", awaiting_attribution_or_context: "待核发言人或语境"};
    for (const row of result.decisions) {
      const item = document.createElement("li");
      const action = row.action === "silent" ? "静默对应提醒" : "保留提醒";
      const price = prices[row.price_status];
      item.textContent = [row.id, row.utterance, roles[row.speaker], contexts[row.context], action, price]
        .filter(Boolean).join(" · ");
      list.append(item);
    }
  }
  if (list.childElementCount) panel.append(list);
}

function localFile(value, download = false) {
  if (typeof value !== "string" || !value.startsWith("/api/tasks/")) return null;
  try {
    const url = new URL(value, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/tasks/")) return null;
    if (download) url.searchParams.set("download", "1");
    return url.pathname + url.search;
  } catch { return null; }
}

function resultLink(id, path, download) {
  const link = el(id);
  const url = localFile(path, download);
  link.hidden = !url;
  if (url) link.setAttribute("href", url);
  else link.removeAttribute("href");
}

function selectedRoleTool() {
  return availableRoleTools().find((tool) => tool.id === el("role-tool").value);
}

function roleExample(tool) {
  return tool?.input_examples?.[el("role-site").value] ?? tool?.input_example;
}

function availableRoleTools() {
  const tools = context.session?.role_tools;
  if (!Array.isArray(tools)) return [];
  const site = el("role-site").value;
  const available = tools.filter((tool) => typeof tool?.id === "string" && typeof tool.label === "string" &&
    Array.isArray(tool.sites) && tool.sites.includes(site));
  const mainImages = context.session.main_images;
  if (mainImages?.sites?.includes(site) && Array.isArray(mainImages.slots) &&
      mainImages.slots.every((slot) => Object.hasOwn(mainImageSlotLabels, slot))) {
    available.push(mainImageTool);
  }
  if (context.session.long_detail?.sites?.[site]?.length) available.push(longDetailTool);
  return available;
}

function updateMainImageControls(active, busy) {
  const input = el("main-image-input");
  input.hidden = !active;
  input.disabled = !active || busy;
  if (!active) return;

  const site = el("role-site").value;
  const settings = context.session.main_images;
  el("main-image-market").hidden = site !== "PH";
  el("main-image-file-help").textContent =
    "每个槽位需要一张 PNG 或 JPEG，每张最多 " + settings.max_file_bytes / mebibyte + " MiB。文案留空时保存为待提炼。";
  const cards = el("main-image-cards");
  if (cards.dataset.site === site) return;
  el("main-image-product").value = "";
  el("main-image-extra-edge").value = "";
  el("main-image-extra-ref").value = "";
  cards.dataset.site = site;
  cards.replaceChildren(...settings.slots.map((slot) => {
    const card = document.createElement("fieldset");
    card.className = "field";
    const legend = document.createElement("legend");
    legend.textContent = mainImageSlotLabels[slot];
    card.append(legend);
    for (const [key, label] of [["title", "标题"], ["subtitle", "副标题"],
                               ["source_ref", "素材来源编号"], ["image", "本机图片"]]) {
      const field = key === "title" || key === "subtitle" ? document.createElement("textarea")
        : document.createElement("input");
      field.required = key === "source_ref" || key === "image";
      field.id = "main-image-" + slot + "-" + key;
      if (field.tagName === "TEXTAREA") {
        field.rows = 2;
        const limit = context.session.copy_limits?.[key]?.max_characters;
        if (Number.isSafeInteger(limit) && limit > 0) field.maxLength = limit;
      } else if (key === "image") {
        field.type = "file";
        field.accept = "image/png,image/jpeg,.png,.jpg,.jpeg";
        field.setAttribute("aria-describedby", "main-image-file-help");
      } else {
        field.maxLength = 64;
        field.pattern = el("main-image-product").pattern;
        field.setAttribute("aria-describedby", "main-image-id-help");
      }
      const caption = document.createElement("label");
      caption.htmlFor = field.id;
      caption.textContent = label;
      card.append(caption, field);
    }
    return card;
  }));
}

function detailField(parent, id, label, kind = "text", required = true) {
  const caption = document.createElement("label");
  caption.htmlFor = id;
  caption.textContent = label;
  const field = document.createElement(kind === "copy" ? "textarea" : "input");
  field.id = id;
  field.required = required;
  if (kind === "copy") {
    field.rows = 2;
  } else if (kind === "ref") {
    field.maxLength = 64;
    field.pattern = el("main-image-product").pattern;
  } else if (kind === "file") {
    field.type = "file";
    field.accept = "image/png,image/jpeg,.png,.jpg,.jpeg";
  } else if (kind === "decimal") {
    field.inputMode = "decimal";
    field.pattern = "[0-9]{1,7}(\\.[0-9]{1,2})?";
  }
  parent.append(caption, field);
  return field;
}

function detailGroup(label) {
  const fields = document.createElement("fieldset");
  fields.className = "field";
  const legend = document.createElement("legend");
  legend.textContent = label;
  fields.append(legend);
  return fields;
}

function detailStatus(parent, id, options) {
  const label = document.createElement("label");
  label.htmlFor = id;
  label.textContent = "资料状态";
  const control = document.createElement("select");
  control.id = id;
  setOptions(control, options, options[0][0]);
  parent.append(label, control);
  return control;
}

const detailFactLabels = {
  price: [["amount", "合成价格（PHP）"]],
  dimensions: [["width", "宽（cm）"], ["depth", "深（cm）"], ["height", "高（cm）"]]
};

function makeDetailModule(module, index, allowSizeNA) {
  const base = "detail-" + module.id + "-";
  const fields = detailGroup(String(index + 1).padStart(2, "0") + " " + module.label);
  const supported = !module.image_required;
  const statusOptions = supported ? [["unknown", "待核"], ["provided", "有资料"]] : [["provided", "有资料（必填）"]];
  if (allowSizeNA) statusOptions.push(["not_applicable", "不适用（需来源）"]);
  const state = detailStatus(fields, base + "status", statusOptions);
  const provided = document.createElement("fieldset");
  const notApplicable = document.createElement("fieldset");
  if (allowSizeNA) detailField(notApplicable, base + "na_ref", "不适用依据编号", "ref");
  if (module.fact) {
    for (const [key, label] of detailFactLabels[module.fact]) {
      detailField(provided, base + key, label, "decimal");
    }
  } else {
    detailField(provided, base + "title", "标题", "copy", false);
    detailField(provided, base + "body", "正文", "copy", false);
  }
  detailField(provided, base + "fact_ref", "事实来源编号", "ref");
  if (module.fact !== "price") {
    detailField(provided, base + "image", "本机图片" + (module.image_required ? "" : "（选填）"), "file", module.image_required);
    detailField(provided, base + "image_ref", "图片来源编号" + (module.image_required ? "" : "（与图片同时填写）"), "ref", module.image_required);
  }
  fields.append(provided);
  if (allowSizeNA) fields.append(notApplicable);
  const update = () => {
    provided.hidden = provided.disabled = state.value !== "provided";
    notApplicable.hidden = notApplicable.disabled = state.value !== "not_applicable";
  };
  state.addEventListener("change", update);
  update();
  return fields;
}

function updateLongDetailControls(active, busy) {
  const form = el("long-detail-input");
  form.hidden = !active;
  form.disabled = !active || busy;
  if (!active) return;
  const site = el("role-site").value;
  if (form.dataset.site === site) return;
  const config = context.session.long_detail;
  form.dataset.site = site;
  const content = el("long-detail-fields");
  content.replaceChildren();
  detailField(content, "detail-product", "合成商品编号", "ref");
  const hint = document.createElement("small");
  hint.textContent = "请上传本机 PNG 或 JPEG，每张最多 " + config.max_file_bytes / mebibyte +
    " MiB。可选模块没有资料时保留待核。文案留空时保存为待提炼。";
  content.append(hint);
  if (site === "PH") {
    const description = detailGroup("独立英文商品描述（选填）");
    detailField(description, "detail-description", "英文描述（至少 30 词）", "copy", false);
    detailField(description, "detail-description-ref", "描述来源编号", "ref", false);
    content.append(description);
  }
  content.append(...config.sites[site].map((module, index) => makeDetailModule(module, index,
    site === "PH" && module.id === "size_choice")));
}

function readLongDetail(data, files) {
  const config = context.session.long_detail;
  const ph = data.site === "PH";
  const reading = id => el(id).value.trim();
  data.scope = "synthetic_offline";
  data.product_id = reading("detail-product");
  if (ph) {
    Object.assign(data, {locale: "en-PH", currency: "PHP"});
    const text = el("detail-description").value;
    const source_ref = reading("detail-description-ref");
    if (Boolean(text.trim()) !== Boolean(source_ref)) throw new Error("英文描述和描述来源编号须同时填写。");
    if (text.trim()) data.product_description = {text, source_ref};
  }
  data.modules = config.sites[data.site].map((module) => {
    const prefix = "detail-" + module.id + "-";
    const status = reading(prefix + "status");
    const row = {module: module.id};
    if (module.fact) {
      const values = status === "unknown" ? {} : Object.fromEntries(
        detailFactLabels[module.fact].map(([key]) => [key, reading(prefix + key)]));
      data[module.fact] = status === "unknown" ? {status} : {status, ...values, source_ref: reading(prefix + "fact_ref")};
      if (module.fact === "dimensions" && status === "provided") data.dimensions.unit = "cm";
    } else {
      row.status = status;
      if (status === "not_applicable") row.fact_ref = reading(prefix + "na_ref");
      if (status === "provided") {
        row.title = el(prefix + "title").value;
        row.body = el(prefix + "body").value;
        row.fact_ref = reading(prefix + "fact_ref");
      }
    }
    if (status === "provided" && module.fact !== "price") {
      const file = el(prefix + "image").files[0];
      const ref = reading(prefix + "image_ref");
      if (Boolean(file) !== Boolean(ref)) throw new Error(module.label + "的图片与图片来源编号须同时填写。");
      if (file) {
        if (!file.size || file.size > config.max_file_bytes || !/\.(png|jpe?g)$/i.test(file.name)) {
          throw new Error(module.label + "需要符合大小要求的 PNG 或 JPEG。");
        }
        row.image_ref = ref;
        files.push(file);
      }
    }
    return row;
  });
}


function roleMediaLimits(tool) {
  if (!Number.isSafeInteger(tool?.max_media_files) || tool.max_media_files < 1) return null;
  const total = tool.max_media_bytes;
  return {
    count: tool.max_media_files,
    bytes: Number.isSafeInteger(total) && total > 0 ? total : defaultRoleMediaBytes,
    required: tool.media_required !== false
  };
}

const roleDrafts = new Map();
let roleDraftKey = "";
let roleExampleUndo = null;

function setInputFiles(input, files) {
  if (!files.length) {
    input.value = "";
    return true;
  }
  try {
    const transfer = new DataTransfer();
    for (const file of files) transfer.items.add(file);
    input.files = transfer.files;
    return input.files.length === files.length;
  } catch {
    input.value = "";
    return false;
  }
}

function restoreRoleDraft(key) {
  if (roleDraftKey === key) return;
  reviseWorkflowForm("role");
  const input = el("role-input");
  const media = el("role-media");
  if (roleDraftKey) roleDrafts.set(roleDraftKey, {text:input.value, files:[...media.files], undo:roleExampleUndo});
  const draft = roleDrafts.get(key);
  input.value = draft?.text || "";
  roleExampleUndo = draft?.undo || null;
  roleDraftKey = key;
  input.removeAttribute("aria-invalid");
  notice("role-form-status", setInputFiles(media, draft?.files || []) ? "" : "任务文字已恢复，请重新选择该工具的素材。");
}

function updateRoleControls() {
  const tools = availableRoleTools();
  const select = el("role-tool");
  setOptions(select, tools.map((tool) => [tool.id, tool.label]), select.value || tools[0]?.id);
  if (!select.value && tools.length) select.value = tools[0].id;
  const tool = selectedRoleTool();
  restoreRoleDraft(tool ? el("role-site").value + "/" + tool.id : "");
  const busy = ownsWorkflowSubmission("role");
  select.disabled = !tool || busy;
  el("role-site").disabled = busy;
  const isMainImage = tool?.id === mainImageTool.id;
  const isLongDetail = tool?.id === longDetailTool.id;
  el("role-input-field").hidden = !tool || isMainImage || isLongDetail;
  el("role-input").disabled = el("role-input-field").hidden || busy;
  updateMainImageControls(isMainImage, busy);
  updateLongDetailControls(isLongDetail, busy);
  el("role-description").textContent = tool?.description ||
    (tool ? "" : context.session ? "此站点暂未开放岗位工具。" : "正在读取可用工具。");
  const scope = el("role-scope");
  scope.textContent = typeof tool?.execution_scope === "string" ? tool.execution_scope : "";
  scope.hidden = !scope.textContent;
  el("role-example").hidden = !roleExample(tool);
  el("role-example").disabled = busy;
  el("role-example").textContent = roleExampleUndo ? "撤回示例" : "填入示例";
  el("role-submit").disabled = !tool || Boolean(workflows.role.pending);
  const media = el("role-media");
  const mediaLimits = roleMediaLimits(tool);
  el("role-media-field").hidden = !mediaLimits;
  media.disabled = !mediaLimits || busy;
  media.setAttribute("aria-required", String(Boolean(mediaLimits?.required)));
  el("role-media-limit").textContent = mediaLimits ?
    `${mediaLimits.required ? "请选择" : "可选择"}最多 ${mediaLimits.count} 个 MP4，合计不超过 ${mediaLimits.bytes / mebibyte} MiB。文件名以英文字母或数字开头，仅用英文字母、数字、下划线或连字符。` : "";
}

function updateTranscriptionControls() {
  const config = context.session?.transcription;
  const enabled = Number.isSafeInteger(config?.max_upload_bytes) && config.max_upload_bytes > 0 &&
    Object.keys(config.languages || {}).length > 0;
  const language = el("transcription-language");
  setOptions(language, enabled ? Object.entries(config.languages) : [], language.value || undefined);
  language.disabled = !enabled;
  el("transcription-media").disabled = !enabled;
  el("transcription-submit").disabled = !enabled || Boolean(workflows.transcription.pending);
  el("transcription-limit").textContent = enabled
    ? "支持 WAV、MP4、MOV 和 M4A，最大 " + config.max_upload_bytes / (1024 * 1024) + " MiB，音轨最长 5 分钟。仅在本机处理。"
    : context.session ? "本机转写入口暂不可用。" : "正在读取本机配置。";
}

function renderWorkflowTask(page, task) {
  const panel = el(page + "-result");
  if (!task) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = page === "transcription" ? "选择本地素材后开始转写。" : "提交任务材料后可在这里查看结果。";
    panel.replaceChildren(empty);
    return;
  }
  const summary = document.createElement("div");
  summary.className = "task-summary";
  summary.setAttribute("aria-live", "polite");
  const title = document.createElement("strong");
  title.textContent = task.title || viewLabels[page];
  const status = document.createElement("span");
  status.className = "workflow-state";
  status.textContent = [sites[task.site] || task.site, taskStatus(task)].filter(Boolean).join(" · ");
  summary.append(title, status);
  if (["warehouse-listing-stock-record", "shop-listing-stock-intake"].includes(task.source?.tool_id)
      && task.status === "ready") {
    const stock = task.result;
    const notice = document.createElement("p");
    notice.textContent = stock.stock_reason + "。可售净量：" + (stock.available_units ?? "未知") + "。";
    const receipt = document.createElement("p");
    receipt.textContent = "仓位 " + stock.warehouse_id + " · SKU " + stock.sku_id + " · 第 " + stock.revision + " 版";
    const provenance = document.createElement("p");
    provenance.textContent = stock.upstream.warehouse
      ? "仓库任务 " + stock.upstream.warehouse.task_id + "。"
        + (stock.listing_draft ? "本机上架草稿已就绪。" : "已拦截本机上架草稿。")
      : "仓库任务 " + task.id + "，可交给商城收件。";
    summary.append(notice, receipt, provenance);
  }


  if (task.source?.tool_id === "ad-competitor-channel-intake" && task.status === "ready") {
    const result = task.result;
    const header = document.createElement("p");
    header.textContent = result.decision + " 素材商业使用权待核，因果未核验。";
    const list = document.createElement("ul");
    const labels = {product_card: "商品卡", shoppable_video: "挂链短视频", live: "直播"};
    for (const [name, channel] of Object.entries(result.channels)) {
      const line = document.createElement("li");
      const rate = channel.ratios.click_through;
      const value = rate ? rate.numerator + "/" + rate.denominator :
        channel.issues.clicks ? "未知（点击与曝光口径需核对）" : "未知（无可用分母）";
      line.textContent = labels[name] + "点击率 " + value + "。来源 " +
        (channel.source_refs.join("、") || "缺") + "。";
      list.append(line);
    }
    summary.append(header, list);
  }


  if (task.source?.tool_id === "ph-ad-certification-evidence-intake" && task.status === "ready") {
    const result = task.result;
    const status = document.createElement("p");
    status.textContent = result.summary + " 真实认证、试投和上架仍待授权。";
    const scope = document.createElement("p");
    scope.textContent = "PH · " + result.category + " · " + result.holder_id + " · " +
      result.sku_id + " v" + result.sku_revision;
    const provenance = document.createElement("p");
    provenance.textContent = "选品任务 " + result.upstream.certification.task_id;
    summary.append(status, scope, provenance);
  }
  if (task.source?.tool_id === "ph-live-controller-risk-intake" && task.status === "ready") {
    const result = task.result;
    const status = document.createElement("p");
    status.textContent = result.summary + " 真实价格、语义和签收均待核。";
    const source = document.createElement("p");
    source.textContent = "菲律宾直播任务 " + result.upstream.recording.task_id + " · " +
      result.source_identity.start_seconds + "–" + result.source_identity.end_seconds + " 秒";
    const cues = document.createElement("p");
    cues.textContent = "原保留提示 " + result.original_cue_summary
      .filter(cue => cue.action === "keep").map(cue => cue.id).join("、") + "。";
    summary.append(status, source, cues);
  }
  if (task.source?.tool_id === longDetailTool.id) {
    const number = document.createElement("p");
    number.className = "muted";
    number.textContent = "任务编号 " + task.id;
    summary.append(number);
  }
  if (task.message) {
    const message = document.createElement("p");
    message.textContent = task.message;
    summary.append(message);
  }
  panel.replaceChildren(summary);
  const labels = {
    json: "下载 JSON", text: "下载文本", srt: "下载 SRT 字幕", video: "下载 MP4",
    report: "下载报告", receipt: "下载回执", timeline: "下载时间线", input: "下载输入记录", contact: "下载抽帧总览"
  };
  const download = document.createElement("div");
  download.className = "workflow-actions";
  const artworkFiles = {
    "cn-director-version-delivery": [["director_bundle", "下载多版本交付包", "director-versions.zip"]],
    "cn-shop-poster-version-receive": [["bundle", "下载海报源档包", "poster-source.zip"]],
    "ph-shop-poster-archive-takeout": [["bundle", "下载海报源档包", "poster-source.zip"]],
    "main-image-set": [["preview", "下载主图总览", "main-images-preview.png"], ["bundle", "下载主图源档包", "main-images.zip"]],
    "long-product-detail": [["preview", "下载商详预览", "long-detail-preview.png"], ["detail_bundle", "下载长商详源档包", "long-detail.zip"]],
  }[task.source?.tool_id];
  if (artworkFiles || ["director-media-visual-intake", "visual-video-cover", "visual-surface-render",
       "shop-visual-receive", "campaign-visual-receive", "live-visual-receive", "ph-competitor-reviewed-evidence", "visual-poster-version-delivery"].includes(task.source?.tool_id)) {
    const bundleName = {"visual-video-cover":"video-cover-source.zip",
      "director-media-visual-intake":"director-media-bundle.zip", "visual-poster-version-delivery":"poster-source.zip"}[task.source.tool_id] ?? "visual-surface.zip";
    const files = artworkFiles ?? (task.source.tool_id === "ph-competitor-reviewed-evidence"
      ? [["evidence_bundle", "下载图文与词云证据", "ph-reviewed-evidence.zip"]]
      :
      task.source.tool_id === "visual-poster-version-delivery"
      ? [["bundle", "下载海报源档包", bundleName]]
      : [["preview", "下载 PNG 封面", "video-cover.png"], ["bundle", "下载本机交付包", bundleName]]);
    const combinedFiles = task.source.tool_id === "cn-shop-poster-version-receive" ? [
      ["mall_main_bundle", "下载同版主图文件", "main-images.zip"],
      ["mall_detail_bundle", "下载同版商详文件", "product-detail.zip"],
      ["mall_combined_receipt", "下载组合收件回执", "combined-receipt.json"],
    ] : [];
    for (const [kind, label, filename] of [...files, ...combinedFiles]) {
      const path = localFile(task.artifacts?.[kind], true);
      if (!path || !path.startsWith("/api/tasks/" + task.id + "/files/")) continue;
      const link = document.createElement("a");
      link.className = "button outline";
      link.href = path;
      link.textContent = label;
      link.addEventListener("click", async event => {
        event.preventDefault();
        if (link.getAttribute("aria-disabled") === "true") return;
        link.setAttribute("aria-disabled", "true");
        const statusId = page === "role" ? "role-download-status" : page + "-form-status";
        const report = (message, error) => {
          if (context.page === page && workflows[page].task?.id === task.id) notice(statusId, message, error);
        };
        report("正在检查并准备本机文件…");
        try {
          const response = await fetch(localFile(task.artifacts[kind]), {cache: "no-store"});
          if (response.status !== 200) throw new Error("Local artifact unavailable");
          const content = await response.blob();
          if (!content.size) throw new Error("Empty local artifact");
          const objectUrl = URL.createObjectURL(content);
          const save = document.createElement("a");
          save.href = objectUrl;
          save.download = filename;
          document.body.append(save);
          save.click();
          save.remove();
          setTimeout(() => URL.revokeObjectURL(objectUrl), 5000);
          report("文件已交给浏览器下载。");
        } catch {
          report("未能取到此文件。可从当前任务重新下载。", true);
        } finally {
          link.removeAttribute("aria-disabled");
        }
      });
      download.append(link);
    }
  }
  if (["cn-shop-image-review", "ph-shop-image-review"].includes(task.source?.tool_id)) {
    const preview = localFile(task.artifacts?.preview, false);
    if (preview === "/api/tasks/" + task.id + "/files/preview") {
      const link = document.createElement("a");
      link.className = "button outline";
      link.href = preview;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "查看已绑定原图";
      download.append(link);
    }
  }
  for (const [name, label] of Object.entries(labels)) {
    const path = localFile(task.artifacts?.[name], true);
    if (!path || !path.startsWith("/api/tasks/" + encodeURIComponent(task.id) + "/files/")) continue;
    const link = document.createElement("a");
    link.className = "button outline";
    link.href = path;
    link.textContent = label;
    download.append(link);
  }
  if (download.childElementCount) panel.append(download);
  if (["teardown-listening-review-import", "live-listening-review-receive",
       "live-risk-context-rehearsal"].includes(task.source?.tool_id)) {
    renderLiveListening(panel, task.result);
  }
  const archiveKinds = [["mall_front_original", "商城收件原图"], ["mall_detail_original", "商城收件整页"]];
  if (archiveKinds.every(([kind]) => task.artifacts?.[kind] === "/api/tasks/" + task.id + "/files/" + kind)) {
    const receipt = localFile(task.artifacts.mall_visual_receipt, true);
    const product = task.result.visual_delivery.listing;
    const archive = document.createElement("details");
    archive.className = "mall-visual-archive";
    const title = document.createElement("summary");
    title.textContent = "查看商城已收同版原图";
    archive.append(title);
    const limits = document.createElement("p");
    limits.textContent = "SKU " + product.sku_id + " · v" + product.version + " · 合成归档。实物事实、资格和发布授权仍待核。";
    archive.append(limits);
    if (receipt) {
      const link = document.createElement("a");
      link.href = receipt;
      link.textContent = "下载原图与版式回执";
      archive.append(link);
    }
    for (const [kind, label] of archiveKinds) {
      const figure = document.createElement("figure");
      figure.style.margin = "1rem 0";
      const image = document.createElement("img");
      image.alt = label;
      image.className = "mall-visual-image";
      image.style.display = "block";
      image.style.maxWidth = "100%";
      image.style.height = "auto";
      const caption = document.createElement("figcaption");
      caption.textContent = label + " · 原图底部";
      figure.append(image, caption);
      archive.append(figure);
      archive.addEventListener("toggle", () => {
        if (archive.open && !image.hasAttribute("src")) image.src = task.artifacts[kind];
      });
    }
    panel.append(archive);
  }
  if (page === "transcription" && Array.isArray(task.result?.segments)) {
    const previewText = task.result.segments.filter((segment) => typeof segment?.text === "string")
      .map((segment) => segment.text.trim()).filter(Boolean).join("\n");
    if (previewText) {
      const preview = document.createElement("pre");
      preview.className = "transcript-preview";
      preview.setAttribute("aria-label", "转写文本预览");
      preview.textContent = previewText.length > 20000
        ? previewText.slice(0, 20000) + "\n…请下载完整文本。" : previewText;
      panel.append(preview);
    }
  }
  if (task.result !== null && typeof task.result === "object") {
    const details = document.createElement("details");
    const heading = document.createElement("summary");
    heading.textContent = "查看结果详情";
    const contents = document.createElement("pre");
    const formatted = JSON.stringify(task.result, null, 2);
    contents.textContent = formatted.length > 20000 ? formatted.slice(0, 20000) + "\n…请下载完整结果。" : formatted;
    details.append(heading, contents);
    panel.append(details);
  }
}

function showWorkflowTask(page, task) {
  const state = workflows[page];
  const changed = JSON.stringify(state.task) !== JSON.stringify(task);
  clearTimeout(state.poll);
  state.task = task;
  state.failures = 0;
  storeTask(task);
  if (context.page === "tasks") renderTaskList();
  if (changed) renderWorkflowTask(page, task);
  pollWorkflow(page, task.id);
}

function pollWorkflow(page, id) {
  const state = workflows[page];
  if (state.task?.id !== id || !activeStates.has(state.task.status)) return;
  state.poll = setTimeout(async () => {
    try {
      const body = await request("/api/tasks/" + encodeURIComponent(id));
      if (state.task?.id !== id) return;
      if (body.task?.id !== id || pageForTask(body.task) !== page) throw new Error("未能读取当前任务。");
      showWorkflowTask(page, body.task);
    } catch (error) {
      if (state.task?.id !== id) return;
      if (++state.failures <= 3) pollWorkflow(page, id);
      else notice(page + "-form-status", error.message + " 可在任务记录中重新查看状态。", true);
    }
  }, 800);
}

function readMedia(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === "string" && reader.result.startsWith("data:")
      ? resolve(reader.result) : reject(new Error("无法读取该媒体文件。"));
    reader.onerror = () => reject(new Error("无法读取该媒体文件。"));
    reader.readAsDataURL(file);
  });
}

function ownsWorkflowSubmission(page) {
  const state = workflows[page];
  const pending = state.pending;
  return Boolean(pending && context.page === page && context.navigation === pending.navigation &&
    state.task?.id === pending.taskId && state.formRevision === pending.formRevision);
}

function clearWorkflowProgress(page) {
  const pending = workflows[page]?.pending;
  if (pending?.progress && el(page + "-form-status").textContent === pending.progress) notice(page + "-form-status");
}

function reviseWorkflowForm(page) {
  workflows[page].formRevision += 1;
  clearWorkflowProgress(page);
}

async function submitWorkflow(event, page) {
  event.preventDefault();
  const state = workflows[page];
  if (state.pending || !context.session || context.page !== page) return;
  const status = page + "-form-status";
  const site = el(page + "-site").value;
  const data = { site };
  let files = [];
  const tool = page === "role" ? selectedRoleTool() : null;
  const isMainImage = tool?.id === mainImageTool.id;
  const isLongDetail = tool?.id === longDetailTool.id;
  let endpoint = "/api/tasks/" + page;
  if (isMainImage) {
    endpoint = "/api/tasks/main-images";
    const config = context.session.main_images;
    data.scope = "synthetic_offline";
    data.product_id = el("main-image-product").value;
    const extraEdge = el("main-image-extra-edge");
    const extraRef = el("main-image-extra-ref");
    if (extraEdge.value || extraRef.value) {
      if (!extraEdge.value || !extraRef.value) {
        notice(status, "附加方图需要同时填写边长与规格来源编号。", true);
        return;
      }
      data.additional_output = {edge: extraEdge.valueAsNumber, spec_ref: extraRef.value};
    }
    data.cards = config.slots.map((slot) => {
      const prefix = "main-image-" + slot + "-";
      files.push(el(prefix + "image").files[0]);
      return { slot, title: el(prefix + "title").value, subtitle: el(prefix + "subtitle").value,
        source_ref: el(prefix + "source_ref").value };
    });
    if (files.some((file) => !file?.size || file.size > config.max_file_bytes || !/\.(png|jpe?g)$/i.test(file.name))) {
      notice(status, "请为每个槽位选择符合大小要求的 PNG 或 JPEG。", true);
      return;
    }
    if (site === "PH") {
      Object.assign(data, {locale: "en-PH", currency: "PHP", price: {status: "unknown"}, dimensions: {status: "unknown"}});
    }
  } else if (isLongDetail) {
    endpoint = "/api/tasks/long-detail";
    try { readLongDetail(data, files); }
    catch (error) { notice(status, error.message, true); return; }
  } else if (page === "transcription") {
    const configuration = context.session.transcription;
    if (!configuration) return;
    data.language = el("transcription-language").value;
    const file = el("transcription-media").files[0];
    if (!file || !file.size || file.size > configuration.max_upload_bytes || !/\.(wav|mp4|mov|m4a)$/i.test(file.name)) {
      notice(status, "请选择符合格式和大小要求的非空媒体文件。", true);
      return;
    }
    files = [file];
  } else {
    if (!tool) return;
    data.tool_id = tool.id;
    try { data.input = JSON.parse(el("role-input").value); }
    catch {
      notice(status, "任务材料有 JSON 格式错误，请检查引号、逗号和括号。", true);
      el("role-input").setAttribute("aria-invalid", "true");
      el("role-input").focus();
      return;
    }
    if (!data.input || typeof data.input !== "object" || Array.isArray(data.input)) {
      notice(status, "任务材料须为 JSON 对象，例如 { \"商品\": \"示例\" }。", true);
      el("role-input").setAttribute("aria-invalid", "true");
      el("role-input").focus();
      return;
    }
    const mediaLimits = roleMediaLimits(tool);
    if (mediaLimits) {
      files = Array.from(el("role-media").files);
      if (mediaLimits.required && files.length === 0) {
        notice(status, "请先选择本地 MP4 素材。", true);
        return;
      }
      if (files.length > mediaLimits.count || files.reduce((size, file) => size + file.size, 0) > mediaLimits.bytes) {
        notice(status, `最多选择 ${mediaLimits.count} 个 MP4，合计不超过 ${mediaLimits.bytes / mebibyte} MiB。`, true);
        return;
      }
      const names = new Set(files.map((file) => file.name.toLowerCase()));
      if (names.size !== files.length || files.some((file) => !file.size || !roleVideoFilename.test(file.name))) {
        notice(status, "请选择非空且文件名互不重复的 MP4。文件名须符合素材要求。", true);
        return;
      }
    }
  }
  const signature = JSON.stringify(data);
  const retryFailedTask = state.task?.id && state.task.id === state.submission?.taskId &&
    ["failed", "interrupted"].includes(state.task.status);
  const previousFiles = state.submission?.files;
  const sameFiles = previousFiles?.length === files.length && files.every((file, index) => file === previousFiles[index]);
  if (retryFailedTask || state.submission?.signature !== signature || !sameFiles) {
    state.submission = { signature, files, id: requestId() };
  }
  const submission = state.submission;
  data.request_id = submission.id;
  const pending = {navigation: context.navigation, taskId: state.task?.id, formRevision: state.formRevision};
  state.pending = pending;
  const progress = (message) => {
    if (!ownsWorkflowSubmission(page)) return;
    pending.progress = message;
    notice(status, message);
  };
  const update = page === "transcription" ? updateTranscriptionControls : updateRoleControls;
  update();
  try {
    if (files.length) {
      const media = [];
      for (const [index, file] of files.entries()) {
        progress(`正在读取本机素材（${index + 1}/${files.length}）。`);
        media.push({ name: file.name, data: await readMedia(file) });
      }
      if (isMainImage) {
        data.cards.forEach((card, index) => { card.image = media[index]; });
      } else if (isLongDetail) {
        data.modules.filter((module) => module.image_ref).forEach((module, index) => { module.image = media[index]; });
      } else {
        data.media = page === "transcription" ? media[0] : media;
      }
    }
    progress("正在提交任务。");
    const {task} = await request(endpoint, data);
    if (!task?.id || pageForTask(task) !== page) throw new Error("未收到有效任务回执，请查看任务记录。");
    submission.taskId = task.id;
    if (ownsWorkflowSubmission(page)) {
      notice(status);
      showWorkflowTask(page, task);
    } else {
      storeTask(task);
      if (context.page === "tasks") renderTaskList();
      document.dispatchEvent(new Event("dt-tasks"));
    }
  } catch (error) {
    if (ownsWorkflowSubmission(page)) notice(status, error.message, true);
  } finally {
    state.pending = null;
    update();
  }
}

function editClose() {
  context.isEditing = false;
  el("edit-form").hidden = true;
  el("edit-toggle").setAttribute("aria-expanded", "false");
}

function editAvailable() {
  return Boolean(currentVersion()?.selected_elements?.length && context.version === latestVersion() &&
    !activeStates.has(context.task?.status));
}

function showCustomerAgent(task) {
  const panel = el("poster-agent-panel");
  panel.hidden = customerModelWorkspace() || !context.session?.poster_agent_fixture || !task?.copy_intake || task.site !== "CN";
  if (panel.hidden) return;
  const states = { starting: "正在启动本机客户会话", drafting: "等待模拟模型选择工具",
    waiting_for_review: "卖点已回给会话，请在本机完成排版审校", receiving: "同一会话正在调用商城收件",
    completed: "本机模拟工具回合已完成", blocked: "本机模拟未通过" };
  el("poster-agent-start").hidden = Boolean(task.agent_session) || task.status !== "awaiting_selling_points";
  el("poster-agent-status").textContent = task.agent_session
    ? states[task.agent_session.state] || ""
    : "隔离测试。使用模拟模型和本机卖点、商城收件工具。";
}
async function startCustomerAgent() {
  const task = context.task;
  const button = el("poster-agent-start");
  if (!task || button.hidden || button.disabled) return;
  button.disabled = true;
  try {
    const endpoint = "/api/tasks/" + encodeURIComponent(task.id);
    const response = await request(endpoint + "/agent-session", {});
    if (context.task?.id !== task.id) return;
    adoptTask(response.task);
    const then = Date.now() + 170000;
    async function refresh() {
      if (context.task?.id !== task.id || Date.now() > then) return;
      try {
        const {task: latest} = await request(endpoint);
        if (context.task?.id !== task.id) return;
        if (latest.status !== context.task.status ||
            latest.agent_session?.state !== context.task.agent_session?.state) adoptTask(latest);
        if (!["completed", "blocked"].includes(latest.agent_session?.state)) setTimeout(refresh, 600);
      } catch {
        setTimeout(refresh, 850);
      }
    }
    setTimeout(refresh, 300);
  } catch(error) {
    el("poster-agent-status").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

const detailQuestions = {
  fit: {
    question: "谁能用？能用在哪儿？",
    example: "适合 6 岁以上，或适用于某个型号。如有限制，请写清楚。",
  },
  quality: {
    question: "用什么做的？哪里有特点？",
    example: "外壳是 ABS，边角有软胶。只写自己确认的内容。",
  },
  spec: {
    question: "多大？一盒有几个？",
    example: "长 20 厘米，一盒 2 个。按这件商品实际的单位和数量填写。",
  },
  use: {
    question: "怎么用？",
    example: "打开后扣在支架上。",
  },
};

function detailErrorText(error) {
  const byMessage = {
    detail_fact_too_long: "有一项写得太长，请改成一句短话，保留关键的数字、单位或限制，再保存。",
    detail_fact_rejected: "有一项文字无法保存。请只写一句你确认的信息，去掉换行，再保存。",
    detail_no_prior_evidence: "这一项还没有保存过照片，请选择商品原图或另选一张照片。",
    detail_asset_missing: "有一张已选的照片没找到，请重新选择那张照片再保存。",
    detail_asset_changed: "有一张已选的照片已变化，请重新选择那张照片再保存。",
    detail_asset_path_changed: "有一张已选的照片没有读到，请重新选择再保存。",
    detail_asset_binding_invalid: "有一张已选照片无法核对，请重新选择再保存。",
    detail_image_input_shape: "补充照片没读到。请选择不超过 4 MiB 的 PNG 或 JPG 照片。",
    detail_input_shape: "商品详情页的表单没有读全。请在同一任务重新打开「补充商品信息」再保存。",
    detail_wrong_task: "这个任务没有可补充的商品详情页。请从任务记录打开原任务。",
    "商详证据转换为本机 PNG 后合计须不超过 16 MiB。请缩小尺寸再传。":
      "这几张照片处理后合计超过 16 MiB。请缩小补充照片的尺寸再保存。",
  };
  if (Object.hasOwn(byMessage, error.message)) return byMessage[error.message];
  const byCode = {
    invalid_image: "有一张图没读到。请检查商品原图和补充照片，使用不超过 4 MiB 的 PNG 或 JPG。",
    invalid_payload: "这次没能保存。请检查每张照片不超过 4 MiB，或缩小补充照片后再试。",
    detail_edition_not_available: "此时不能修改。请在任务记录打开这份任务，等正在进行的操作结束后再试。",
    invalid_detail_evidence: "商品信息没能保存。请检查回答和照片，再试一次。",
    visual_material_missing: "制作商品详情页前，还需要补充页面列出的文字或照片。",
    wrong_detail_payload: "没有读到完整的商品信息表单，请重新打开再保存。",
    visual_evidence_scope: "要一起做详情页，请选择抖音中国，上传商品原图，填商品编号，并留空已确认的海报文案。",
    foreground_origin_required: "请在本机工作台打开原任务后再试。",
  };
  return Object.hasOwn(byCode, error.code) ? byCode[error.code] : (error.message || "这次没能保存，请再试一次。");
}

function detailTaskMessage(message = "") {
  const visible = {
    "已保存本机任务。请补齐下方标出的事实和图片。": "任务已保存。制作商品详情页前，请补充下方列出的文字或照片。",
    "商详资料已在本机保存。": "商品信息已保存。",
  };
  return Object.hasOwn(visible, message) ? visible[message] : message;
}

function detailFields(container, fields = {}) {
  for (const [key, item] of Object.entries(detailQuestions)) {
    const saved = fields[key];
    const prefix = container.id + "-" + key;
    const field = document.createElement("fieldset");
    field.innerHTML = `
      <legend>${item.question}</legend>
      <label for="${prefix}-fact">你的回答</label>
      <small id="${prefix}-example">示例（只供参考） ${item.example}</small>
      <textarea id="${prefix}-fact" rows="2" maxlength="160" aria-describedby="${prefix}-example"></textarea>
      <label for="${prefix}-source">哪张照片能说明这一项？</label>
      <select id="${prefix}-source">
        <option value="">晚点选照片</option>
        <option value="original">用这件商品的原图</option>
        <option value="evidence">另选一张照片</option>
      </select>
      <label for="${prefix}-image" hidden>选择新照片（PNG 或 JPG，不超过 4 MiB）</label>
      <input id="${prefix}-image" type="file" accept="image/png,image/jpeg,.png,.jpg,.jpeg" hidden>`;
    const select = field.querySelector("select");
    if (saved?.image_kind) {
      const label = saved.image_kind === "original" ? "保留已选的商品原图" : "保留已经选好的照片";
      select.prepend(new Option(label, "keep", true, true));
    }
    field.querySelector("textarea").value = typeof saved?.fact === "string" ? saved.fact : "";
    const image = field.querySelector('input[type="file"]');
    select.addEventListener("change", () => {
      const visible = select.value === "evidence";
      image.hidden = !visible;
      image.previousElementSibling.hidden = !visible;
    });
    container.append(field);
  }
}

function detailMissingText(missing) {
  const grouped = new Map();
  for (const {field, reason} of missing) {
    const question = detailQuestions[field]?.question;
    if (!question || !["fact", "image"].includes(reason)) continue;
    if (!grouped.has(question)) grouped.set(question, []);
    grouped.get(question).push(reason === "fact" ? "回答" : "照片");
  }
  return [...grouped].map(([question, needs]) => "「" + question + "」还缺" + needs.join("和")).join("。");
}

function collectDetailEvidence(container, hasOriginal) {
  const detail = {};
  const missing = [];
  for (const [key, item] of Object.entries(detailQuestions)) {
    const prefix = container.id + "-" + key;
    const fact = el(prefix + "-fact").value.trim();
    const source = el(prefix + "-source").value;
    const file = source === "evidence" && el(prefix + "-image").files[0];
    const image = source === "keep" || (source === "original" && hasOriginal) ? source : file || null;
    if (file && (!file.size || file.size > 4 * 1024 * 1024 || !/\.(png|jpe?g)$/i.test(file.name))) {
      throw new Error("「" + item.question + "」的照片须为不超过 4 MiB 的 PNG 或 JPG。请重新选择。");
    }
    if (!fact) missing.push({field:key, reason:"fact"});
    if (!image) missing.push({field:key, reason:"image"});
    detail[key] = {fact, image};
  }
  return {detail, missing};
}

async function encodeDetailEvidence(detail) {
  for (const [key, entry] of Object.entries(detail)) {
    if (entry.image && typeof entry.image === "object") {
      try {
        entry.image = {name: entry.image.name, data: await fileData(entry.image)};
      } catch {
        throw new Error("「" + detailQuestions[key].question + "」的照片没读到。请重新选择。");
      }
    }
  }
  return detail;
}

function updateCombinedForm() {
  const checkbox = el("poster-combined-detail");
  checkbox.disabled = el("site").value !== "CN";
  if (checkbox.disabled) checkbox.checked = false;
  const checked = checkbox.checked;
  const container = el("poster-combined-fields");
  const message = el("poster-combined-missing");
  container.hidden = !checked;
  el("poster-combined-note").hidden = !checked;
  message.hidden = !checked;
  el("make-poster").textContent = checked ? "保存任务" : "生成海报";
  if (!checked) return;
  try {
    const {missing} = collectDetailEvidence(container, Boolean(el("product-file").files[0]));
    const fields = detailMissingText(missing);
    message.textContent = fields ? "可以先保存，做商品详情页之前再补。 " + fields + "。" :
      "已填完四项。请确认照片能说明对应的回答。";
  } catch (error) {
    message.textContent = error.message;
  }
}

function showTaskDetail(task) {
  const status = task?.token_plan_acceptance;
  const detail = status?.detail;
  const panel = el("poster-task-detail-edit");
  const message = el("poster-task-detail-summary");
  const requested = detail?.requested === true;
  const editable = requested && task.status === "awaiting_selling_points" &&
    (!task.agent_session || task.agent_session.state === "blocked") &&
    ["waiting_for_detail_evidence", "ready_for_customer", "blocked"].includes(status.state);
  panel.hidden = !editable;
  message.hidden = !editable;
  if (!editable) panel.open = false;
  if (!requested) return;
  const missing = detailMissingText(detail.missing || []);
  message.textContent = detail.ready === true ? "商品信息齐了，可以开始制作主图、海报和商品详情页。" :
    (missing ? "任务已保存。做商品详情页之前还需要补齐。 " + missing + "。" : "做商品详情页之前，请补充商品信息。");
  const fields = el("poster-task-detail-fields");
  const binding = task.id + ":" + JSON.stringify(detail.fields);
  if (editable && fields.dataset.binding !== binding) {
    if (fields.dataset.binding?.split(":")[0] !== task.id) panel.open = false;
    fields.replaceChildren();
    detailFields(fields, detail.fields);
    fields.dataset.binding = binding;
    notice("poster-task-detail-error");
  }
  el("poster-task-detail-save").disabled = Boolean(context.detailSaving || context.tokenPlanStarting);
}

async function saveTaskDetail(event) {
  event.preventDefault();
  const task = context.task;
  const panel = el("poster-task-detail-edit");
  if (!task || panel.hidden || context.detailSaving || context.tokenPlanStarting) return;
  context.detailSaving = task.id;
  const button = el("poster-task-detail-save");
  const status = "poster-task-detail-error";
  button.disabled = true;
  showTokenPlan(task);
  try {
    const {detail} = collectDetailEvidence(el("poster-task-detail-fields"), true);
    const body = await request("/api/tasks/" + encodeURIComponent(task.id) + "/detail-evidence",
      {detail: await encodeDetailEvidence(detail)});
    if (context.task?.id !== task.id) return;
    if (body.task?.id !== task.id) throw new Error("未能读取当前任务。");
    adoptTask(body.task);
    notice(status, "商品信息已保存。");
  } catch (error) {
    if (context.task?.id === task.id) notice(status, detailErrorText(error), true);
  } finally {
    context.detailSaving = null;
    showTokenPlan(context.task);
  }
}

function showTokenPlan(task) {
  const status = task?.token_plan_acceptance;
  const isPreview = context.session?.model_workspace_preview === true;
  const isCustomer = customerModelWorkspace();
  const isWorkspace = isPreview || isCustomer;
  if (isCustomer) el("poster-token-plan-start").textContent = "用此模型开始";
  el("poster-customer-consent-note").hidden = !isCustomer || !task;
  if (isPreview) el("poster-token-plan-start").textContent = "用所选模型开始本机预检";
  el("poster-token-plan-status").hidden = !task;
  showTaskDetail(task);
  if (window.DTWorkspace?.showOrdinaryVisualChain(task)) return;
  if (!status) {
    el("poster-token-plan-new-scope").hidden = true;
    el("poster-token-plan-old-notice").hidden = true;
    el("poster-token-plan-new-notice").hidden = true;
    el("poster-token-plan-receipt").hidden = true;
    el("poster-token-plan-launch").hidden = v2ModelWorkspace() || !task;
    el("poster-token-plan-start").disabled = Boolean(context.tokenPlanStarting || context.detailSaving);
    el("poster-token-plan-detail").textContent = task ? modelAccessReason(task) : "";
    return;
  }
  const isNewPoster = status.flow === "new_poster";
  const combined = isNewPoster && status.detail?.requested === true;
  el("poster-token-plan-new-scope").textContent = combined
    ? "用你确认的文字和已选照片，在本机制作海报、主图和商品详情页。完成后可到商城领取同版文件。"
    : "海报采用本机上传原图和本地排版，提供 720×880 PNG 及可编辑 PSD。";
  el("poster-token-plan-old-notice").hidden = isNewPoster;
  el("poster-token-plan-new-notice").hidden = !isNewPoster;
  el("poster-token-plan-new-scope").hidden = !isNewPoster;
  const evidence = {
    simulation: "当前已有本机模拟证据。",
    fixed_backend: "当前已有固定文案流程证据。",
    confirmed_copy: "当前已有人工确认的原稿。",
    local_task: "当前已有本机任务证据。",
  };
  const descriptions = {
    ready_for_customer: "可由本人发起已审稿件的模型工具回合核对。",
    starting: "正在等待本机确认。",
    drafting: "正在核对所选模型的工具调用及卖点 Skill 回传。",
    receiving: "正在核对商城 Agent 的同版收件及结果回传。",
    completed: "已审稿件的模型工具回合和商城同版收件已完成。此回执对应既有稿件的核对。",
    blocked: "本次点验未完成，请查看当前任务状态。",
    disabled: "打开连接入口可检查当前任务的开始条件。",
  };
  if (isNewPoster) {
    descriptions.ready_for_customer = "请本人确认发起新商品海报会话。";
    descriptions.drafting = "待模型选择卖点 Skill 和排版 Skill，并接收工具回传。";
    descriptions.waiting_for_review = "模型排版计划已保存。请核对原图、卖点和图层计划，再确认渲染。";
    descriptions.receiving = "正在本机渲染或等待商城 Agent 回传同版收件。";
    descriptions.completed = "本次会话及商城同版收件已完成。请检查新商品 PNG、PSD 和商城收件凭证。";
  }
  if (combined) {
    descriptions.ready_for_customer = "商品信息已齐，你可以开始制作海报、主图和商品详情页。";
    descriptions.waiting_for_detail_evidence = "";
    descriptions.drafting = "模型正在整理卖点，准备海报、主图和商品详情页。";
    descriptions.waiting_for_review = "排版计划已保存。请核对商品照片、详情页信息和文案，再确认出图。";
    descriptions.receiving = "正在把主图、海报和商品详情页交给商城。";
    descriptions.completed = "已完成。可从商城领取同版海报、主图和商品详情页。";
  }
  if (v2ModelWorkspace() && ["disabled", "ready_for_customer"].includes(status.state) && (!combined || status.detail.ready)) {
    descriptions[status.state] = "真实商品会话尚未启用。可在模型连接查看各用途的配置和验证状态。";
  }
  const hint = status.state === "disabled" || !Object.hasOwn(descriptions, status.state)
    ? evidence[status.evidence] || "" : "";
  const messages = {
    foreground_session_closed: "本人关闭确认窗或等待确认超时。",
    desktop_required: "此 Windows 桌面无法显示本机确认窗。",
    provider_auth_rejected: "模型服务未通过本次凭据校验。",
    provider_limit: "本次会话触及所选服务的额度或限流。",
    provider_response_rejected: "所选模型接口未返回可用结果。",
    tool_binding_or_copy_mismatch: "模型选择的内容与已审稿件或任务版本不一致。",
    model_did_not_call_tool: "模型未调用本轮要求的受限工具。",
    local_tool_rejected: "本机工具拒绝执行或无法核验收件。",
    interrupted: "工作台已关闭，会话已终止。",
  };
  let detail = descriptions[status.state] ?? descriptions.disabled;
  if (status.state === "completed" && ["local_model_simulation", "http_simulation"].includes(status.proof_scope)) {
    detail = "本机模型替身通过工具回合与同版取件。真人点验尚无结果。";
  } else if (status.state === "completed" && status.proof_scope === "foreground_provider_response") {
    detail += " 本次选定的语言模型已返回工具结果。请人工核对成品和同版收件。";
  } else if (status.state === "blocked" && messages[status.reason]) {
    detail = messages[status.reason];
  }
  el("poster-token-plan-detail").textContent = combined && status.state === "ready_for_customer" && !status.detail.ready ? "" : hint + detail;
  el("poster-token-plan-launch").hidden = v2ModelWorkspace() || isWorkspace && ["completed", "starting", "drafting", "waiting_for_review", "receiving"].includes(status.state);
  el("poster-token-plan-new-notice").hidden = isWorkspace || !isNewPoster || !canOpenModelForTask(status);
  el("poster-token-plan-old-notice").hidden = isWorkspace || isNewPoster || !canOpenModelForTask(status);
  el("poster-token-plan-start").disabled = Boolean(context.tokenPlanStarting || context.detailSaving);
  el("poster-token-plan-receipt").hidden = status.state !== "completed" ||
    !/^[0-9a-f]{32}$/.test(status.mall_task_id || "");
}

function canOpenModelForTask(status) {
  return status?.can_start === true && (!status.detail?.requested || status.detail.ready === true)
    && ["ready_for_customer", "blocked"].includes(status.state);
}

function modelAccessReason(task) {
  if (!task) return "你可以先看可用的模型。要开始制作，先上传商品原图，填商品名、商品编号和你确认的短句后保存。做新海报时，把已确认文案留空。只做海报，新建时不用勾选商品详情页。";
  const status = task.token_plan_acceptance;
  if (status?.detail?.requested && !status.detail.ready) {
    const missing = detailMissingText(status.detail.missing || []);
    return (missing ? "你已选做商品详情页。开始制作前，" + missing + "。" : "你已选做商品详情页。开始制作前，请补充商品信息。")
      + "可以晚点填，已上传的图片不用重传。";
  }
  if (tokenPlanActiveStates.has(status?.state)) {
    return "当前已有本机会话。若刚才已打开连接窗，请在 Windows 切换到「连接模型」窗口。";
  }
  if (status?.state === "completed") return "该任务已有收件。可查看同版收件。新一轮请从商品表单保存新任务。";
  if (task.site !== "CN") return "本入口的商品交互目前支持中国抖音。可在商品表单选择中国抖音，再提交新任务。";
  if (task.source?.simulated || task.source?.kind !== "local_upload") return "请在商品表单选择本机商品原图，再保存新任务。";
  if (!task.source?.sku) return "此任务没有商品编号。请在商品表单填写编号并保存，再从结果区发起。";
  if (task.versions?.length || task.confirmed_copy) return "此任务已有成品或已确认文案。要发起新稿，请在商品表单留空已确认文案并保存新任务。";
  if (!status) return "此任务尚未符合交互要求。请在商品表单核对有效商品编号、原图和可核的短事实。";
  return "暂时未能开始。如果另一任务的会话仍在运行，请完成或关闭该会话后，重新点击接入我的模型。";
}

function showModelAccess(task) {
  const modal = el("model-access-dialog");
  if (!modal) return;
  const supplement = task?.token_plan_acceptance?.detail?.requested &&
    !task.token_plan_acceptance.detail.ready && !el("poster-task-detail-edit").hidden;
  el("model-access-reason").textContent = modelAccessReason(task);
  const next = el("model-access-next");
  next.textContent = supplement ? "补充商品信息" : "回到工作台";
  next.onclick = () => {
    modal.close();
    if (supplement) {
      const edit = el("poster-task-detail-edit");
      edit.open = true;
      edit.scrollIntoView({block: "nearest"});
      const first = task.token_plan_acceptance.detail.missing?.[0];
      const input = first && el("poster-task-detail-fields-" + first.field + (first.reason === "image" ? "-source" : "-fact"));
      (input || edit.querySelector("textarea")).focus();
    } else if (!task) {
      el("product-title").focus();
    }
  };
  if (!modal.open) modal.showModal();
}

async function startTokenPlan(event) {
  const task = context.task;
  if (!event.isTrusted || context.tokenPlanStarting || context.detailSaving) return;
  if (context.session?.ordinary_visual_chain === true) {
    window.DTWorkspace?.openOrdinaryVisualChain(task);
    return;
  }
  if (redesignedModelWorkspace() && window.DTWorkspace && !window.DTWorkspace.canStart(task)) return;
  if (!task) {
    showModelAccess(null);
    return;
  }
  const endpoint = "/api/tasks/" + encodeURIComponent(task.id);
  context.tokenPlanStarting = task.id;
  showTokenPlan(task);
  notice("poster-token-plan-error");
  try {
    const {task: latest} = await request(endpoint);
    if (context.task?.id !== task.id || latest?.id !== task.id) return;
    adoptTask(latest);
    if (!canOpenModelForTask(latest.token_plan_acceptance)) {
      if (redesignedModelWorkspace()) {
        notice("poster-token-plan-error", modelAccessReason(latest), true);
        const edit = el("poster-task-detail-edit");
        if (!edit.hidden) edit.open = true;
      } else showModelAccess(latest);
      return;
    }
    const body = await request(endpoint + "/token-plan-foreground", {start: true});
    if (customerModelWorkspace()) window.DTWorkspace.reloadCustomer();
    if (context.task?.id !== task.id) return;
    if (body.task?.id !== task.id) throw new Error("未能读取当前点验任务。");
    adoptTask(body.task);
    context.pollFailures = 0;
    pollTask(task.id);
  } catch (error) {
    if (context.task?.id !== task.id) return;
    try {
      const {task: latest} = await request(endpoint);
      if (context.task?.id !== task.id) return;
      if (latest?.id === task.id) {
        adoptTask(latest);
        context.pollFailures = 0;
        pollTask(task.id);
      }
    } catch {}
    if (context.task?.id === task.id) {
      if (error.code === "visual_material_missing") showModelAccess(context.task);
      notice("poster-token-plan-error", detailErrorText(error), true);
    }
  } finally {
    context.tokenPlanStarting = null;
    showTokenPlan(context.task);
  }
}

function openTokenPlanReceipt() {
  const status = context.task?.token_plan_acceptance;
  if (status?.state === "completed" && /^[0-9a-f]{32}$/.test(status.mall_task_id || "")) {
    openTask(status.mall_task_id, true, {control: el("poster-token-plan-receipt"),
      returnTo: {id: context.task.id, version: context.version}});
  }
}

function showPosterCopy(task) {
  showTokenPlan(task);
  showCustomerAgent(task);
  const panel = el("poster-copy-panel");
  panel.hidden = customerModelWorkspace() || task?.token_plan_acceptance?.flow === "new_poster" ||
    !task?.copy_intake || !["awaiting_selling_points", "drafting"].includes(task.status);
  const available = context.session?.poster_copy_capability;
  el("poster-copy-run").disabled = panel.hidden || task.status !== "awaiting_selling_points" || !available || Boolean(task.agent_session);
  if (!panel.hidden) {
    if (task.copy_intake.state === "rejected") notice("poster-copy-status", task.message, true);
    else if (!available) notice("poster-copy-status", "可在模型设置保存客户云端模型，再确认提炼。");
    else notice("poster-copy-status", available === "customer_cloud_http_mock" ? "兼容接口模拟 · 无云调用" : "");
  }
}

async function startPosterCopy() {
  const current = context.task;
  const button = el("poster-copy-run");
  if (!current || button.disabled || current.token_plan_acceptance?.flow === "new_poster") return;
  button.disabled = true;
  try {
    const settings = context.session.settings;
    const payload = {};
    if (settings.mode === cloudMode) {
      const confirmed = await confirmCloudAction({...settings, posterCopyScope: context.session.poster_copy_capability},
                                                 "poster-copy");
      if (!confirmed || context.task?.id !== current.id) return;
      payload.cloud_consent = settings.revision;
    }
    const response = await request("/api/tasks/" + encodeURIComponent(current.id) + "/execute-copy", payload);
    if (context.task?.id !== current.id) return;
    notice("poster-copy-status");
    adoptTask(response.task);
    pollTask(current.id);
  } catch(error) {
    if (context.task?.id !== current.id) return;
    showPosterCopy(current);
    notice("poster-copy-status", error.message || "提炼没有开始。", true);
  } finally {
    if (context.task?.id === current.id && context.task.status === "awaiting_selling_points") {
      button.disabled = !context.session?.poster_copy_capability;
    }
  }
}

function showPlannedControls(task) {
  const panel = el("planned-panel");
  const isNewPoster = task?.token_plan_acceptance?.flow === "new_poster";
  const active = isNewPoster
    ? task.status === "awaiting_layout_review" && task.token_plan_acceptance.state === "waiting_for_review"
    : ["awaiting_layout_plan", "awaiting_layout_review"].includes(task?.status)
      && (!task.agent_session || task.agent_session.state === "waiting_for_review");
  panel.hidden = !active;
  el("poster-view").classList.toggle("ws-reviewing", active && task.status === "awaiting_layout_review");
  el("planned-manual-input").hidden = isNewPoster;
  el("planned-customer-review").hidden = !isNewPoster;
  if (!active) return;
  const key = task.id + ":" + (task.plan_gate?.plan_sha256 || "");
  if (panel.dataset.binding !== key) {
    panel.dataset.binding = key;
    for (const field of ["copy", "asset", "layout"]) el("planned-check-" + field).checked = false;
  }
  el("planned-approval").hidden = task.status !== "awaiting_layout_review";
  const plan = task.plan_gate?.review_summary;
  const copy = task.confirmed_copy;
  el("planned-readable").textContent = plan || copy ? [
    task.source?.sku ? "商品 " + task.source.sku : task.title,
    copy?.title ? "标题 " + copy.title : plan?.selected_selling_point,
    copy?.subtitle ? "副标题 " + copy.subtitle : null,
  ].filter(Boolean).join(" · ") : "";
  el("planned-technical").hidden = !plan;
  const json = JSON.stringify(plan || {}, null, 2);
  if (el("planned-summary").textContent !== json) el("planned-summary").textContent = json;
}

async function plannedAction(review) {
  const current = context.task;
  const isNewPoster = current?.token_plan_acceptance?.flow === "new_poster";
  const button = el(review ? "planned-render" : "planned-save");
  if (el("planned-panel").hidden || button.disabled || (!review && isNewPoster)) return;
  let body;
  try {
    if (review) {
      body = {binding:current.plan_binding, plan_sha256: current.plan_gate.plan_sha256,
        copy_checked:el("planned-check-copy").checked,
        asset_checked:el("planned-check-asset").checked, plan_checked:el("planned-check-layout").checked};
    } else {
      body = {...JSON.parse(el("planned-submission").value), binding: current.plan_binding};
    }
    el("planned-save").disabled = true;
    el("planned-render").disabled = true;
    const result = await request("/api/tasks/" + encodeURIComponent(current.id) +
      (review ? "/layout-review" : "/layout-plan"), body);
    if (context.task?.id !== current.id) return;
    notice("planned-status");
    adoptTask(result.task);
    pollTask(current.id);
  } catch(error) {
    if (context.task?.id === current.id) notice("planned-status", error.message || "排版计划或审校未提交。", true);
  } finally {
    el("planned-save").disabled = false;
    el("planned-render").disabled = false;
  }
}

function renderResult() {
  const task = context.task;
  const version = currentVersion();
  const versions = orderedVersions(task);
  showPosterCopy(task);
  showPlannedControls(task);
  const mode = task?.run?.simulated === true ? "demo" : task?.run?.mode || task?.run?.provider;
  el("task-summary").hidden = !task;
  if (task) {
    el("result-name").textContent = task.title || "海报任务";
    const kind = task.run?.workflow?.execution_kind;
    const tag = task.token_plan_acceptance?.flow === "new_poster" ? "新商品海报 · 本机排版"
      : kind === "customer_interactive_agent_mock" ? "本机客户交互模拟 · 无真实供应商调用"
      : kind === "customer_cloud_http_mock" ? "本机兼容接口模拟 · 未调用云端"
      : kind === "customer_cloud_skill_projection" ? "客户云模型文案 · 本机卖点规则与排版"
      : kind === "isolated_copy_test" ? "模拟接线 · 未实际调用 Skill"
      : (mode === "reviewed_local_plan" ? (kind === "local_skill_executor" ? "本机 Skill 回执 · 任务排版计划"
        : "已确认文案直用 · 任务排版计划")
      : (mode === "user_copy" ? "已确认文案直用 · 模板预览" : (Object.hasOwn(modeLabels, mode) ? modeLabels[mode] : "")));
    const source = task.source?.kind === "fixture" || task.source?.fixture || task.source?.kind === "synthetic_fixture"
      ? "模拟素材" : "";
    el("result-state").textContent = [sites[task.site], states[task.status] || "等待状态", tag, source].filter(Boolean).join(" · ");
    el("result-message").textContent = detailTaskMessage(task.message);
    el("result-message").hidden = !task.message;
  }
  const img = el("poster-preview");
  const preview = localFile(version?.files?.png);
  const usage = task?.status === "ready" && preview ? usageNotice(mode, task.run?.runtime) : "";
  el("result-usage").textContent = usage;
  el("result-usage").hidden = !usage;
  if (preview && img.getAttribute("src") !== preview) img.setAttribute("src", preview);
  if (!preview) img.removeAttribute("src");
  img.hidden = !preview;
  img.alt = preview ? (task.title || "商品海报") + " v" + version.version : "";
  el("preview-empty").hidden = Boolean(preview);
  el("preview-empty-text").textContent = task && activeStates.has(task.status) ? "正在生成海报" :
    task && ["failed", "interrupted"].includes(task.status) ? "本次任务没有可预览的成品" : "提交商品信息后在这里查看海报";
  el("local-result-status").hidden = !version;
  el("output-tools").hidden = !version;
  if (!version) {
    editClose();
    return;
  }
  setOptions(el("version-select"), versions.map((v) => [String(v.version), "v" + v.version]), version.version);
  resultLink("download-png", version.files?.png, true);
  resultLink("download-psd", version.files?.psd, true);
  resultLink("download-review", version.files?.review, false);
  const editable = editAvailable();
  el("edit-toggle").disabled = !editable;
  el("edit-toggle").textContent = version.version === latestVersion() ?
    (version.editable_objects ? "修改单层" : "修改文字") : "旧版本只读";
  if (!editable) editClose();
}

function adoptTask(task, reset = false) {
  if (!task?.id) return;
  const changed = context.task?.id !== task.id;
  if (!reset && !changed && JSON.stringify(context.task) === JSON.stringify(task)) return;
  if (reset || changed) {
    context.followLatest = true;
    el("planned-submission").value = "";
    notice("planned-status");
    notice("poster-token-plan-error");
    editClose();
    notice("revision-status");
  }
  context.task = task;
  if (context.followLatest || !currentVersion()) context.version = latestVersion();
  storeTask(task);
  renderResult();
  if (context.page === "tasks") renderTaskList();
  document.dispatchEvent(new Event("dt-task"));
}

function stopPolling() {
  clearTimeout(context.pollId);
  context.pollId = 0;
}

function pollTask(id) {
  stopPolling();
  if (context.task?.id !== id || !(activeStates.has(context.task.status) ||
      tokenPlanActiveStates.has(context.task.token_plan_acceptance?.state))) return;
  context.pollId = setTimeout(async () => {
    if (context.task?.id !== id) return;
    try {
      const body = await request("/api/tasks/" + encodeURIComponent(id));
      if (context.task?.id !== id) return;
      adoptTask(body.task);
      context.pollFailures = 0;
      pollTask(id);
    } catch (error) {
      if (context.task?.id !== id) return;
      context.pollFailures += 1;
      if (context.pollFailures <= 3) pollTask(id);
      else notice("poster-status", error.message + " 可以通过任务记录重新打开。", true);
    }
  }, 800);
}

async function openTask(id, navigate, {control, version, returnTo} = {}) {
  if (navigate && context.taskOpen?.id === id) {
    cancelTaskOpen(true);
    return;
  }
  let active;
  if (navigate) {
    cancelTaskOpen();
    active = {id, list: control?.closest("#task-rows, #workspace-recent"), controller: new AbortController()};
    context.taskOpen = active;
    context.navigation += 1;
    clearWorkflowProgress(context.page);
    if (control) markTaskOpenControl(active, control);
    notice("workspace-open-status", "正在打开本机任务…");
  }
  const navigation = context.navigation;
  try {
    const body = await request("/api/tasks/" + encodeURIComponent(id), undefined, active?.controller.signal);
    if (navigation !== context.navigation || active && active !== context.taskOpen) return;
    if (body.task?.id !== id) throw new Error("未找到此任务。");
    const page = pageForTask(body.task);
    if (page === "poster") {
      if (!navigate && context.task) return;
      if (version != null && !orderedVersions(body.task).some(entry => entry.version === version)) {
        throw new Error("原任务的这一版暂时无法读取。请保留当前收件，稍后重试。");
      }
      adoptTask(body.task, true);
      if (version != null) {
        context.version = version;
        context.followLatest = false;
        renderResult();
      }
      notice("poster-status");
      context.pollFailures = 0;
      pollTask(id);
    } else {
      if (!navigate && workflows[page].task) return;
      if (navigate && page === "role") context.artworkReturn = returnTo ? {...returnTo, mall: id} : null;
      showWorkflowTask(page, body.task);
      if (page === "role") {
        el("workspace-return-artwork").hidden = context.artworkReturn?.mall !== id;
        notice("role-download-status");
      }
    }
    if (navigate) {
      cancelTaskOpen();
      setPage(page, true);
    }
  } catch (error) {
    if (navigation !== context.navigation || active && active !== context.taskOpen) return;
    if (active) cancelTaskOpen();
    notice(navigate ? "workspace-open-status" : "poster-status", error.message, true);
  }
}

function updatePosterControl() {
  el("make-poster").disabled = !context.session || context.submitting;
}

async function makePoster(event) {
  event.preventDefault();
  if (context.submitting || !context.session) return;
  if (redesignedModelWorkspace() && window.DTWorkspace && !window.DTWorkspace.canSave()) return;
  const site = el("site").value;
  const title = el("product-title").value.trim();
  const facts = el("product-facts").value.trim();
  if (!title || !facts) {
    notice("poster-status", "请填写商品名称和已核实的商品事实。", true);
    return;
  }
  const file = el("product-file").files[0];
  const fixture = selectedFixture() ? context.fixture : null;
  if (!file && !fixture) {
    notice("poster-status", "请先上传本机商品图片。当前工作台的海报入口没有实际生图能力。", true);
    return;
  }
  if (file && (file.size > 4 * 1024 * 1024 || !/\.(png|jpe?g)$/i.test(file.name))) {
    notice("poster-status", "请选择不大于 4 MB 的 PNG 或 JPG 图片。", true);
    return;
  }
  const confirmedTitle = el("confirmed-poster-title").value;
  const confirmedSubtitle = el("confirmed-poster-subtitle").value;
  const hasCopy = Boolean(confirmedTitle || confirmedSubtitle);
  const productRef = el("poster-product-ref").value.trim();
  const combined = el("poster-combined-detail").checked;
  if (combined && (site !== "CN" || !file || fixture || !productRef || hasCopy)) {
    notice("poster-status", "一起做商品详情页时，请上传本机商品原图、填商品编号，并留空已确认的海报标题和副标题。", true);
    return;
  }
  if (fixture && productRef) {
    notice("poster-status","排版计划使用本机原图。请选择图片或清空商品编号后再运行演示。",true);
    return;
  }
  if (hasCopy && (fixture || !confirmedTitle.trim() || !confirmedSubtitle.trim())) {
    notice("poster-status", "要直接使用文案，请上传商品图并同时填写标题和副标题。", true);
    return;
  }
  context.submitting = true;
  const navigation = context.navigation;
  const revision = context.draftRevision;
  if (context.productSubmission?.revision !== revision) context.productSubmission = {revision, id: requestId()};
  updatePosterControl();
  try {
    const data = { site, title, facts, request_id: context.productSubmission.id };
    const detail = combined ? collectDetailEvidence(el("poster-combined-fields"), true).detail : null;
    notice("poster-status", "正在提交任务…");
    if (fixture) data.fixture = fixture;
    else data.image = { name: file.name, data: await fileData(file) };
    if (hasCopy) data.confirmed_copy = { title: confirmedTitle, subtitle: confirmedSubtitle };
    if (productRef) data.product_ref = productRef;
    if (detail) data.detail = await encodeDetailEvidence(detail);
    const body = await request(fixture ? "/api/tasks" : "/api/tasks/poster", data);
    if (!body.task?.id) throw new Error("工作台没有返回可读取的任务。");
    const clearDraft = context.draftRevision === revision;
    const showSaved = clearDraft && navigation === context.navigation && context.page === "poster";
    if (showSaved) {
      adoptTask(body.task, true);
      notice("poster-status");
      context.pollFailures = 0;
      pollTask(body.task.id);
    } else {
      storeTask(body.task);
      if (context.page === "tasks") renderTaskList();
      document.dispatchEvent(new Event("dt-tasks"));
      notice("workspace-open-status", "商品已保存，可以在任务记录里继续。");
    }
    if (redesignedModelWorkspace()) {
      document.dispatchEvent(new CustomEvent("dt-saved-product", {detail: {clearDraft, showSaved}}));
    }
  } catch (error) {
    notice("poster-status", combined ? detailErrorText(error) : (error.message || "提交失败。"), true);
  } finally {
    context.submitting = false;
    updatePosterControl();
  }
}

function editCopyLimit() {
  const layerId = el("element-select").value.split("/").at(-1);
  const field = layerId === "T01" ? "title" : layerId === "T02" ? "subtitle" : null;
  return field ? context.session?.copy_limits?.[field] : null;
}

function editTextFromSelection() {
  const id = el("element-select").value;
  const layer = currentVersion()?.selected_elements?.find((item) => item.id === id);
  const planned = currentVersion()?.editable_objects?.find((item) => item.id === id);
  el("element-value-label").textContent = planned?.field === "x" ? "新横向位置（像素）" :
    (planned?.field === "fill" ? "新填色（#RRGGBB）" : "新文字");
  el("planned-edit-review-label").hidden = !planned;
  el("planned-edit-reviewed").checked = false;
  el("element-text").value = planned ? String(planned.value) : (layer?.text ?? "");
  const limit = editCopyLimit();
  el("element-text-help").textContent = limit
    ? "最多 " + limit.max_lines + " 行，每行最多 " + limit.max_line_characters + " 个字符。宽字会占用更多空间。"
    : "";
}

async function makeRevision(event) {
  event.preventDefault();
  if (context.editing || !editAvailable()) return;
  const id = context.task.id;
  const base = context.version;
  const text = el("element-text").value.trim();
  if (!text) {
    notice("revision-status", "请输入要写入所选元素的文字。", true);
    return;
  }
  const limit = editCopyLimit();
  const lines = text.split("\n");
  if (limit && (lines.length > limit.max_lines ||
    lines.some((line) => [...line].length > limit.max_line_characters))) {
    notice("revision-status", "文字超过版式的行数或每行字数限制。", true);
    return;
  }
  context.editing = true;
  el("edit-submit").disabled = true;
  notice("revision-status", "正在提交修改…");
  try {
    const element = el("element-select").value;
    const version = currentVersion();
    const planned = version.editable_objects?.find((item) => item.id === element);
    const changes = { base_version: base, element_ref: element, request_id: requestId() };
    if (planned) Object.assign(changes, {task_id:id,site:context.task.site,product_ref:context.task.source.sku,
      layout_sha256:version.plan_release.layout_sha256, value:planned.field === "x" ? Number(text) : text,
      reviewed:el("planned-edit-reviewed").checked});
    else changes.text = text;
    const body = await request("/api/tasks/" + encodeURIComponent(id) + (planned ? "/layout-edit" : "/revise"), changes);
    if (context.task?.id === id) {
      context.followLatest = true;
      adoptTask(body.task);
      editClose();
      notice("revision-status");
      context.pollFailures = 0;
      pollTask(id);
    }
  } catch (error) {
    if (context.task?.id === id && (error.status === 409 || /stale/i.test(error.code || ""))) {
      try {
        const latest = await request("/api/tasks/" + encodeURIComponent(id));
        if (context.task?.id === id) adoptTask(latest.task);
      } catch {}
    }
    if (context.task?.id === id) notice("revision-status", error.message || "修改未完成。", true);
  } finally {
    context.editing = false;
    el("edit-submit").disabled = false;
  }
}

async function saveSettings(event) {
  event.preventDefault();
  if (context.saving || context.testing || !context.session) return;
  const mode = el("model-mode").value;
  const external = mode !== "demo";
  const data = { mode, base_url: external ? el("model-base").value.trim() : "",
    model: external ? el("model-name").value.trim() : "" };
  const password = el("model-key");
  if (mode === cloudMode && !/^https:\/\//i.test(data.base_url)) {
    notice("settings-status", "云端服务地址必须以 https:// 开头。", true);
    return;
  }
  if (external && password.value) data.key = password.value;
  if (external && el("forget-key").checked) data.forget_key = true;
  if (data.key && data.forget_key) {
    notice("settings-status", "请输入新密钥或清除已保存的密钥，两者请选择其一。", true);
    return;
  }
  context.saving = true;
  updateSettingsControls();
  const before = el("settings-form").querySelectorAll("input,select");
  before.forEach((control) => { control.disabled = true; });
  notice("settings-status", "正在保存模型配置…");
  try {
    await request("/api/settings", data);
    context.session = await request("/api/session");
    setSettings(context.session.settings);
    showPosterCopy(context.task);
    notice("settings-status", "设置已保存到本机。");
  } catch (error) {
    notice("settings-status", error.message || "未保存设置。", true);
  } finally {
    before.forEach((control) => { control.disabled = false; });
    password.value = "";
    context.saving = false;
    updateSettingsControls();
  }
}

async function testSettings() {
  if (context.saving || context.testing || !context.session) return;
  if (context.settingsDirty) {
    notice("settings-status", "请先保存修改，再测试已保存的连接。", true);
    return;
  }
  context.testing = true;
  updateSettingsControls();
  try {
    const settings = context.session.settings;
    const payload = {};
    if (settings.mode === cloudMode) {
      notice("settings-status");
      if (!await confirmCloudAction(settings, "test")) return;
      payload.cloud_consent = settings.revision;
    }
    notice("settings-status", "正在测试…");
    const data = (await request("/api/model/test", payload)).connection;
    if (data?.simulated) notice("settings-status", "模拟演示可用。未发出模型请求。");
    else if (data?.ok) {
      const success = modeLabels[settings.mode] + "调用成功" + (data.model ? " · " + data.model : "") + "。";
      notice("settings-status", success + usageNotice(settings.mode, data.runtime));
    }
    else notice("settings-status", "连接测试未通过。", true);
  } catch (error) {
    notice("settings-status", error.message || "连接测试未完成。", true);
  } finally {
    context.testing = false;
    updateSettingsControls();
  }
}

async function initialize() {
  try {
    const body = await request("/api/session");
    context.session = body;
    document.dispatchEvent(new Event("dt-session"));
    updateModeBadge();
    el("profile-name").textContent = typeof body.profile === "string" ? body.profile : "";
    setSettings(body.settings);
    for (const button of document.querySelectorAll("button[data-fixture]")) {
      button.disabled = !body.fixtures?.[button.dataset.fixture];
    }
    notice("session-status");
    updateTranscriptionControls();
    updateRoleControls();
    if (await refreshTasks() && context.jobs.length) {
      const cancelledBeforeModel = task => task.agent_session?.state === "blocked"
        && task.agent_session.reason === "foreground_session_closed" && task.agent_session.model_turns === 0;
      const latestByPage = new Map();
      for (const task of context.jobs) {
        const page = pageForTask(task);
        if (page === "poster" && redesignedModelWorkspace()) continue;
        const current = latestByPage.get(page);
        if (!current || (page === "poster" && cancelledBeforeModel(current) && !cancelledBeforeModel(task))) {
          latestByPage.set(page, task);
        }
      }
      await Promise.all([...latestByPage.values()].map(task => openTask(task.id, false)));
    }
  } catch (error) {
    notice("session-status", error.message || "未能加载本机工作区。", true);
  } finally {
    updateSettingsControls();
    updatePosterControl();
  }
}

for (const item of document.querySelectorAll("button[data-view]")) item.addEventListener("click", () => setPage(item.dataset.view, true));
document.querySelector(".brand").addEventListener("click", event => {
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  setPage("home", true);
});
el("workspace-return-artwork").addEventListener("click", event => {
  const source = context.artworkReturn;
  if (source?.mall === workflows.role.task?.id) openTask(source.id, true, {control: event.currentTarget, version: source.version});
});
el("top-settings").addEventListener("click", () => {
  if ((v2ModelWorkspace() || customerModelWorkspace()) && window.DTWorkspace) {
    window.DTWorkspace.openModels(["poster", "video"].includes(context.page) ? context.page : null);
  } else setPage("models", true);
});
el("refresh-tasks").addEventListener("click", refreshTasks);
for (const button of document.querySelectorAll("button[data-fixture]")) {
  button.addEventListener("click", () => loadFixture(button.dataset.fixture));
}
el("product-file").addEventListener("change", () => {
  if (el("product-file").files.length) context.fixture = null;
  showSource();
});
el("site").addEventListener("change", () => {
  if (context.fixture && el("site").value !== context.fixture) context.fixture = null;
  showSource();
});
detailFields(el("poster-combined-fields"));
el("poster-combined-detail").addEventListener("change", updateCombinedForm);
el("poster-combined-fields").addEventListener("input", updateCombinedForm);
el("poster-combined-fields").addEventListener("change", updateCombinedForm);
el("product-file").addEventListener("change", updateCombinedForm);
el("site").addEventListener("change", updateCombinedForm);
updateCombinedForm();
el("poster-task-detail-form").addEventListener("submit", saveTaskDetail);
el("poster-form").addEventListener("submit", makePoster);
for (const page of Object.keys(workflows)) {
  renderWorkflowTask(page, null);
  const form = el(page + "-form");
  form.addEventListener("submit", (event) => submitWorkflow(event, page));
  for (const name of ["input", "change"]) form.addEventListener(name, () => reviseWorkflowForm(page));
}
el("role-site").addEventListener("change", updateRoleControls);
el("role-tool").addEventListener("change", updateRoleControls);
el("role-example").addEventListener("click", () => {
  const example = roleExample(selectedRoleTool());
  if (!example) return;
  reviseWorkflowForm("role");
  const input = el("role-input");
  if (roleExampleUndo) {
    input.value = roleExampleUndo.text;
    roleExampleUndo = null;
    notice("role-form-status", "已恢复原来的任务材料。");
  } else {
    roleExampleUndo = {text: input.value};
    input.value = typeof example === "string" ? example : JSON.stringify(example, null, 2);
    notice("role-form-status", "示例已填入。可以撤回示例，恢复原来的材料。");
  }
  input.removeAttribute("aria-invalid");
  el("role-example").textContent = roleExampleUndo ? "撤回示例" : "填入示例";
});
el("role-input").addEventListener("input", () => {
  roleExampleUndo = null;
  el("role-example").textContent = "填入示例";
  if (el("role-input").getAttribute("aria-invalid") === "true") notice("role-form-status");
  el("role-input").removeAttribute("aria-invalid");
});
el("settings-form").addEventListener("submit", saveSettings);
el("settings-form").addEventListener("input", () => { context.settingsDirty = true; });
el("model-mode").addEventListener("change", settingsTargetChanged);
el("model-base").addEventListener("input", settingsTargetChanged);
el("test-connection").addEventListener("click", testSettings);
el("version-select").addEventListener("change", () => {
  context.version = Number(el("version-select").value);
  context.followLatest = context.version === latestVersion();
  notice("revision-status");
  editClose();
  renderResult();
});
el("edit-toggle").addEventListener("click", () => {
  if (!editAvailable()) return;
  if (context.isEditing) { editClose(); return; }
  const version = currentVersion();
  const options = (version.editable_objects || version.selected_elements).map((item) => [item.id, item.id]);
  setOptions(el("element-select"), options, options[0]?.[0]);
  editTextFromSelection();
  context.isEditing = true;
  el("edit-form").hidden = false;
  el("edit-toggle").setAttribute("aria-expanded", "true");
  el("element-select").focus();
  notice("revision-status");
});
el("edit-cancel").addEventListener("click", () => { editClose(); el("edit-toggle").focus(); });
el("element-select").addEventListener("change", editTextFromSelection);
el("edit-form").addEventListener("submit", makeRevision);
el("poster-copy-run").addEventListener("click", startPosterCopy);
el("poster-agent-start").addEventListener("click", startCustomerAgent);
el("poster-form-model-access")?.addEventListener("click", (event) => {
  if (event.isTrusted) {
    if (redesignedModelWorkspace() && window.DTWorkspace) window.DTWorkspace.openModels("poster");
    else showModelAccess(null);
  }
});
el("poster-token-plan-start").addEventListener("click", startTokenPlan);
el("poster-token-plan-receipt").addEventListener("click", openTokenPlanReceipt);
el("planned-save").addEventListener("click", () => plannedAction(false));
el("planned-render").addEventListener("click", () => plannedAction(true));
initialize();

