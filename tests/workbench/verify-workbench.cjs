"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

if (!process.argv[2]) throw new Error("Provide the isolated loopback URL of a new QA instance.");
const origin = new URL(process.argv[2]);
if (["8735","8769","8772","8775","8778"].includes(origin.port)) throw new Error("Use a new QA port. This workbench port is reserved.");
if (origin.protocol !== "http:" || origin.hostname !== "127.0.0.1" || !origin.port ||
    origin.pathname !== "/" || origin.search || origin.hash || origin.username || origin.password) {
  throw new Error("Use the loopback HTTP origin of the isolated QA workbench.");
}

const runtimeNode = process.env.DT_QA_NODE_MODULES || path.join(process.env.USERPROFILE || "",
  ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "node", "node_modules");
const {chromium} = require(path.join(runtimeNode, "playwright"));
const {PNG} = require(path.join(runtimeNode, "pngjs"));
const chrome = process.env.DT_QA_CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const runTag = crypto.randomBytes(5).toString("hex");
const out = fs.mkdtempSync(path.join(require("node:os").tmpdir(), "dt-workbench-qa-"));
const proof = {scope:"local_browser_and_offline_customer_model_only", pass:false, checks:[],
  requests:[], issueCounts:{pageErrors:0,blockedExternal:0,blockedUnexpected:0}};
const modelPath = "/api/model-workspace";
const assetKeys = ["mall_main_bundle", "mall_detail_bundle", "mall_combined_receipt"];
const sha = bytes => crypto.createHash("sha256").update(bytes).digest("hex");
const credentialFixtures = new Set(["", "demo-local-ok", "demo-local-empty", "demo-local-denied", "demo-local-failed"]);
const taskPath = id => "/api/tasks/" + id;
let browser;
let mayWrite = false;
let ownTaskId = null;
const ownAccounts = new Set();

function fixture(name, variant) {
  const image = new PNG({width:360, height:360});
  for (let y = 0; y < image.height; y++) {
    for (let x = 0; x < image.width; x++) {
      const offset = (y * image.width + x) * 4;
      const object = x > 84 && x < 275 && y > 120 && y < 236;
      image.data[offset] = object ? 35 + 14 * variant : 229;
      image.data[offset + 1] = object ? 96 + Math.floor(y / 8) : 240;
      image.data[offset + 2] = object ? 175 - 17 * variant : 236;
      image.data[offset + 3] = 255;
    }
  }
  const full = path.join(out, name);
  fs.writeFileSync(full, PNG.sync.write(image), {flag:"wx"});
  return full;
}

function status(locator, expected) {
  return locator.waitFor({state:expected, timeout:10000});
}

function responseFor(page, pathname, method = "POST") {
  return page.waitForResponse(reply => {
    return new URL(reply.url()).pathname === pathname && reply.request().method() === method;
  }, {timeout:18000});
}

async function getJson(page, pathname) {
  assert(pathname.startsWith("/api/"));
  const res = await page.request.get(new URL(pathname, origin).href, {maxRedirects:0});
  assert.equal(res.status(), 200, "Readback failed for " + pathname);
  return res.json();
}

async function actionReply(page, pathname, action, code) {
  const [reply] = await Promise.all([responseFor(page, pathname), action()]);
  if (code) {
    assert(!reply.ok(), "Expected the offline model gate to reject " + code);
    assert.equal((await reply.json()).error?.code, code);
    return null;
  }
  assert(reply.ok(), pathname + " returned HTTP " + reply.status());
  return reply.json();
}

