
"use strict";

(() => {
  const capabilityNames = {language: "语言", vision: "视觉理解", image: "图片生成", video: "视频生成"};
  const sources = {official: "官方参考", interface: "接口返回 · 本机替身", manual: "手动填入 · 未验证"};
  const purposes = ["language", "image", "video"];
  const demoScenarios = new Set(["demo-local-ok", "demo-local-empty", "demo-local-denied", "demo-local-failed"]);
  let snapshot = null;
  let returnView = null;
  let pending = false;
  let reconnect = null;
  let draftedProduct = false;
  let sourcePreview = null;

  const customerStates = {
    not_connected: "尚未在本机选定模型。",
    pending: "请切换到刚打开的 Windows 本机连接窗口。",
    ready: "已在本机选定语言模型。当前账号权限未验证，可回原草稿。",
    cancelled: "本机窗口已取消。商品草稿已保留，可重新打开。",
    consumed: "本次凭据已交给已启动的任务。下一次使用请重新连接。",
    expired: "本机连接已到期。请重新打开连接窗口。",
    failed: "本机连接未完成。请确认 Windows 桌面可用后重试。"
  };
  let customerSnapshot = null;
  let customerBusy = false;
  let customerRefresh = null;
  let customerTimer = null;

  function renderCustomerSelection() {
    const providers = customerSnapshot?.providers || [];
    const hasPlan = customerSnapshot?.plan === "token-personal";
    const provider = el("mc-customer-provider");
    const plan = el("mc-customer-plan");
    const state = customerSnapshot?.connection?.state;
    const waiting = state === "pending";
    const selectedProvider = provider.value;
    const selectedPlan = plan.value;
    provider.replaceChildren(new Option("请选择供应商", ""));
    for (const entry of providers) provider.add(new Option(entry.label, entry.id));
    provider.value = providers.some((entry) => entry.id === selectedProvider) ? selectedProvider : "";
    plan.replaceChildren(new Option("请选择账户或套餐", ""));
    if (hasPlan && provider.value) plan.add(new Option("个人 Token Plan", "token-personal"));
    plan.value = selectedPlan || "";
    provider.disabled = providers.length === 0 || customerBusy || waiting;
    plan.disabled = !provider.value || customerBusy || waiting;
    el("mc-customer-connect").disabled = !provider.value || !plan.value || customerBusy || waiting || state === "ready";
    el("mc-customer-disconnect").hidden = state !== "ready";
    el("mc-customer-disconnect").disabled = customerBusy;
    const catalog = provider.value ? (customerSnapshot?.candidates || []).filter((entry) => entry.provider === provider.value) : [];
    el("mc-customer-candidates").replaceChildren(...(catalog.length ? catalog.map((entry) =>
      node("li", (entry.name || entry.id) + (entry.name && entry.name !== entry.id ? "（" + entry.id + "）" : "") +
        (entry.tools === "official_reference" ? " · 工具调用见官方说明" : " · 工具调用待验证")))
      : [node("li", provider.value ? "目前没有可显示的官方参考条目。" : "请选择供应商查看对应条目。")]));
  }

  function renderCustomer(data) {
    if (data?.customer !== true || !Object.hasOwn(customerStates, data.connection?.state)) {
      throw new Error("无法核对本机连接状态。请刷新本机状态后重试。");
    }
    customerSnapshot = data;
    if (data.protocol_fixture && customerModelWorkspace()) el("mode-badge").textContent = "本机协议替身";
    const {state, model_id: model} = data.connection;
    el("mc-customer-status").dataset.state = state;
    notice("mc-customer-status", customerStates[state], state === "failed");
    el("mc-customer-model").textContent = state === "ready" && model
      ? model + " · 本机已选，账号权限未验证" : "尚未选定语言模型";
    renderCustomerSelection();
    updateNeeds();
    if (state === "pending") customerTimer = setTimeout(reloadCustomer, 700);
  }

  function reloadCustomer() {
    if (!customerModelWorkspace() || customerBusy) return;
    if (customerRefresh) return customerRefresh;
    clearTimeout(customerTimer);
    customerRefresh = request("/api/model-workspace").then(renderCustomer).catch(() => {
      customerSnapshot = null;
      el("mc-customer-status").dataset.state = "unavailable";
      notice("mc-customer-status", "无法读取本机连接状态。请点「刷新本机状态」重试。", true);
      renderCustomerSelection();
      updateNeeds();
    }).finally(() => { customerRefresh = null; });
    return customerRefresh;
  }

  async function changeCustomer(action, payload) {
    if (customerBusy) return;
    customerBusy = true;
    clearTimeout(customerTimer);
    renderCustomerSelection();
    let failed = false;
    try {
      if (customerRefresh) await customerRefresh;
      await request("/api/model-workspace/" + action, payload);
    } catch {
      failed = true;
    } finally {
      customerBusy = false;
      renderCustomerSelection();
    }
    await reloadCustomer();
    if (failed && customerSnapshot?.connection?.state !== "pending") {
      notice("mc-customer-status", "未能完成操作。请确认 Windows 本机窗口状态后重试。", true);
    }
  }

  function updateCustomerNeeds() {
    const connection = customerSnapshot?.connection;
    const ready = connection?.state === "ready" && connection?.model_id;
    el("workspace-model-shortcut").textContent = ready ? "已在本机选定语言模型" : "还未在本机选定语言模型";
    el("workspace-language-need").textContent = ready ? connection.model_id + " · 账号权限未验证" : "连接支持工具调用的语言模型后再开始任务";
    const newImage = el("workspace-image-new").checked;
    el("workspace-image-gate").hidden = !newImage;
    el("workspace-image-summary").textContent = newImage ? "新图生成暂不可用" : "可使用现有商品图";
    el("workspace-image-need").textContent = "当前本人模式暂未接入图片生成。可以换用现有商品图，也可以稍后再做。";
    const newVideo = el("workspace-video-new").checked;
    el("workspace-video-local").hidden = newVideo;
    el("workspace-video-cloud").hidden = !newVideo;
    el("workspace-video-need").textContent = "当前本人模式暂未接入视频生成。可用现有视频继续剪辑。";
  }

  el("mc-customer-provider").addEventListener("change", () => {
    el("mc-customer-plan").value = "";
    renderCustomerSelection();
  });
  el("mc-customer-plan").addEventListener("change", renderCustomerSelection);
  el("mc-customer-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!event.isTrusted || !customerModelWorkspace() || el("mc-customer-connect").disabled) return;
    changeCustomer("native-connect", {provider: el("mc-customer-provider").value, plan: el("mc-customer-plan").value});
  });
  el("mc-customer-disconnect").addEventListener("click", (event) => {
    if (event.isTrusted && customerModelWorkspace()) changeCustomer("native-disconnect", {});
  });
  el("mc-customer-refresh").addEventListener("click", reloadCustomer);


  function node(tag, content, className) {
    const out = document.createElement(tag);
    if (content) out.textContent = content;
    if (className) out.className = className;
    return out;
  }

  function control(content, run, className = "button outline") {
    const out = node("button", content, className);
    out.type = "button";
    out.addEventListener("click", run);
    return out;
  }

  function accountConfiguration(account) {
    const provider = snapshot?.providers.find((item) => item.id === account?.provider);
    return {provider, plan: provider?.plans.find((item) => item.id === account?.plan)};
  }

  function activePlan() {
    return accountConfiguration({provider: el("mc-provider").value, plan: el("mc-plan").value}).plan;
  }

  function currentModel(purpose) {
    const saved = snapshot?.assignments[purpose];
    const account = snapshot?.accounts.find((item) => item.id === saved?.account_id);
    const model = account?.models.find((item) => item.id === saved?.model_id);
    return model ? {account, model} : null;
  }

  function providerChange(keepPlan = null) {
    const provider = snapshot?.providers.find((entry) => entry.id === el("mc-provider").value);
    const plans = provider?.plans || [];
    el("mc-plan").replaceChildren(...plans.map((plan) => new Option(plan.label, plan.id)));
    if (plans.some((plan) => plan.id === keepPlan)) el("mc-plan").value = keepPlan;
    planChange();
  }

  function planChange() {
    const plan = activePlan();
    if (!plan) return;
    el("mc-workspace-field").hidden = !plan.needs_workspace;
    el("mc-workspace").required = plan.needs_workspace;
    el("mc-key").value = "";
    let text = "";
    if (plan.source === "official") {
      text = "使用官方套餐目录，当前账号权限未验证。此处无已核实的按 Key 查目录接口。";
    } else if (plan.needs_workspace) {
      text = "北京工作空间的官方接口可返回目录和能力。授权清单需另查，本预览用替身且没有验证真实调用。";
    } else {
      text = "官方接口可返回目录。目录权限、推理权限和费用需分别核实。能力标注还需参考官方模型文档。";
    }
    el("mc-plan-hint").textContent = text + " " + plan.billing + "。本机替身无费用。";
  }

  function assignments() {
    for (const purpose of purposes) {
      const select = el("mc-assign-" + purpose);
      const original = currentModel(purpose);
      const values = [new Option("暂不配置", "")];
      for (const account of snapshot.accounts) {
        for (const model of account.models) {
          if (!model.capabilities.includes(purpose)) continue;
          const entry = new Option(account.label + " · " + model.name, account.id + ":" + model.id);
          if (purpose === "language" && !model.runnable) {
            entry.disabled = true;
            entry.textContent += "（工具调用待验证）";
          }
          values.push(entry);
        }
      }
      select.replaceChildren(...values);
      if (original) select.value = original.account.id + ":" + original.model.id;
      select.disabled = false;
    }
    const language = currentModel("language");
    el("mc-language-note").textContent = language?.model.runnable
      ? "已选本机替身，可在预览中测试工具调用。真实账号未验。"
      : "请选可做本机工具调用预检的语言模型。";
    el("mc-image-note").textContent = currentModel("image") ? "已保存配置。预览没有真实生图适配。" : "生成新图时才用。";
    el("mc-video-note").textContent = currentModel("video") ? "已保存配置。预览没有真实视频生成适配。" : "生成新镜头时才用。";
  }

  function useAccountAgain(account) {
    el("mc-provider").value = account.provider;
    providerChange(account.plan);
    el("mc-workspace").value = account.workspace;
    el("mc-label").value = account.label;
    reconnect = account;
    el("mc-key").focus();
    notice("mc-connect-status", "已填入此账户信息。点击连接会刷新预览目录。");
  }

  function modelDetails(model) {
    const row = node("tr");
    const capabilities = model.capabilities.map((key) => capabilityNames[key]);
    const capability = capabilities.length > 1 ? "多能力 · " + capabilities.join("、") : capabilities[0] || "未知";
    const tool = model.tools === "catalog" ? "有参考支持" : "待验证";
    for (const value of [model.name, capability, sources[model.source], tool]) row.append(node("td", value));
    return row;
  }

  function accountRow(account) {
    const {provider, plan} = accountConfiguration(account);
    const row = node("article", "", "ws-account");
    const heading = node("div", "", "ws-intro-row");
    const info = node("div");
    info.append(node("h4", account.label), node("p", provider.label + " · " + plan.label + " · " + sources[account.source], "muted"));
    const actions = node("div", "", "ws-actions");
    if (account.source === "interface") actions.append(control("重新连接", () => useAccountAgain(account)));
    const remove = control("删除账户", () => {
      if (window.confirm("删除本机账户配置并清除相应用途？")) {
        change("remove", {account_id: account.id}, "mc-connect-status", "账户配置已删除。");
      }
    }, "button neutral");
    remove.dataset.modelRemove = account.id;
    actions.append(remove);
    heading.append(info, actions);
    row.append(heading);
    if (account.status === "mock_empty") {
      row.append(node("p", "接口返回空目录。请检查工作空间或目录权限，也可手动记录一个模型编号。", "ws-help"));
    }
    if (account.models.length) {
      const list = node("details", "", "ws-model-list");
      list.append(node("summary", "查看 " + account.models.length + " 个目录条目"));
      const wrap = node("div", "", "table-wrap");
      const table = node("table");
      const header = node("tr");
      for (const label of ["模型", "能力", "来源", "工具调用"]) {
        const th = node("th", label);
        th.scope = "col";
        header.append(th);
      }
      const head = node("thead");
      head.append(header);
      const body = node("tbody");
      body.append(...account.models.map(modelDetails));
      table.append(head, body);
      wrap.append(table);
      list.append(wrap);
      row.append(list);
    }
    const form = node("form", "", "ws-manual");
    form.dataset.account = account.id;
    const field = node("input");
    field.name = "model_id";
    field.type = "text";
    field.required = true;
    field.maxLength = 128;
    field.placeholder = "手动填写模型编号";
    field.setAttribute("aria-label", account.label + " 的手动模型编号");
    const submit = node("button", "添加未验证模型", "button outline");
    submit.type = "submit";
    submit.dataset.modelManual = "";
    form.append(field, submit);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      change("manual", {account_id: account.id, model_id: field.value.trim()},
        "mc-connect-status", "已记录手动模型。真实能力和调用权限待验证。");
    });
    row.append(form);
    return row;
  }

  function updateSnapshot(data) {
    snapshot = data;
    if (!el("mc-provider").options.length) {
      el("mc-provider").replaceChildren(...snapshot.providers.map((entry) => new Option(entry.label, entry.id)));
      providerChange();
    }
    el("mc-provider").disabled = false;
    el("mc-plan").disabled = false;
    el("mc-account-empty").hidden = snapshot.accounts.length > 0;
    el("mc-accounts").replaceChildren(...snapshot.accounts.map(accountRow));
    assignments();
    updateNeeds();
  }

  async function reloadModels() {
    try {
      updateSnapshot(await request("/api/model-workspace"));
      el("mc-connect").disabled = false;
    } catch (error) {
      notice("mc-connect-status", error.message || "无法读取账户。请确认本机工作台正在运行，再刷新页面。", true);
      updateNeeds();
    }
  }

  const fixes = {
    catalog_denied: "请核对套餐或工作空间的目录读取权限，再点连接。也可以记录模型编号供核查。",
    catalog_failed: "请核对连接参数后重试，已保存的账户仍可查看。",
    catalog_unsupported: "请选择正常替身以查看官方参考目录。",
    invalid_workspace: "请输入北京工作空间编号，例如 llm-demo123。",
    demo_credential_only: "请清空这栏，使用下方的测试替身选项。"
  };

  async function change(action, data, status, message) {
    if (pending) return;
    pending = true;
    el("mc-connect").disabled = true;
    try {
      updateSnapshot(await request("/api/model-workspace/" + action, data));
      notice(status, message);
    } catch (error) {
      notice(status, [error.message, fixes[error.code]].filter(Boolean).join(" "), true);
      if (snapshot && action === "assign") assignments();
    } finally {
      pending = false;
      el("mc-connect").disabled = !snapshot;
    }
  }

  let modelReturnFocus = null;
  function openModels(from) {
    returnView = from || null;
    modelReturnFocus = returnView ? document.activeElement : null;
    el("mc-return").hidden = !returnView;
    el("mc-return").textContent = from === "video" ? "返回视频任务" : "返回原任务";
    setPage("models", true);
  }

  let recentSignature = "";
  function renderRecent() {
    const tasks = context.jobs.slice(0, 4);
    const signature = JSON.stringify(tasks.map(task => [task.id, task.title, pageForTask(task), taskStatus(task)]));
    if (signature === recentSignature) return;
    recentSignature = signature;
    const list = el("workspace-recent");
    el("workspace-empty").hidden = tasks.length > 0;
    list.hidden = tasks.length === 0;
    const rows = tasks.map(task => {
      const item = node("article", "", "ws-task-row");
      const title = node("div");
      title.append(node("strong", task.title || "未命名任务"), node("small", viewLabels[pageForTask(task)], "muted"));
      const state = node("span", taskStatus(task), "ws-chip");
      const open = control(task.status === "ready" ? "查看结果" : "继续处理", event => {
        openTask(task.id, true, {control:event.currentTarget});
      });
      open.dataset.taskId = task.id;
      item.append(title, state, open);
      return item;
    });
    replaceTaskOpenRows(list, rows);
  }

  function renderProductState() {
    const task = context.task;
    const completed = task?.token_plan_acceptance?.state === "completed";
    const taskStatusForStep = ["awaiting_layout_review", "rendering", "ready"].includes(task?.status);
    const step = completed ? 4 : taskStatusForStep ? 3 : 2;
    el("poster-form").hidden = Boolean(task);
    el("poster-view").classList.toggle("ws-saved-product", Boolean(task));
    for (const item of document.querySelectorAll("[data-ws-step]")) {
      const number = Number(item.dataset.wsStep);
      item.classList.toggle("past", number < step);
      if (number === step) item.setAttribute("aria-current", "step");
      else item.removeAttribute("aria-current");
    }
    el("workspace-reset-product").hidden = !task;
    updateDraftActions();
    if (task) {
      el("workspace-product-subtitle").textContent = task.title + " · " + taskStatus(task);
    } else {
      el("workspace-product-subtitle").textContent = "保存商品照片和已确认的信息后，再开始制作。";
    }
  }

  const productDraftKey = "dt-workbench.product-draft.v1";
  let clearedProduct = null;

  function draftFields() {
    const regular = ["site", "product-title", "product-facts", "poster-product-ref", "product-file",
      "confirmed-poster-title", "confirmed-poster-subtitle", "poster-combined-detail",
      "workspace-image-existing", "workspace-image-new"].map(el);
    return regular.concat([...el("poster-combined-fields").querySelectorAll("input[id],select[id],textarea[id]")]);
  }

  function captureProduct() {
    return draftFields().map(field => [field.id, field.type === "file" ? [...field.files]
      : ["checkbox","radio"].includes(field.type) ? field.checked : field.value]);
  }

  function writeProductDraft() {
    try {
      if (!draftedProduct) sessionStorage.removeItem(productDraftKey);
      else {
        const input = captureProduct();
        sessionStorage.setItem(productDraftKey, JSON.stringify({
          fields: input.filter(([,value]) => !Array.isArray(value)),
          missingFiles: input.some(([,value]) => Array.isArray(value) && value.length > 0)
        }));
      }
    } catch {}
  }

  function applyProduct(input) {
    const saved = new Map(input);
    let filesRestored = true;
    for (const field of draftFields()) {
      const value = saved.get(field.id);
      if (field.type === "file" && Array.isArray(value)) {
        filesRestored = setInputFiles(field, value) && filesRestored;
      } else if (["checkbox","radio"].includes(field.type) && typeof value === "boolean") field.checked = value;
      else if (field.type !== "file" && typeof value === "string") field.value = value;
    }
    showSource();
    updateCombinedForm();
    updateLocalPreview();
    return filesRestored;
  }

  function restoreProductDraft() {
    try {
      const raw = sessionStorage.getItem(productDraftKey);
      const saved = raw ? JSON.parse(raw) : null;
      if (!Array.isArray(saved?.fields)) return;
      applyProduct(saved.fields);
      draftedProduct = true;
      notice("workspace-draft-status", saved.missingFiles
        ? "已恢复草稿文字。刷新前未保存的图片请重新选择。"
        : "已恢复本页未保存的草稿。");
    } catch {}
  }

  function resetProductForm() {
    el("poster-form").reset();
    context.fixture = null;
    context.draftRevision += 1;
    draftedProduct = false;
    showSource();
    updateCombinedForm();
    updateLocalPreview();
    writeProductDraft();
  }

  function updateDraftActions() {
    el("workspace-new-product").textContent = draftedProduct ? "继续商品草稿" : "新建商品任务";
    el("workspace-reset-product").textContent = draftedProduct ? "继续未保存草稿" : "新建商品任务";
    el("workspace-clear-product").hidden = !draftedProduct;
    el("workspace-undo-product").hidden = !clearedProduct;
  }

  function newProduct() {
    if (context.submitting) {
      setPage("poster", true);
      notice("poster-status", "正在保存这份商品资料…");
      return;
    }
    stopPolling();
    context.task = null;
    context.version = null;
    if (!draftedProduct && !clearedProduct) resetProductForm();
    renderResult();
    notice("poster-status");
    setPage("poster", true);
    renderProductState();
    updateNeeds();
    el("product-title").focus();
  }

  function updateLocalPreview() {
    const image = el("workspace-source-preview");
    if (sourcePreview) URL.revokeObjectURL(sourcePreview);
    sourcePreview = null;
    const file = el("product-file").files[0];
    if (file && /\.(png|jpe?g)$/i.test(file.name) && file.size <= 4 * 1024 * 1024) {
      sourcePreview = URL.createObjectURL(file);
      image.src = sourcePreview;
      image.hidden = false;
    } else {
      image.removeAttribute("src");
      image.hidden = true;
    }
  }


  const v2Catalogs = {
    not_requested: "尚未获取可用模型。",
    loading: "正在获取此账户的模型目录。",
    listed: "接口已返回目录。目录条目和模型实际调用权限分别确认。",
    empty: "接口返回空名单。检查工作空间或账户，也可手动记录模型编号。",
    denied: "供应商拒绝读取目录。请核对账号、Key 和工作空间权限后重试。",
    unsupported: "此连接方式没有可用的账号目录查询。可查看官方参考或手动记录。",
    failed: "目录获取失败。核对连接参数后可重试。"
  };
  const v2Credentials = {
    ready: "已在本机输入 Key。点击「获取可用模型」才会向供应商查询目录。",
    not_connected: "本机暂无有效 Key。",
    pending: "请在 Windows 本机窗口输入 Key 或取消。",
    cancelled: "已取消本机输入，草稿与已保存的用途仍可查看。",
    failed: "本机输入未完成。请确认 Windows 桌面可用后重试。"
  };
  const v2ConnectionIssues = {
    token_plan_direct_disabled: "此连接方式暂不支持该套餐地址，请核对服务地址和连接方式。",
    catalog_protocol_mismatch: "地址与所选协议不匹配。请按供应商文档核对协议和基础地址。",
    invalid_catalog_endpoint: "无法使用该地址。请填写所选协议的 HTTPS 基础地址，地址里不要包含 /models、参数或凭据。",
    native_window_unavailable: "本机输入窗未能启动。请确认正在运行工作台的 Windows 桌面会话可用，然后重新打开输入窗。",
    native_window_active: "已有一个 Windows 输入窗在等待操作。请先完成或关闭该窗口。",
    desktop_required: "当前 Windows 会话无法显示输入窗。请回到运行工作台的 Windows 桌面，再打开输入窗。",
    connection_expired: "上一次本机连接已过期。请重新打开输入窗，输入 Key 后再点「获取可用模型」。",
    credential_prompt_expired: "本机输入窗等待超时。请重新打开输入窗；账户设置和任务草稿仍可继续使用。",
    worker_closed: "本机连接已关闭。请重新打开输入窗，输入 Key 后再获取模型。",
    native_key_required: "当前没有可用的本机 Key。请重新打开输入窗，输入 Key 后再获取模型。",
    model_workspace_origin: "请回到本机工作台的「模型连接」页面发起操作。",
    credential_launch_timeout: "本机输入窗未能及时启动，已结束等待。请回到运行工作台的 Windows 桌面重新打开输入窗。",
    unsafe_endpoint: "供应商地址未通过安全检查。请核对官方 HTTPS 基础地址。",
    non_public_endpoint: "此地址未解析到公开的供应商服务。请核对官方 HTTPS 基础地址。"
  };
  const v2SubscriptionEndpoint = /(?:token[-_.]?plan|coding[-_.]?plan|claude[-_.]?code|(?:^|[./-])coding(?:[./-]|$))/i;
  const v2Purposes = {language: "语言与工具调用", image: "图片生成", video: "视频生成"};
  let v2Snapshot = null;
  let v2AccountsSignature = "";
  let v2Request = null;
  let v2Timer = null;
  let v2WindowAccountId = null;
  let v2WindowPollUntil = null;
  const v2WindowPollDurationMs = 140_000;

  function v2Method() {
    const provider = v2Snapshot?.providers.find((item) => item.id === el("mc-v2-provider").value);
    return provider?.methods.find((item) => item.id === el("mc-v2-method").value);
  }

  function v2EndpointIssue(method, input) {
    if (!method?.base_required || !input) return null;
    const unchecked = input.replace(/^[a-z][a-z0-9+.-]*:\/\//i, "").split(/[?#]/, 1)[0];
    if (v2SubscriptionEndpoint.test(unchecked)) return "token_plan_direct_disabled";
    let endpoint;
    try {
      endpoint = new URL(input);
    } catch {
      return "invalid_catalog_endpoint";
    }
    const path = endpoint.pathname.replace(/\/+$/, "");
    if (!input.startsWith("https://") || endpoint.port || endpoint.username || endpoint.password ||
        /[?#]/.test(input) || path.endsWith("/models") || !endpoint.hostname.includes(".")) {
      return "invalid_catalog_endpoint";
    }
    const anthropicPath = /\/apps\/anthropic(?:\/v1)?$/i.test(path);
    const openAIPath = /\/compatible-mode\/v1$/i.test(path);
    if ((method.id === "openai-compatible" && (anthropicPath || endpoint.hostname === "api.anthropic.com")) ||
        (method.id === "anthropic-compatible" && (openAIPath || endpoint.hostname === "api.openai.com"))) {
      return "catalog_protocol_mismatch";
    }
    return null;
  }

  function v2BeijingPlan(method, value) {
    const available = v2Snapshot?.providers.find(item => item.id === "bailian")?.methods.find(item => item.id === "personal-token-plan");
    return available?.catalog === "supported" && el("mc-v2-provider").value === "custom" &&
      method?.id === "openai-compatible" && /^https:\/\/token-plan\.cn-beijing\.maas\.aliyuncs\.com\/compatible-mode\/v1\/?$/i.test(value);
  }

  function v2UpdateEndpoint(method) {
    const field = el(method?.workspace_required ? "mc-v2-workspace" : "mc-v2-base-url");
    const value = field.value.trim();
    const reserved = method?.workspace_required && /^(?:token-plan|coding-plan|coding)$/i.test(value);
    const useBeijing = v2BeijingPlan(method, value);
    const issue = reserved ? "token_plan_direct_disabled" : useBeijing ? null : v2EndpointIssue(method, value);
    notice("mc-v2-endpoint-status", issue ? v2ConnectionIssues[issue] : "", Boolean(issue));
    if (issue) field.setAttribute("aria-invalid", "true");
    else field.removeAttribute("aria-invalid");
    el("mc-v2-open").textContent = method?.catalog === "unsupported" ? "记录账户配置" : "输入 API Key";
    el("mc-v2-open").disabled = !method || method.catalog === "blocked" || Boolean(v2Request) || Boolean(issue) ||
      Boolean(v2Snapshot?.accounts.some((account) => account.credential === "pending"));
    return issue;
  }

  function v2SelectSetup() {
    const providerSelect = el("mc-v2-provider");
    const methodSelect = el("mc-v2-method");
    const providers = v2Snapshot?.providers || [];
    const provider = providers.find((item) => item.id === providerSelect.value);
    const methodId = methodSelect.value;
    setOptions(providerSelect, [["", "请选择供应商"], ...providers.map((entry) => [entry.id, entry.label])], provider?.id || "");
    setOptions(methodSelect, [["", "请选择连接方式"], ...(provider?.methods || []).map((entry) => [entry.id, entry.label])],
      provider?.methods.some((entry) => entry.id === methodId) ? methodId : "");
    providerSelect.disabled = !providers.length;
    methodSelect.disabled = !provider;
    const method = v2Method();
    el("mc-v2-base-field").hidden = !method?.base_required;
    el("mc-v2-base-url").required = Boolean(method?.base_required);
    el("mc-v2-workspace-field").hidden = !method?.workspace_required;
    el("mc-v2-workspace").required = Boolean(method?.workspace_required);
    el("mc-v2-method-detail").textContent = method ? [method.protocol && "协议 " + method.protocol, method.endpoint && "目录 " + method.endpoint, method.detail].filter(Boolean).join("。") : "先选择供应商和连接方式。";
    const references = Array.isArray(method?.reference) ? method.reference : [];
    el("mc-v2-reference").hidden = !references.length;
    el("mc-v2-reference-list").replaceChildren(...references.map((entry) => {
      const item = node("li", entry.label || entry.id);
      if (entry.url && entry.url.startsWith("https://")) {
        const link = node("a", entry.label);
        link.href = entry.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        item.replaceChildren(link);
      }
      return item;
    }));
    v2UpdateEndpoint(method);
  }

  function v2OptionKey(account, model) {
    return JSON.stringify([account, model]);
  }

  function v2ModelSource(model) {
    if (model.source === "manual") return "手动填入 · 未验证";
    if (v2Snapshot.fixture) return "接口返回 · 本机替身";
    return model.source === "workspace" ? "接口返回 · 工作空间目录" : "接口返回 · 账户目录";
  }

  function v2Execution(purpose, assignment) {
    if (!assignment) return "尚未选定模型。";
    const state = {credential_required: "已保存。运行前请在本机重新连接账户。",
      catalog_required: "已保存。请重新获取此账户的目录。",
      capability_unverified: "已保存。此模型的能力和调用权限仍需验证。"}[assignment.state];
    if (state) return state;
    if (assignment.execution === "token_plan_visual_ready") return "已保存。可在商品任务中确认使用此套餐，账号调用权限待验证。";
    if (assignment.execution === "token_plan_visual_fixture") return "已保存。可用本机替身预检商品任务。";
    if (assignment.execution === "provider_ready") return "已保存。此用途支持发起请求，账号推理权限待实际确认。";
    if (assignment.execution === "provider_fixture_ready" && v2Snapshot?.fixture) return "已保存。可用本机替身验证此用途。";
    if (assignment.execution === "fixture_ready" && v2Snapshot?.fixture) return "已保存，可在本机测试路由。真实调用未验证。";
    return purpose === "language"
      ? "已保存。工具调用和真实任务执行尚未验证。"
      : "已保存。当前工作台尚未启用此用途的真实调用。";
  }

  function v2Assignments() {
    for (const purpose of purposes) {
      const select = el("mc-v2-assign-" + purpose);
      const assignment = v2Snapshot?.assignments[purpose];
      const groups = [];
      for (const account of v2Snapshot?.accounts || []) {
        const models = account.models.filter((model) => model.capabilities.includes(purpose) || (model.manual_purposes || []).includes(purpose));
        if (!models.length) continue;
        const group = node("optgroup");
        group.label = account.label;
        for (const model of models) {
          const declaration = (model.manual_purposes || []).includes(purpose) && !model.capabilities.includes(purpose) ? " · 用途手填，待验证" : "";
          group.append(new Option(model.id + " · " + v2ModelSource(model) + declaration, v2OptionKey(account.id, model.id)));
        }
        groups.push(group);
      }
      const selected = assignment ? v2OptionKey(assignment.account_id, assignment.model_id) : "";
      select.replaceChildren(new Option("暂不配置", ""), ...groups);
      if (selected && !Array.from(select.options).some((option) => option.value === selected)) {
        const option = new Option(assignment.model_id + " · 已保存，需重新获取目录", selected);
        option.disabled = true;
        select.add(option);
      }
      select.value = selected;
      select.disabled = Boolean(v2Request);
      el("mc-v2-note-" + purpose).textContent = v2Execution(purpose, assignment);
    }
  }

  function v2StatusAction(text, action, payload, account, style) {
    const button = control(text, (event) => {
      if (event.isTrusted) v2Change(action, payload);
    }, style);
    button.dataset.v2Action = action;
    button.dataset.accountId = account.id;
    button.disabled = Boolean(v2Request);
    return button;
  }



  function v2ModelList(account) {
    const details = node("details", "", "ws-model-list");
    details.append(node("summary", "查看 " + account.models.length + " 个模型"));
    const wrap = node("div", "", "table-wrap");
    const table = node("table");
    const head = node("thead");
    const header = node("tr");
    for (const title of ["模型", "已知能力", "来源", "验证状态"]) {
      const cell = node("th", title);
      cell.scope = "col";
      header.append(cell);
    }
    head.append(header);
    const body = node("tbody");
    for (const model of account.models) {
      const capabilities = model.capabilities.map((value) => capabilityNames[value]).filter(Boolean);
      if (model.vision === true && !capabilities.includes("视觉理解")) capabilities.push("视觉理解");
      const classification = capabilities.length > 1 ? "多能力 · " + capabilities.join("、") : capabilities[0] || "能力未知";
      const capabilitySource = {official_reference: "官方参考", provider_metadata: "目录标注", manual: "手动标注"}[model.capability_source];
      const row = node("tr");
      row.append(node("td", model.id), node("td", [classification, capabilitySource].filter(Boolean).join(" · ")),
        node("td", v2ModelSource(model)), node("td", model.capabilities.includes("language") ? "调用权限及工具调用未验证" : "调用权限未验证"));
      body.append(row);
    }
    table.append(head, body);
    wrap.append(table);
    details.append(wrap);
    return details;
  }

  function v2Manual(account) {
    const form = node("form", "", "ws-manual");
    form.dataset.v2Manual = account.id;
    const field = node("input");
    field.name = "model_id";
    field.required = true;
    field.maxLength = 128;
    field.placeholder = "填写模型编号";
    field.setAttribute("aria-label", account.label + " 的模型编号");
    const purpose = node("select");
    purpose.name = "purpose";
    purpose.required = true;
    purpose.setAttribute("aria-label", account.label + " 的模型用途");
    purpose.append(new Option("选择模型用途", ""), ...Object.entries(v2Purposes).map(([key, label]) => new Option(label, key)));
    const submit = node("button", "记录未验证模型", "button outline");
    submit.type = "submit";
    submit.disabled = Boolean(v2Request);
    form.append(field, purpose, submit);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!event.isTrusted || !field.value.trim()) return;
      v2Change("manual", {account_id: account.id, model_id: field.value.trim(), purpose: purpose.value});
    });
    return form;
  }

  function v2WindowMessage(account) {
    if (!account) return "";
    if (v2ConnectionIssues[account.code]) return v2ConnectionIssues[account.code];
    if (account.credential === "pending") {
      return account.window_state === "visible"
        ? "本机窗口已打开。请到 Windows 输入 Key 或取消。"
        : "正在打开 Windows 本机输入窗。";
    }
    return v2Credentials[account.credential] || "";
  }

  function v2WatchCredentialWindow(account) {
    if (!account) return;
    if (account.credential === "pending") {
      v2WindowAccountId = account.id;
      notice("mc-v2-status", v2WindowMessage(account));
    } else if (v2WindowAccountId === account.id) {
      v2WindowAccountId = null;
      notice("mc-v2-status", v2WindowMessage(account), account.credential === "failed");
    }
  }

  function v2Account(account) {
    const provider = v2Snapshot.providers.find((item) => item.id === account.provider);
    const method = provider?.methods.find((item) => item.id === account.method);
    const row = node("article", "", "ws-account");
    row.dataset.v2Account = account.id;
    row.dataset.catalogState = account.catalog_state;
    row.dataset.credentialState = account.credential;
    if (account.window_state) row.dataset.windowState = account.window_state;
    const heading = node("div", "", "ws-intro-row");
    const meta = node("div");
    meta.append(node("h4", account.label), node("p", [provider?.label, method?.label, method?.protocol, account.workspace_id, account.base_url].filter(Boolean).join(" · "), "muted"));
    const actions = node("div", "", "ws-actions");
    if (method?.catalog === "supported") {
      if (account.credential === "ready") {
        actions.append(v2StatusAction("获取可用模型", "catalog", {account_id: account.id}, account, "button primary"));
      } else if (account.credential !== "pending") {
        actions.append(v2StatusAction("重新输入 Key", "open", {account_id: account.id}, account, "button outline"));
      }
      if (account.credential === "ready") {
        actions.append(v2StatusAction("移除本次凭据", "close", {account_id: account.id}, account, "button outline"));
      }
    }
    const remove = control("删除账户", (event) => {
      if (event.isTrusted && window.confirm("删除此本机账户，并清除相应用途？")) v2Change("remove", {account_id: account.id});
    }, "button neutral");
    remove.dataset.v2Action = "remove";
    remove.dataset.accountId = account.id;
    remove.disabled = Boolean(v2Request) || account.credential === "pending";
    actions.append(remove);
    heading.append(meta, actions);
    row.append(heading);
    if (method?.catalog === "supported") {
      const credential = v2Snapshot?.fixture && account.credential === "ready" ? "本机替身已就绪（未输入 Key）。"
        : v2WindowMessage(account) || "请重新输入 Key。";
      row.append(node("p", credential, "muted"));
    }
    const catalogStatus = account.catalog_state === "listed" && account.catalog_source === "workspace"
      ? "接口已返回工作空间目录。模型调用权限仍需单独验证。"
      : v2ConnectionIssues[account.code] ? "" : v2Catalogs[account.catalog_state] || "请刷新本机状态。";
    if (catalogStatus) row.append(node("p", catalogStatus, account.catalog_state === "denied" ? "notice error" : "ws-help"));
    if (account.models.length) row.append(v2ModelList(account));
    if (method && method.catalog !== "blocked" && account.credential !== "pending") {
      row.append(v2Manual(account));
    }
    return row;
  }

  function v2Render() {
    v2SelectSetup();
    const active = document.activeElement;
    const activeAction = active?.dataset?.v2Action;
    const activeAccount = active?.dataset?.accountId;
    const accounts = el("mc-v2-accounts");
    el("mc-v2-empty").hidden = Boolean(v2Snapshot?.accounts.length);
    const accountList = v2Snapshot?.accounts || [];
    const signature = JSON.stringify(accountList);
    if (signature !== v2AccountsSignature) {
      v2AccountsSignature = signature;
      const previous = new Map(Array.from(accounts.children, (row) => [row.dataset.v2Account, row]));
      const activeForm = active?.closest?.("[data-v2-manual]");
      accounts.replaceChildren(...accountList.map((account) => {
        const old = previous.get(account.id);
        const row = v2Account(account);
        const oldForm = old?.querySelector("[data-v2-manual]");
        const newForm = row.querySelector("[data-v2-manual]");
        if (oldForm && newForm) {
          newForm.elements.model_id.value = oldForm.elements.model_id.value;
          newForm.elements.purpose.value = oldForm.elements.purpose.value;
        }
        if (old?.querySelector("details[open]") && row.querySelector("details")) row.querySelector("details").open = true;
        return row;
      }));
      if (activeAction && activeAccount) {
        [...accounts.querySelectorAll("[data-v2-action]")].find((button) =>
          button.dataset.accountId === activeAccount && button.dataset.v2Action === activeAction)?.focus();
      } else if (activeForm && active.name) {
        [...accounts.querySelectorAll("[data-v2-manual]")].find((form) =>
          form.dataset.v2Manual === activeForm.dataset.v2Manual)?.elements.namedItem(active.name)?.focus();
      }
    }
    v2Assignments();
    updateNeeds();
  }

  function v2SetSnapshot(payload) {
    if (payload?.workspace !== "provider-catalog-v2") throw new Error("unexpected_snapshot");
    v2Snapshot = payload;
  }

  async function v2Change(action = "", payload) {
    if (!v2ModelWorkspace() || v2Request) return;
    clearTimeout(v2Timer);
    const target = action === "assign" ? "mc-v2-assign-status" : "mc-v2-status";
    if (action === "catalog") notice(target, v2Snapshot?.fixture
      ? "正在从本机替身获取目录。"
      : "正在向所选供应商获取目录。此操作不发起推理。");
    v2Request = request("/api/model-workspace" + (action ? "/" + action : ""), payload);
    el("mc-v2-open").disabled = true;
    el("mc-v2-refresh").disabled = true;
    try {
      v2SetSnapshot(await v2Request);
      const manualSetup = action === "open" && v2Method()?.catalog === "unsupported" && !payload?.account_id;
      const messages = {open: manualSetup
        ? "已保存账户信息。此方式没有已验证的账号目录查询，可在账户下手动填写准确模型 ID。"
        : "请查看下方账户的本机输入状态；完成后点获取目录。",
        catalog: "目录操作结束。请查看对应账户的结果。", manual: "已记录模型，能力和调用权限仍需验证。",
        assign: payload?.model_id ? "已保存此用途。" : "此用途已清除。",
        close: "本次本机凭据已移除。", remove: "本机账户已删除。"};
      if (action) notice(target, messages[action]);
      if (action === "open" && !manualSetup) {
        v2WatchCredentialWindow(v2Snapshot.accounts.find((account) => account.credential === "pending"));
      } else if (!action && v2WindowAccountId) {
        v2WatchCredentialWindow(v2Snapshot.accounts.find((account) => account.id === v2WindowAccountId));
      }
    } catch (error) {
      const fixes = {catalog_denied: "没有目录读取权限。请核对账号、Key 和工作空间。",
        catalog_unsupported: "该连接方式未支持按账号读取目录。", catalog_failed: "目录查询失败，请核对工作空间或重新输入 Key。",
        invalid_workspace: "工作空间编号无效，请核对后重试。", model_workspace_blocked: "该方式暂不可通过 D&T 接入。",
        LOCAL_UNREACHABLE: "无法连接本机工作台，请刷新本机状态后重试。"};
      notice(target, v2ConnectionIssues[error.code] || fixes[error.code] ||
        "这项操作没有完成，请刷新状态后重试。", true);
      if (action === "open" && !payload?.account_id &&
          ["token_plan_direct_disabled", "catalog_protocol_mismatch", "invalid_catalog_endpoint"].includes(error.code)) {
        el(v2Method()?.base_required ? "mc-v2-base-url" : v2Method()?.workspace_required ? "mc-v2-workspace" : "mc-v2-method").focus();
      }
    } finally {
      v2Request = null;
      el("mc-v2-refresh").disabled = false;
      v2Render();
    }
    const pendingWindow = v2Snapshot?.accounts.some((account) => account.credential === "pending");
    if (pendingWindow) {
      if (v2WindowPollUntil === null) v2WindowPollUntil = Date.now() + v2WindowPollDurationMs;
    } else {
      v2WindowPollUntil = null;
    }
    const catalogLoading = v2Snapshot?.accounts.some((account) => account.catalog_state === "loading");
    if (catalogLoading || (pendingWindow && Date.now() < v2WindowPollUntil)) {
      v2Timer = setTimeout(v2Change, 800);
    } else if (pendingWindow && v2WindowAccountId) {
      notice("mc-v2-status", "输入窗状态尚未更新。请检查 Windows 输入窗，再点「刷新本机状态」。", true);
    }
  }

  function closeCatalogueDetails() {
    for (const details of el("mc-v2-accounts").querySelectorAll("details[open]")) details.open = false;
  }

  el("mc-v2-provider").addEventListener("change", () => {
    closeCatalogueDetails();
    el("mc-v2-method").value = "";
    el("mc-v2-label").value = "";
    el("mc-v2-base-url").value = "";
    el("mc-v2-workspace").value = "";
    notice("mc-v2-status");
    v2SelectSetup();
  });
  el("mc-v2-method").addEventListener("change", () => {
    closeCatalogueDetails();
    el("mc-v2-base-url").value = "";
    el("mc-v2-workspace").value = "";
    notice("mc-v2-status");
    v2SelectSetup();
  });
  el("mc-v2-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const method = v2Method();
    if (!event.isTrusted || !method || method.catalog === "blocked" || v2UpdateEndpoint(method)) return;
    const base = el("mc-v2-base-url").value.trim();
    const connection = v2BeijingPlan(method, base) ? {provider: "bailian", method: "personal-token-plan"} : {
      provider: el("mc-v2-provider").value,
      method: method.id,
      ...(method.workspace_required ? {workspace_id: el("mc-v2-workspace").value.trim()} : {}),
      ...(method.base_required ? {base_url: base} : {})
    };
    v2Change("open", {...connection, label: el("mc-v2-label").value.trim()});
  });
  el("mc-v2-base-url").addEventListener("input", () => {
    closeCatalogueDetails();
    notice("mc-v2-status");
    v2UpdateEndpoint(v2Method());
  });
  el("mc-v2-workspace").addEventListener("input", () => v2UpdateEndpoint(v2Method()));
  el("mc-v2-refresh").addEventListener("click", () => v2Change());
  for (const purpose of purposes) {
    el("mc-v2-assign-" + purpose).addEventListener("change", (event) => {
      const [account_id, model_id] = event.target.value ? JSON.parse(event.target.value) : [null, null];
      v2Change("assign", {purpose, account_id, model_id});
    });
  }




  const taskOperationCopy = {
    language: {name: "语言模型", start: "用语言模型处理商品", download: "下载返回文本"},
    image: {name: "图片生成模型", start: "生成新图片", prompt: "你想生成什么图片？", download: "下载生成图片"},
    video: {name: "视频生成模型", start: "生成新镜头", prompt: "你想生成什么镜头？", download: "下载生成视频"}
  };
  const taskOperationStates = {
    submitting: "正在提交，请勿重复操作", submitted: "已提交，可查询进度",
    pending: "供应商处理中", succeeded: "已返回结果", failed: "这次操作未完成",
    unknown: "提交结果不明，系统已停止，未重新提交"
  };
  const taskOperationOpen = new Set(["submitting", "submitted", "pending", "unknown"]);
  const taskOperationSupported = new Set(["provider_ready", "provider_fixture_ready"]);
  const taskOperationActions = {};
  let taskOperationList = null;
  let taskOperationTaskId = null;
  let taskOperationBusy = false;
  let taskOperationApproval = null;
  let ordinaryChainApproval = null;
  let ordinaryChainStarting = false;

  function ordinaryChainEligible(task) {
    const status = task?.token_plan_acceptance;
    return context.session?.ordinary_visual_chain === true && task?.site === "CN" &&
      status?.flow === "new_poster" && status.detail?.requested === true;
  }

  function ordinaryChainSelection(task) {
    const picked = v2Snapshot?.assignments.language;
    const executions = v2Snapshot?.fixture ? ["provider_fixture_ready", "token_plan_visual_fixture"] : ["provider_ready", "token_plan_visual_ready"];
    if (!ordinaryChainEligible(task) || !v2ModelWorkspace() ||
        !executions.includes(picked?.execution) || context.page !== "poster" ||
        taskOperationList?.task_id !== task?.id || !Number.isInteger(taskOperationList?.version) ||
        !/^[0-9a-f]{64}$/.test(taskOperationList?.revision || "")) return null;
    return picked;
  }

  function showOrdinaryVisualChain(task) {
    if (context.session?.ordinary_visual_chain !== true) return false;
    const status = task?.token_plan_acceptance;
    const details = status?.detail;
    const eligible = ordinaryChainEligible(task);
    const state = status?.state;
    const active = ["starting", "drafting", "waiting_for_review", "receiving"].includes(state);
    const picked = eligible ? ordinaryChainSelection(task) : null;
    const draft = task?.status === "awaiting_selling_points" && !task?.confirmed_copy && !task?.versions?.length;
    const busy = ordinaryChainStarting || taskOperationBusy || context.detailSaving;
    const note = el("poster-token-plan-detail");
    const button = el("poster-token-plan-start");
    const receipt = el("poster-token-plan-receipt");
    el("poster-token-plan-status").hidden = !eligible;
    el("poster-customer-consent-note").hidden = true;
    el("poster-token-plan-old-notice").hidden = true;
    el("poster-token-plan-new-notice").hidden = true;
    const scope = el("poster-token-plan-new-scope");
    scope.hidden = !eligible;
    scope.textContent = "制作会复用已保存的商品事实和图片。海报出图前需要你确认排版。";
    el("poster-token-plan-launch").hidden = !eligible || active || state === "completed";
    receipt.hidden = state !== "completed" || !/^[0-9a-f]{32}$/.test(status.mall_task_id || "");
    button.textContent = "开始制作商品三件套";
    button.disabled = Boolean(!status?.can_start || !details?.ready || !draft || !picked || busy);
    note.textContent = ({
      starting: "正在启动商品三件套任务。",
      drafting: "所选语言模型正在处理卖点和排版。",
      waiting_for_review: "排版已准备好。请核对文案、商品图片和版面，再确认渲染。",
      receiving: "正在生成主图和详情页，并由商城接收同版文件。",
      completed: v2Snapshot?.fixture
        ? "本机预检已完成，可打开商城同版收件核对文件。"
        : "商城已接收同版文件，可打开收件核对三件套和可编辑 PSD。"
    })[state] || (!details?.ready
      ? "请先补齐商品详情页资料。"
      : state === "blocked" && !draft
        ? "这次制作未完成。请保留此任务的回执，再用新草稿继续。"
        : !draft
          ? "当前任务已有确认文案或成品。继续使用下方的原稿和取件入口。"
          : !picked
            ? "请先在模型连接选择已接通且支持工具调用的语言模型，再刷新任务执行记录。"
            : state === "blocked"
              ? "上次制作未完成。请核对当前草稿和模型连接后重试。"
              : "使用已选语言模型，把本机商品图片做成主图、海报和详情页。");
    return true;
  }

  async function openOrdinaryVisualChain(task) {
    const dialog = el("workspace-task-ordinary-chain-confirm");
    const picked = ordinaryChainSelection(task);
    if (!picked || !task || dialog.open || el("poster-token-plan-start").disabled) return;
    ordinaryChainStarting = true;
    notice("poster-token-plan-error");
    showOrdinaryVisualChain(task);
    try {
      const snapshot = await request(taskOperationPath(task.id));
      if (context.page !== "poster" || context.task !== task) return;
      if (snapshot?.task_id !== task.id || !Array.isArray(snapshot.operations)) throw new Error("task_mismatch");
      taskOperationList = snapshot;
      const selection = ordinaryChainSelection(task);
      if (selection?.account_id !== picked.account_id || selection?.model_id !== picked.model_id) {
        notice("poster-token-plan-error", "语言模型选择已变更。请核对模型连接后重新打开确认窗。", true);
        return;
      }
      ordinaryChainApproval = {
        taskId: task.id, account_id: selection.account_id, model_id: selection.model_id,
        task_version: snapshot.version, revision: snapshot.revision, execution: selection.execution
      };
      el("workspace-task-ordinary-chain-target").textContent =
        [selection.label || "当前测试账户", selection.model_id, "任务版本 " + snapshot.version].join(" · ");
      const fixture = v2Snapshot.fixture;
      const tokenPlan = selection.provider === "bailian" && selection.method === "personal-token-plan";
      el("workspace-task-ordinary-chain-scope").textContent = fixture
        ? "本机预检使用测试接口。商品照片与导出文件留在本机。请核对排版后确认出图，再由商城接收同版。"
        : "会把商品名称、已保存的商品事实和选用的详情文字、任务与版本标识、卖点 Skill 的工具指令和核对结果摘要发送给所选供应商。图片、PSD 和其他导出文件留在本机。";
      el("workspace-task-ordinary-chain-cost").textContent = fixture
        ? "本次预检不调用真实供应商，不产生模型费用。"
        : tokenPlan
          ? "本次最多发起 7 次语言模型请求，按你所选的北京 Token Plan 套餐结算。海报出图前需要你确认排版。"
          : "这次制作最多发起 7 次语言模型请求，费用按你所选账号的规则结算。海报出图前需要你确认排版。";
      dialog.returnValue = "";
      dialog.showModal();
    } catch {
      if (context.task?.id === task.id) {
        notice("poster-token-plan-error", "暂时读不到任务版本。请刷新执行记录后重试。", true);
      }
    } finally {
      ordinaryChainStarting = false;
      showOrdinaryVisualChain(context.task);
    }
  }

  async function runOrdinaryVisualChain(approval) {
    const {taskId, account_id, model_id, task_version, revision, execution} = approval;
    const task = context.task;
    const picked = ordinaryChainSelection(task);
    if (ordinaryChainStarting || taskOperationBusy || task?.id !== taskId) return;
    if (picked?.account_id !== account_id || picked?.model_id !== model_id || picked?.execution !== execution ||
        taskOperationList?.version !== task_version || taskOperationList?.revision !== revision) {
      notice("poster-token-plan-error", "任务或模型选择已经变更，请刷新执行记录后重新确认。", true);
      return;
    }
    ordinaryChainStarting = true;
    notice("poster-token-plan-error");
    showOrdinaryVisualChain(task);
    const base = "/api/tasks/" + encodeURIComponent(taskId);
    try {
      const response = await request(base + "/ordinary-visual-chain",
        {approved: true, account_id, model_id, task_version, revision});
      if (context.task?.id !== taskId) return;
      if (response?.task?.id !== taskId) throw new Error("task_mismatch");
      adoptTask(response.task);
      context.pollFailures = 0;
      pollTask(taskId);
    } catch (error) {
      if (context.task?.id !== taskId) return;
      try {
        const {task: latest} = await request(base);
        if (latest?.id === taskId && context.task?.id === taskId) {
          adoptTask(latest);
          context.pollFailures = 0;
          pollTask(taskId);
        }
      } catch {}
      if (context.task?.id === taskId) {
        const guidance = {
          task_changed: "任务已经更新，请刷新执行记录并核对当前输入。",
          selection_changed: "语言模型选择已经变更，请到模型连接核对后重新确认。",
          visual_material_missing: "缺少商品详情页资料。请补齐页面列出的信息后重试。",
          account_busy: "当前账户正在处理另一个操作，请等该操作结束后再尝试。"
        };
        notice("poster-token-plan-error", guidance[error.code] ||
          "本次启动未得到可验证回执。请从任务记录查看当前状态，确认未启动后再重试。", true);
        loadTaskOperations(context.task, true);
      }
    } finally {
      ordinaryChainStarting = false;
      showOrdinaryVisualChain(context.task);
    }
  }

  function taskOperationPath(taskId, operationId = "") {
    return "/api/tasks/" + encodeURIComponent(taskId) + "/provider-operations" +
      (operationId ? "/" + encodeURIComponent(operationId) : "");
  }

  function drawTaskOperationControls() {
    const task = context.page === "poster" ? context.task : null;
    const versionText = el("workspace-task-operation-version");
    const matching = task && taskOperationList?.task_id === task.id;
    versionText.textContent = matching ? "当前任务版本：" + taskOperationList.version : "";
    for (const purpose of purposes) {
      const ui = taskOperationActions[purpose];
      const assignment = v2Snapshot?.assignments[purpose];
      ui.model.textContent = v2TaskLabel(purpose);
      const useExistingCopy = purpose === "language" && Boolean(task?.confirmed_copy);
      const useTriad = purpose === "language" && ordinaryChainEligible(task);
      const operations = matching ? taskOperationList.operations.filter((item) => item.purpose === purpose) : [];
      const unresolved = operations.find((item) => taskOperationOpen.has(item.state));
      const canRun = !useExistingCopy && !useTriad && assignment && taskOperationSupported.has(assignment.execution);
      ui.scope.textContent = useExistingCopy
        ? "此任务沿用你已确认的文案。"
        : useTriad
        ? "从本页的「开始制作商品三件套」运行正式卖点 Skill 与制作流程。"
        : purpose === "language"
        ? "向所选模型发送和分析已保存的商品名称和执行所需的公开信息。"
        : "仅发送下面填写的生成描述。生成文件可单独取回，原商品图和草稿会保留。";
      ui.inputField.hidden = !ui.prompt || !canRun || Boolean(unresolved);
      ui.start.hidden = !canRun || Boolean(unresolved);
      ui.start.disabled = taskOperationBusy || !matching;
      ui.results.hidden = !operations.length;
      ui.results.replaceChildren(...operations.map((operation) => taskOperationRow(task, operation, purpose)));
    }
    el("workspace-task-operations-refresh").disabled = taskOperationBusy;
    if (context.page === "poster") showOrdinaryVisualChain(task);
  }

  function taskOperationRow(task, operation, purpose) {
    const item = node("li");
    const status = taskOperationStates[operation.state] || "暂时无法读取状态";
    item.append(node("p", [status, operation.model_id, operation.task_version && "版本 " + operation.task_version].filter(Boolean).join(" · ")));
    if (operation.state === "unknown" && !operation.job_id) {
      item.append(node("p", "供应商未确认接单。请先在供应商处核对这次请求，再决定下一步。", "muted"));
    }
    if (operation.state === "failed" && operation.error) {
      item.append(node("p", "可核对账户、模型权限及输入内容。", "muted"));
    }
    if (purpose === "video" && operation.job_id && ["submitted", "pending"].includes(operation.state)) {
      if (operation.error) {
        item.append(node("p", "暂时未能从供应商取到结果。可用原账户继续查询这条视频。", "muted"));
      }
      const poll = control("查询这条视频的进度", (event) => {
        if (event.isTrusted) askTaskOperation(purpose, operation);
      });
      poll.id = "workspace-task-poll-video";
      poll.disabled = taskOperationBusy;
      item.append(poll);
    }
    const output = taskOperationPath(task.id, operation.id) + "/output";
    if (operation.state === "succeeded" && operation.output_href === output) {
      const link = node("a", taskOperationCopy[purpose].download, "button outline");
      link.href = output;
      link.dataset.workspaceTaskDownload = purpose;
      link.download = "";
      item.append(link);
    }
    if (operation.state === "succeeded" && typeof operation.preview === "string" && operation.preview) {
      item.append(node("p", operation.preview, "muted"));
    }
    return item;
  }

  async function loadTaskOperations(task, quiet = false) {
    if (!task || taskOperationBusy) return;
    taskOperationBusy = true;
    drawTaskOperationControls();
    if (!quiet) notice("workspace-task-operation-status", "正在刷新本机回执…");
    try {
      const response = await request(taskOperationPath(task.id));
      if (taskOperationTaskId !== task.id) return;
      if (response?.task_id !== task.id || !Array.isArray(response.operations)) throw new Error("task_mismatch");
      taskOperationList = response;
      notice("workspace-task-operation-status");
    } catch {
      if (taskOperationTaskId === task.id) {
        notice("workspace-task-operation-status", "未能读取本机回执。刷新后再检查，避免重复提交。", true);
      }
    } finally {
      taskOperationBusy = false;
      drawTaskOperationControls();
      if (taskOperationTaskId && taskOperationTaskId !== task.id) loadTaskOperations(context.task, true);
    }
  }

  function askTaskOperation(purpose, operation = null) {
    const task = context.task;
    const assignment = v2Snapshot?.assignments[purpose];
    const dialog = el("workspace-task-provider-confirm");
    if (!task || context.page !== "poster" || taskOperationBusy || dialog.open ||
        taskOperationList?.task_id !== task.id) return;
    if (!operation && !taskOperationSupported.has(assignment?.execution)) return;
    if (purpose === "language" && (task.confirmed_copy || ordinaryChainEligible(task))) return;
    const ui = taskOperationActions[purpose];
    if (!operation && ui.prompt && !ui.prompt.value.trim()) {
      notice("workspace-task-operation-status", "请先写下希望模型生成的内容。", true);
      ui.prompt.focus();
      return;
    }
    const text = ui.prompt?.value.trim() || "";
    const account = operation
      ? v2Snapshot?.accounts?.find((entry) => entry.id === operation.account_id)?.label || "原提交账户"
      : assignment.label || "所选账户";
    const model = operation?.model_id || assignment.model_id;
    const fixture = Boolean(v2Snapshot?.fixture);
    const data = operation ? "这次只发送该视频生成的查询标识。"
      : purpose === "language" ? "将发送此任务已保存的商品文字及完成当前步骤所需的工具消息。原图和成品留在本机。"
      : "将发送你填写的生成描述。原图、其他素材和草稿留在本机。";
    el("workspace-task-provider-confirm-title").textContent = operation ? "确认查询视频进度" : "确认调用所选模型";
    el("workspace-task-provider-confirm-details").textContent =
      account + " · " + model + " · " + taskOperationCopy[purpose].name + "。" + data;
    el("workspace-task-provider-confirm-charges").textContent = fixture
      ? "此实例只使用本机测试接口，无真实供应商请求或费用。"
      : "费用按该服务商账户规则计算。本次使用你选定的模型。";
    el("workspace-task-provider-approve").textContent = operation ? "确认查询" : "确认开始";
    taskOperationApproval = {
      taskId: task.id, version: taskOperationList.version, revision: taskOperationList.revision,
      purpose, operation, account_id: operation?.account_id || assignment.account_id, model_id: model, prompt: text
    };
    dialog.returnValue = "";
    dialog.showModal();
  }

  async function runTaskOperation(approval) {
    const {taskId, purpose, operation, account_id, model_id, prompt} = approval;
    const selection = v2Snapshot?.assignments[purpose];
    if (taskOperationBusy || context.page !== "poster" || context.task?.id !== taskId ||
        taskOperationList?.version !== approval.version || taskOperationList?.revision !== approval.revision ||
        (!operation && (selection?.account_id !== account_id || selection?.model_id !== model_id ||
          !taskOperationSupported.has(selection?.execution)))) return;
    taskOperationBusy = true;
    drawTaskOperationControls();
    const path = taskOperationPath(taskId, operation?.id) + (operation ? "/poll" : "");
    notice("workspace-task-operation-status", operation ? "正在查询这条视频的进度…" : "正在请求所选模型…");
    try {
      const response = await request(path, operation ? {approved: true}
        : {purpose, account_id, model_id, prompt, approved: true, task_version: approval.version, revision: approval.revision});
      const result = response?.operation;
      if (!result || result.purpose !== purpose || result.account_id !== account_id || result.model_id !== model_id ||
          (operation && result.id !== operation.id)) throw new Error("operation_mismatch");
      if (taskOperationTaskId === taskId) {
        const previous = taskOperationList.operations.filter((entry) => entry.id !== result.id);
        taskOperationList.operations = [result, ...previous];
        notice("workspace-task-operation-status");
        if (purpose === "language" && result.state === "succeeded") {
          refreshProductAfterLanguage(taskId);
        }
      }
    } catch (error) {
      if (taskOperationTaskId === taskId) {
        const guidance = {purpose_unavailable: "所选模型暂时不能用于这一步。请查看模型连接状态。",
          provider_operations_blocked: "该连接方式不能从 D&T 发起调用。请查看模型连接状态。",
          provider_operation_in_progress: "已有一条请求需要核对。请刷新本机回执。",
          LOCAL_UNREACHABLE: "暂未收到本机响应。请先刷新本机回执，核对这次请求后再继续。"};
        notice("workspace-task-operation-status", guidance[error.code] ||
          "这次操作未得到可核对的结果。请刷新本机回执以确认，避免重复提交。", true);
        taskOperationList = null;
      }
    } finally {
      taskOperationBusy = false;
      drawTaskOperationControls();
      if (taskOperationTaskId && taskOperationTaskId !== taskId) loadTaskOperations(context.task, true);
    }
  }

  async function refreshProductAfterLanguage(taskId) {
    try {
      const response = await request("/api/tasks/" + encodeURIComponent(taskId));
      if (response?.task?.id !== taskId || context.task?.id !== taskId) return;
      adoptTask(response.task);
      if (context.page === "poster") loadTaskOperations(context.task, true);
    } catch {
      // The saved operation receipt remains readable. Reopening the task refreshes product data.
    }
  }

  function initTaskOperations() {
    const cards = el("workspace-task-operation-cards");
    for (const purpose of purposes) {
      const copy = taskOperationCopy[purpose];
      const section = node("section", "", "field");
      const title = node("h4", copy.name);
      const model = node("p", "", "muted");
      const scope = node("p", "", "muted");
      const inputField = node("div", "", "field");
      let prompt = null;
      if (copy.prompt) {
        prompt = node("textarea");
        prompt.id = "workspace-task-prompt-" + purpose;
        prompt.maxLength = 2000;
        prompt.rows = 2;
        const label = node("label", copy.prompt);
        label.htmlFor = prompt.id;
        inputField.append(label, prompt);
      }
      const start = control(copy.start, (event) => {
        if (event.isTrusted) askTaskOperation(purpose);
      });
      start.id = "workspace-task-start-" + purpose;
      const results = node("ul");
      results.id = "workspace-task-result-" + purpose;
      results.setAttribute("aria-live", "polite");
      section.append(title, model, scope, inputField, start, results);
      cards.append(section);
      taskOperationActions[purpose] = {model, scope, inputField, prompt, start, results};
    }
    const chain = el("workspace-task-ordinary-chain-confirm");
    chain.querySelector("form").addEventListener("submit", (event) => {
      if (!event.isTrusted) event.preventDefault();
    });
    chain.addEventListener("close", () => {
      const approval = ordinaryChainApproval;
      ordinaryChainApproval = null;
      if (approval && chain.returnValue === "approve") runOrdinaryVisualChain(approval);
    });
    el("workspace-task-provider-confirm").querySelector("form").addEventListener("submit", (event) => {
      if (!event.isTrusted) event.preventDefault();
    });
    el("workspace-task-provider-confirm").addEventListener("close", (event) => {
      const approval = taskOperationApproval;
      taskOperationApproval = null;
      if (approval && event.target.returnValue === "approve") runTaskOperation(approval);
    });
    el("workspace-task-operations-refresh").addEventListener("click", () => loadTaskOperations(context.task));
  }

  function updateTaskOperations() {
    const task = context.page === "poster" ? context.task : null;
    el("workspace-task-provider-operations").hidden = !v2ModelWorkspace() || !task;
    if (!v2ModelWorkspace()) return;
    const id = task?.id || null;
    if (id !== taskOperationTaskId) {
      taskOperationTaskId = id;
      taskOperationList = null;
      taskOperationApproval = null;
      for (const {prompt} of Object.values(taskOperationActions)) {
        if (prompt) prompt.value = "";
      }
      notice("workspace-task-operation-status");
      if (id) loadTaskOperations(task, true);
    }
    drawTaskOperationControls();
  }

  initTaskOperations();

  function v2TaskLabel(purpose) {
    const assignment = v2Snapshot?.assignments[purpose];
    return assignment ? [assignment.model_id, assignment.label, v2Execution(purpose, assignment)].filter(Boolean).join(" · ")
      : v2Execution(purpose, null);
  }

  function v2DispatchControl(target, purpose, applicable) {
    if (!applicable || !target || !v2Snapshot?.fixture || v2Snapshot.assignments[purpose]?.execution !== "fixture_ready") {
      target.hidden = true;
      return;
    }
    target.hidden = false;
  }

  async function v2Dispatch(purpose) {
    const button = document.querySelector("[data-v2-dispatch='" + purpose + "']");
    const result = document.querySelector("[data-v2-dispatch-result='" + purpose + "']");
    if (!v2Snapshot?.fixture || !v2Snapshot.assignments[purpose] || button.disabled) return;
    const assignment = v2Snapshot.assignments[purpose];
    button.disabled = true;
    result.textContent = "正在请求本机替身。";
    try {
      const receipt = await request("/api/model-workspace/dispatch-test", {purpose});
      if (receipt?.mode !== "fixture" || receipt.purpose !== purpose || receipt.model_id !== assignment.model_id ||
          receipt.account_id !== assignment.account_id) throw new Error("route_mismatch");
      result.textContent = "本机路由回执 · " + receipt.model_id + " · 真实调用未验证。";
    } catch {
      result.textContent = "本机路由未通过。请核对此用途的模型与账户。";
    } finally {
      button.disabled = false;
    }
  }

  function updateV2Needs() {
    updateTaskOperations();
    const newImage = el("workspace-image-new").checked;
    const newVideo = el("workspace-video-new").checked;
    const task = context.task;
    const language = v2Snapshot?.assignments.language;
    const confirmedCopy = Boolean(task?.confirmed_copy);
    el("workspace-model-shortcut").textContent = language ? "已选择语言模型" : "添加账户并按用途选择模型";
    el("workspace-language-need").textContent = confirmedCopy
      ? "本任务已有确认文案，本机排版沿用该文案。新任务的卖点 Skill 和商城 Agent 需要可执行的语言模型。"
      : "卖点草稿 · " + v2TaskLabel("language");
    el("workspace-image-gate").hidden = !newImage;
    el("workspace-image-summary").textContent = newImage ? v2TaskLabel("image") : "商品图和版式在本机处理，无需生图";
    el("workspace-image-need").textContent = v2TaskLabel("image") + " 可改用已有商品图继续。";
    el("workspace-video-local").hidden = newVideo;
    el("workspace-video-cloud").hidden = !newVideo;
    el("workspace-video-need").textContent = v2TaskLabel("video") + " 可改用已有视频继续剪辑。";
    for (const [purpose, applicable] of [["language", !confirmedCopy], ["image", newImage], ["video", newVideo]]) {
      const control = document.querySelector("[data-v2-dispatch='" + purpose + "']");
      v2DispatchControl(control, purpose, applicable);
    }
  }

  for (const button of document.querySelectorAll("[data-v2-dispatch]")) {
    button.addEventListener("click", (event) => {
      if (event.isTrusted) v2Dispatch(button.dataset.v2Dispatch);
    });
  }


  function updateNeeds() {
    if (v2ModelWorkspace()) {
      updateV2Needs();
      return;
    }
    if (customerModelWorkspace()) {
      updateCustomerNeeds();
      return;
    }
    const picked = currentModel("language");
    el("workspace-model-shortcut").textContent = picked?.model.runnable
      ? "已配置语言模型替身" : "尚未配置语言模型";
    el("workspace-language-need").textContent = picked?.model.runnable
      ? picked.model.name + " · 本机替身"
      : "待配置支持工具调用的模型";
    const newImage = el("workspace-image-new").checked;
    el("workspace-image-gate").hidden = !newImage;
    el("workspace-image-summary").textContent = newImage ? "生成新图片需要配置" : "用已有商品图，无需配置";
    el("workspace-image-need").textContent = currentModel("image")
      ? "已选图片生成模型。此预览还无法发起生图，可先用已有图片或稍后处理。"
      : "生成新产品图需要选择图片生成模型。可以先改用已有图片或稍后处理。";
    const newVideo = el("workspace-video-new").checked;
    el("workspace-video-local").hidden = newVideo;
    el("workspace-video-cloud").hidden = !newVideo;
    el("workspace-video-need").textContent = currentModel("video")
      ? "已选视频生成模型。此预览还无法生成新镜头，可先使用已有视频或稍后处理。"
      : "生成新镜头需要配置视频生成模型。已有视频可直接剪辑。";
  }

  window.DTWorkspace = {
    openModels,
    reloadCustomer,
    reloadV2: v2Change,
    showOrdinaryVisualChain,
    openOrdinaryVisualChain,
    canSave() {
      if (v2ModelWorkspace() || !el("workspace-image-new").checked) return true;
      notice("poster-status", "新图片还未准备好。改用已有图片后可保存这份资料。", true);
      return false;
    },
    canStart(task) {
      if (v2ModelWorkspace()) {
        notice("poster-token-plan-error", "当前连接配置还不能执行真实商品会话。可查看模型的验证状态。", true);
        return false;
      }
      if (!task) {
        notice("poster-status", "请先保存商品图片和资料。可以先在模型连接里查看已配置的模型。", true);
        return false;
      }
      if (task.token_plan_acceptance?.detail?.requested && !task.token_plan_acceptance.detail.ready) {
        notice("poster-token-plan-error", modelAccessReason(task), true);
        if (!el("poster-task-detail-edit").hidden) el("poster-task-detail-edit").open = true;
        return false;
      }
      if (customerModelWorkspace() && customerSnapshot?.connection?.state === "ready") return true;
      if (!customerModelWorkspace() && currentModel("language")?.model.runnable) return true;
      notice("poster-token-plan-error", customerModelWorkspace() ? "请在 Windows 本机窗口选择支持工具调用的语言模型，再回到原任务开始。" : "请先选一个支持工具调用的语言模型，再回来开始生成。", true);
      openModels("poster");
      return false;
    }
  };

  el("workspace-model-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!event.isTrusted || !snapshot || pending) return;
    const plan = activePlan();
    const typed = el("mc-key").value;
    el("mc-key").value = "";
    if (typed && !demoScenarios.has(typed)) {
      notice("mc-connect-status", "这个预览仅接受本地测试替身。请勿输入真实 API Key。", true);
      return;
    }
    notice("mc-connect-status", "正在读取本机替身目录。");
    change("connect", {
      provider: el("mc-provider").value,
      plan: plan.id,
      workspace: plan.needs_workspace ? el("mc-workspace").value.trim() : "",
      label: el("mc-label").value.trim(),
      key: typed || el("mc-scenario").value,
      ...(reconnect && reconnect.provider === el("mc-provider").value && reconnect.plan === plan.id
        && reconnect.workspace === (plan.needs_workspace ? el("mc-workspace").value.trim() : "") ? {account_id: reconnect.id} : {})
    }, "mc-connect-status", plan.source === "official"
      ? "官方套餐目录已载入。此账户的权限尚未验证。"
      : "替身目录已处理。请查看下方账户及目录结果。");
  });
  el("mc-provider").addEventListener("change", () => { reconnect = null; providerChange(); });
  el("mc-plan").addEventListener("change", () => { reconnect = null; planChange(); });
  for (const purpose of purposes) {
    el("mc-assign-" + purpose).addEventListener("change", (event) => {
      const value = event.target.value;
      const colon = value.indexOf(":");
      const selection = value ? {account_id: value.slice(0, colon), model_id: value.slice(colon + 1)}
        : {account_id: null, model_id: null};
      change("assign", {purpose, ...selection}, "mc-assign-status", value ? "已保存此用途。" : "此用途已清除。");
    });
  }
  for (const [id, view] of [
    ["workspace-open-video", "video"], ["workspace-all-tasks", "tasks"], ["workspace-open-roles", "role"],
    ["workspace-video-transcribe", "transcription"], ["workspace-video-later", "home"], ["workspace-image-later", "home"]
  ]) el(id).addEventListener("click", () => setPage(view, true));
  for (const id of ["workspace-new-product", "workspace-reset-product"]) el(id).addEventListener("click", newProduct);
  for (const id of ["workspace-open-models", "settings-model-link"]) el(id).addEventListener("click", () => openModels());
  for (const id of ["workspace-product-models", "workspace-image-models"]) el(id).addEventListener("click", () => openModels("poster"));
  el("workspace-video-models").addEventListener("click", () => openModels("video"));
  el("mc-return").addEventListener("click", () => {
    const view = returnView || "home";
    const trigger = modelReturnFocus;
    returnView = null;
    modelReturnFocus = null;
    setPage(view, true);
    if (trigger?.isConnected && trigger.getClientRects().length) trigger.focus({preventScroll:true});
  });
  el("workspace-video-edit").addEventListener("click", () => {
    const site = context.task?.site;
    if (["CN", "PH"].includes(site)) el("role-site").value = site;
    updateRoleControls();
    el("role-tool").value = "director-local-montage";
    updateRoleControls();
    setPage("role", true);
  });
  el("workspace-video-reuse").addEventListener("click", () => {
    el("workspace-video-existing").checked = true;
    updateNeeds();
  });
  el("workspace-image-reuse").addEventListener("click", () => {
    el("workspace-image-existing").checked = true;
    updateNeeds();
  });
  for (const id of ["workspace-image-existing", "workspace-image-new", "workspace-video-new", "workspace-video-existing"]) {
    el(id).addEventListener("change", updateNeeds);
  }
  function productChanged() {
    context.draftRevision += 1;
    draftedProduct = true;
    clearedProduct = null;
    writeProductDraft();
    updateDraftActions();
  }
  el("poster-form").addEventListener("input", productChanged);
  el("poster-form").addEventListener("change", event => {
    if (event.target.type === "file") productChanged();
  });
  el("workspace-clear-product").addEventListener("click", () => {
    if (!draftedProduct || context.submitting || !window.confirm("清空这份未保存的商品草稿？可以在继续编辑前撤回。")) return;
    clearedProduct = captureProduct();
    resetProductForm();
    updateDraftActions();
    notice("workspace-draft-status", "草稿已清空。继续编辑前，可以撤回清空。");
    el("workspace-undo-product").focus();
  });
  el("workspace-undo-product").addEventListener("click", () => {
    if (!clearedProduct || context.submitting) return;
    const input = clearedProduct;
    clearedProduct = null;
    draftedProduct = true;
    context.draftRevision += 1;
    const filesRestored = applyProduct(input);
    writeProductDraft();
    updateDraftActions();
    notice("workspace-draft-status", filesRestored ? "原草稿已恢复。" : "原文字已恢复，请重新选择图片。");
    el("workspace-clear-product").focus();
  });
  el("product-file").addEventListener("change", updateLocalPreview);
  el("workspace-source-preview").addEventListener("error", () => { el("workspace-source-preview").hidden = true; });
  function activateModelWorkspace() {
    const v2 = v2ModelWorkspace();
    const customer = customerModelWorkspace();
    el("mc-v2-content").hidden = !v2;
    el("mc-customer-content").hidden = v2 || !customer;
    el("mc-preview-content").hidden = v2 || customer;
    el("mc-preview-intro").hidden = v2 || customer;
    el("mc-customer-intro").hidden = v2 || !customer;
    if (v2) {
      el("mc-title").textContent = "模型连接";
      el("workspace-local-files-note").textContent = "商品照片、草稿和导出文件留在本机。获取模型目录不会发送商品资料。";
      v2Change();
    } else if (customer) {
      el("mc-title").textContent = "模型连接";
      reloadCustomer();
    } else if (context.session?.model_workspace_preview) reloadModels();
  }
  document.addEventListener("dt-session", activateModelWorkspace);
  document.addEventListener("dt-view", (event) => {
    if (event.detail !== "models") returnView = null;
    el("mc-return").hidden = !returnView;
    if (event.detail === "models") {
      if (v2ModelWorkspace()) v2Change();
      else if (customerModelWorkspace()) reloadCustomer();
      else if (!snapshot && context.session?.model_workspace_preview) reloadModels();
    }
    if (event.detail === "home") renderRecent();
    if (event.detail === "poster") renderProductState();
    if (v2ModelWorkspace()) updateNeeds();
  });
  document.addEventListener("dt-tasks", renderRecent);
  document.addEventListener("dt-task", () => {
    renderRecent();
    renderProductState();
    updateNeeds();
  });
  document.addEventListener("dt-saved-product", event => {
    if (event.detail?.clearDraft) {
      clearedProduct = null;
      resetProductForm();
      notice("workspace-draft-status");
      updateDraftActions();
    }
    if (event.detail?.showSaved && context.page === "poster") {
      window.scrollTo(0, 0);
      el("workspace-result-heading").focus({preventScroll:true});
    }
  });
  restoreProductDraft();
  if (context.session) activateModelWorkspace();
  renderProductState();
  updateNeeds();
})();