async function guardedContext() {
  const ctx = await browser.newContext({serviceWorkers:"block", acceptDownloads:true, viewport:{width:1320,height:900}});
  await ctx.route("**/*", async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin.origin) {
      proof.issueCounts.blockedExternal++;
      return route.abort();
    }
    if (request.method() === "GET") return route.continue();
    const localPost = request.method() === "POST" && (
      /^\/api\/model-workspace\/(?:connect|manual|assign)$/.test(url.pathname) ||
      url.pathname === "/api/tasks/poster" ||
      /^\/api\/tasks\/[a-f0-9]{32}\/(?:detail-evidence|token-plan-foreground|layout-review)$/.test(url.pathname)
    );
    if (!localPost || !mayWrite) {
      proof.issueCounts.blockedUnexpected++;
      return route.abort();
    }
    let safe = false;
    try {
      const body = request.postDataJSON();
      if (url.pathname === modelPath + "/connect") {
        safe = credentialFixtures.has(body.key ?? "") && body.label.startsWith("QA " + runTag + " ");
      } else if (url.pathname.startsWith(modelPath + "/")) {
        safe = ownAccounts.has(body.account_id);
      } else if (url.pathname === "/api/tasks/poster") {
        safe = body.product_ref === "QA-" + runTag;
      } else {
        safe = ownTaskId !== null && url.pathname.startsWith(taskPath(ownTaskId) + "/");
      }
    } catch {}
    if (!safe) {
      proof.issueCounts.blockedUnexpected++;
      return route.abort();
    }
    proof.requests.push(url.pathname.replace(/[a-f0-9]{32}/g, "<fixture-task>"));
    return route.continue();
  });
  return ctx;
}

async function nav(page, view) {
  const button = page.locator('button[data-view="' + view + '"]');
  await button.click();
  await status(page.locator("#" + view + "-view"), "visible");
}

async function connect(page, {provider, plan, label, scenario, workspace = "", failure}) {
  await status(page.locator("#workspace-model-form"), "visible");
  await page.locator("#mc-provider").selectOption(provider);
  await page.locator("#mc-plan").selectOption(plan);
  const workspaceField = page.locator("#mc-workspace");
  if (workspace) {
    await status(workspaceField, "visible");
    await workspaceField.fill(workspace);
  }
  await page.locator("#mc-label").fill(label);
  const choice = page.locator("#mc-scenario");
  if (await choice.isVisible()) await choice.selectOption(scenario);
  else await page.locator("#mc-key").fill(scenario);
  const snapshot = await actionReply(page, modelPath + "/connect",
    () => page.locator("#mc-connect").click(), failure);
  const key = page.locator("#mc-key");
  if (await key.count()) assert.equal(await key.inputValue(), "", "The display kept a submitted fixture credential");
  if (failure) {
    const notice = page.locator("#mc-connect-status");
    await status(notice, "visible");
    assert((await notice.textContent()).trim(), "Connection error supplied no visible feedback");
    const readback = await getJson(page, modelPath);
    assert(!readback.accounts.some(account => account.label === label));
    return null;
  }
  assert.equal(snapshot.prototype, true);
  assert(!JSON.stringify(snapshot).includes(scenario));
  const account = snapshot.accounts.find(item => item.label === label);
  assert(account, "Account save omitted the synthetic account");
  assert.equal(account.workspace, workspace);
  ownAccounts.add(account.id);
  return account;
}

async function assign(page, purpose, account, model) {
  const locator = page.locator("#mc-assign-" + purpose);
  const selected = account.id + ":" + model;
  const snapshot = await actionReply(page, modelPath + "/assign", () => locator.selectOption(selected));
  assert.deepEqual(snapshot.assignments[purpose], {account_id:account.id,model_id:model});
  assert.equal(await locator.inputValue(), selected);
  return snapshot;
}

async function openTaskFromList(page, title) {
  await nav(page, "tasks");
  const row = page.locator("#task-rows tr").filter({has:page.getByText(title,{exact:true})});
  await row.getByRole("button").click();
}

async function take(page, mallTask, kind) {
  const endpoint = taskPath(mallTask) + "/files/" + kind;
  const link = page.locator('a[href^="' + endpoint + '"]');
  await status(link, "visible");
  const browserResponse = responseFor(page, endpoint, "GET");
  await link.click();
  const res = await browserResponse;
  assert.equal(res.status(), 200);
  const bytes = await res.body();
  assert(bytes.length > 0, "Empty local takeout");
  if (kind.endsWith("bundle")) assert.equal(bytes.subarray(0,2).toString(), "PK");
  return bytes;
}

(async () => {
  const source = fixture("synthetic-product.png", 0);
  const evidence = fixture("synthetic-structure.png", 1);
  browser = await chromium.launch({executablePath:chrome,headless:true,chromiumSandbox:true,
    downloadsPath:path.join(out,"downloads"),args:["--disable-background-networking","--disable-sync","--no-first-run"]});
  const ctx = await guardedContext();
  const page = await ctx.newPage();
  page.on("pageerror", () => proof.issueCounts.pageErrors++);
  assert.equal((await page.goto(origin.href, {waitUntil:"domcontentloaded"})).status(), 200);
  assert.equal(new URL(page.url()).origin, origin.origin);
  assert(await page.title());
  await status(page.locator("#workspace-new-product"), "visible");
  const session = await getJson(page, "/api/session");
  assert(/^bava-tokenplan-new-test-design-/.test(session.profile) && session.model_workspace_preview, "A clean preview QA profile is required");
  const initial = await getJson(page, modelPath);
  assert.equal(initial.prototype, true, "The target is not an isolated model-workspace prototype");
  assert.deepEqual(initial.assignments, {language:null,image:null,video:null},
    "Use a clean isolated QA profile so this script never changes an existing assignment");
  mayWrite = true;

  await nav(page, "models");
  const label = suffix => "QA " + runTag + " " + suffix;
  for (const [scenario, failure] of [["demo-local-denied","catalog_denied"],["demo-local-failed","catalog_failed"]]) {
    await connect(page,{provider:"openai",plan:"api",label:label(failure),scenario,failure});
    proof.checks.push(failure + " did not create an account");
  }
  const empty = await connect(page,{provider:"bailian",plan:"payg",label:label("empty"),workspace:"llm-qaworkspace",
    scenario:"demo-local-empty"});
  assert.equal(empty.status,"mock_empty");
  assert.deepEqual(empty.models,[]);
  proof.checks.push("empty interface catalog stays empty");
  const unknown = "qa-unknown-" + runTag;
  const form = page.locator('form[data-account="' + empty.id + '"]');
  await form.locator('input[name="model_id"]').fill(unknown);
  const afterManual = await actionReply(page, modelPath+"/manual", () => form.locator("button[data-model-manual]").click());
  const row = afterManual.accounts.find(account => account.id === empty.id).models.find(model => model.id === unknown);
  assert.equal(row.tools, "unknown");
  assert.equal(row.runnable, false);
  for (const purpose of ["language","image","video"]) {
    assert.equal(await page.locator('#mc-assign-' + purpose + ' option[value="' + empty.id + ':' + unknown + '"]').count(), 0);
    assert.equal(afterManual.assignments[purpose],null);
  }
  proof.checks.push("manual unknown capability cannot be selected for any purpose");

  const language = await connect(page,{provider:"bailian",plan:"token-personal",label:label("language"),scenario:"demo-local-ok"});
  const languageOnly = await assign(page,"language",language,"qwen3.8-max");
  assert.equal(languageOnly.assignments.image,null);
  assert.equal(languageOnly.assignments.video,null);

  await nav(page,"video");
  await status(page.locator("#workspace-video-existing"),"visible");
  await status(page.locator("#workspace-video-new"),"visible");
  await page.locator("#workspace-video-new").check();
  await status(page.locator("#workspace-video-need"),"visible");
  assert.match(await page.locator("#workspace-video-need").textContent(),/模型|还需|接入/);
  await page.locator("#workspace-video-models").click();
  await status(page.locator("#workspace-model-form"),"visible");
  await page.locator("#mc-return").click();
  await status(page.locator("#video-view"),"visible");
  await page.locator("#workspace-video-existing").check();
  await page.locator("#workspace-video-edit").click();
  await status(page.locator("#role-view"),"visible");
  assert.equal(await page.locator("#role-tool").inputValue(),"director-local-montage");
  proof.checks.push("new video shows model need and returns from settings; existing footage opens local montage");

  await nav(page,"home");
  await page.locator("#workspace-new-product").click();
  await status(page.locator("#poster-form"),"visible");
  const productTitle = "合成夹具 " + runTag;
  for (const [selector,value] of [
    ["#product-title",productTitle],["#product-facts","硅胶夹具\n圆角夹口"],["#poster-product-ref","QA-"+runTag]
  ]) await page.locator(selector).fill(value);
  await page.locator("#product-file").setInputFiles(source);
  assert.equal(await page.locator("#poster-combined-detail").isChecked(),false);
  await page.locator("#poster-combined-detail").check();
  await page.locator("#poster-combined-fields-fit-fact").fill("可夹圆杆");
  await page.locator("#poster-combined-fields-fit-source").selectOption("original");
  await page.locator("#poster-combined-fields-quality-fact").fill("外层为硅胶");
  await page.locator("#poster-combined-fields-quality-source").selectOption("evidence");
  await page.locator("#poster-combined-fields-quality-image").setInputFiles(evidence);
  const {task} = await actionReply(page,"/api/tasks/poster",() => page.locator("#make-poster").click());
  ownTaskId = task.id;
  const ownTask = taskPath(task.id);
  assert.equal(task.token_plan_acceptance.detail.ready,false);
  assert.equal(task.token_plan_acceptance.can_start,false);
  assert.deepEqual(task.token_plan_acceptance.detail.missing,[
    {field:"spec",reason:"fact"},{field:"spec",reason:"image"},
    {field:"use",reason:"fact"},{field:"use",reason:"image"}
  ]);

  await page.locator("#workspace-product-models").click();
  await status(page.locator("#workspace-model-form"),"visible");
  const earlier = await getJson(page,ownTask);
  assert.equal(earlier.task.id,task.id);
  await page.locator("#mc-return").click();
  await status(page.locator("#poster-task-detail-edit"),"visible");
  assert.equal((await getJson(page,ownTask)).task.id,task.id);
  assert.equal(await page.locator("#result-name").textContent(),productTitle);
  proof.checks.push("model settings return to the same partially saved product task");

  const edit = page.locator("#poster-task-detail-edit");
  if (!await edit.evaluate(item => item.open)) await edit.locator("summary").click();
  for (const [key,fact] of [["spec","长度十厘米"],["use","捏住柄部压合"]]) {
    await page.locator("#poster-task-detail-fields-"+key+"-fact").fill(fact);
    await page.locator("#poster-task-detail-fields-"+key+"-source").selectOption("original");
  }
  const updated = (await actionReply(page,ownTask+"/detail-evidence",
    () => page.locator("#poster-task-detail-save").click())).task;
  assert.equal(updated.id,task.id);
  assert.equal(updated.token_plan_acceptance.detail.ready,true);
  assert.equal(updated.token_plan_acceptance.detail.fields.quality.image_kind,"evidence");
  assert(!proof.requests.some(name => name.endsWith("/token-plan-foreground")));

  const started = (await actionReply(page,ownTask+"/token-plan-foreground",
    () => page.locator("#poster-token-plan-start").click())).task;
  assert.equal(started.id,task.id);
  const review = page.locator("#planned-approval");
  await review.waitFor({state:"visible",timeout:45000});
  assert.equal((await getJson(page,ownTask)).task.versions.length,0);
  for (const flag of ["copy","asset","layout"]) await review.locator("#planned-check-"+flag).check();
  await actionReply(page,ownTask+"/layout-review",() => review.locator("#planned-render").click());
  const receipt = page.locator("#poster-token-plan-receipt");
  await receipt.waitFor({state:"visible",timeout:145000});
  const produced = (await getJson(page,ownTask)).task;
  assert.equal(produced.token_plan_acceptance.state,"completed");
  assert.equal(produced.token_plan_acceptance.real_acceptance_passed,false);
  assert.equal(produced.versions[0].version,1);
  const {mall_task_id:mall} = produced.token_plan_acceptance;
  assert.match(mall,/^[a-f0-9]{32}$/);
  const preview = new URL(await page.locator("#poster-preview").getAttribute("src"), origin);
  assert.equal(preview.pathname,ownTask+"/versions/1/png");
  const [previewBytes,pngBytes,psdBytes] = await Promise.all([
    page.request.get(preview.href,{maxRedirects:0}),
    page.request.get(new URL(ownTask+"/versions/1/png?download=1",origin).href,{maxRedirects:0}),
    page.request.get(new URL(ownTask+"/versions/1/psd?download=1",origin).href,{maxRedirects:0}),
  ].map(async value => {const res=await value;assert.equal(res.status(),200);return res.body();}));
  assert(previewBytes.equals(pngBytes));
  assert.equal(pngBytes.readUInt32BE(16),720);
  assert.equal(pngBytes.readUInt32BE(20),880);
  assert.equal(psdBytes.subarray(0,4).toString(),"8BPS");
  proof.product={task:task.id,version:1,mall,poster_png_sha256:sha(pngBytes),poster_psd_sha256:sha(psdBytes)};

  await receipt.click();
  await status(page.locator("#role-view"),"visible");
  const artifacts = {};
  for (const kind of assetKeys) artifacts[kind] = await take(page,mall,kind);
  const bundleProof = JSON.parse(artifacts.mall_combined_receipt.toString());
  assert.equal(bundleProof.binding.task_id,task.id);
  assert.equal(bundleProof.binding.version,1);
  assert.equal(bundleProof.mall_task_id,mall);
  assert.equal(bundleProof.archive_sha256.main,sha(artifacts.mall_main_bundle));
  assert.equal(bundleProof.archive_sha256.detail,sha(artifacts.mall_detail_bundle));
  assert.equal(bundleProof.publish_authorized,false);
  assert.equal(bundleProof.human_signoff_recorded,false);
  const noFallback = await getJson(page,modelPath);
  assert.equal(noFallback.assignments.image,null);
  assert.equal(noFallback.assignments.video,null);
  proof.checks.push("three-piece local output and mall receipt work with only a language model assigned");

  await nav(page,"models");
  const image = await connect(page,{provider:"openai",plan:"api",label:label("image"),scenario:"demo-local-ok"});
  await assign(page,"image",image,"gpt-image-1");
  const video = await connect(page,{provider:"bailian",plan:"token-team",label:label("video"),scenario:"demo-local-ok"});
  const routes = await assign(page,"video",video,"wan3.0-video");
  assert.deepEqual(routes.assignments.language,{account_id:language.id,model_id:"qwen3.8-max"});
  assert.deepEqual(routes.assignments.image,{account_id:image.id,model_id:"gpt-image-1"});
  assert.deepEqual(routes.assignments.video,{account_id:video.id,model_id:"wan3.0-video"});
  const persisted = await getJson(page,modelPath);
  assert.deepEqual(persisted.assignments,routes.assignments);
  proof.checks.push("three purposes retain separate accounts and display models without running image or video generation");
  await nav(page,"video");
  await page.locator("#workspace-video-new").check();
  await status(page.locator("#workspace-video-need"),"visible");
  assert.match(await page.locator("#workspace-video-need").textContent(),/已选.*模型.*无法.*生成/);
  proof.checks.push("assigning a video model does not enable generation in the offline preview");

  await ctx.close();
  mayWrite = false;
  const cold = await guardedContext();
  const coldPage = await cold.newPage();
  coldPage.on("pageerror",() => proof.issueCounts.pageErrors++);
  await coldPage.goto(origin.href,{waitUntil:"domcontentloaded"});
  await openTaskFromList(coldPage,productTitle);
  await status(coldPage.locator("#poster-token-plan-receipt"),"visible");
  assert.equal(await coldPage.locator("#version-select").inputValue(),"1");
  const coldPsd = new URL(await coldPage.locator("#download-psd").getAttribute("href"),origin);
  assert.equal(coldPsd.pathname,ownTask+"/versions/1/psd");
  const fetchAgain = await coldPage.request.get(coldPsd.href,{maxRedirects:0});
  assert.equal(fetchAgain.status(),200);
  assert.equal(sha(await fetchAgain.body()),proof.product.poster_psd_sha256);
  await coldPage.locator("#poster-token-plan-receipt").click();
  proof.coldTakeout = {};
  for (const kind of assetKeys) {
    const bytes = await take(coldPage,mall,kind);
    assert.equal(sha(bytes),sha(artifacts[kind]));
    fs.writeFileSync(path.join(out,kind+(kind.endsWith("bundle")?".zip":".json")),bytes,{flag:"wx"});
    proof.coldTakeout[kind]=sha(bytes);
  }
  assert.deepEqual((await getJson(coldPage,modelPath)).assignments,routes.assignments);
  await cold.close();
  proof.checks.push("fresh browser reopens same poster PSD and same-version mall artifacts byte-for-byte");
  assert.deepEqual(proof.issueCounts,{pageErrors:0,blockedExternal:0,blockedUnexpected:0});
  proof.pass = true;
})().catch(error => {
  proof.error = String(error.stack).replace(/demo-local-(?:ok|empty|denied|failed)/g,"<fixture-scenario>");
}).finally(async () => {
  if (browser) await browser.close();
  fs.writeFileSync(path.join(out,"verification.json"),JSON.stringify(proof,null,2)+"\n",{flag:"wx"});
  console.log(path.relative(process.cwd(),path.join(out,"verification.json")) + (proof.pass ? " PASS" : " FAIL"));
  process.exitCode = proof.pass ? 0 : 1;
});
