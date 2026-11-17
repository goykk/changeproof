import { chromium, expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

// 单条真实浏览器用例：导入仓库 → 创建 changeset → 触发分析 → 等 reported → 断言影响图与报告。
// 浏览器或后端不可用时 skip，由 make e2e 回退 scripts/e2e_http.py 做 HTTP 等价断言。

const WEB_BASE_URL = "http://127.0.0.1:3100";
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8100";
const PROJECT_ROOT = path.resolve(process.cwd(), "..", "..");
const LOCK_PATH = path.join(PROJECT_ROOT, "eval", "samples", "lock.json");
const REPO_LOCAL_PATH = "var/samples/shop_api";
const PREFERRED_SAMPLE_ID = "shop_api_rename_q1";
const REPORT_TIMEOUT_MS = 120_000;

type SampleRef = { base_sha: string; target_sha: string };

function readSampleRef(): SampleRef {
  const lock = JSON.parse(readFileSync(LOCK_PATH, "utf8")) as Record<string, Record<string, SampleRef>>;
  const group = lock.shop_api ?? {};
  const ref = group[PREFERRED_SAMPLE_ID] ?? Object.values(group)[0];
  if (!ref) throw new Error(`lock.json 中没有可用样本 sha：${LOCK_PATH}（请先执行 make sample）`);
  return ref;
}

test("导入仓库 → 创建 changeset → 分析 → 影响图与报告", async ({ request }) => {
  // headless 模式实际使用 chrome-headless-shell，因此用真实 launch 探测，而不是检查 executablePath。
  const browser = await chromium.launch({ headless: true }).catch(() => null);
  test.skip(
    browser === null,
    "未安装 chromium：执行 make e2e，或 PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright npx playwright install chromium",
  );
  if (browser === null) return;

  const health = await request.get(`${API_BASE_URL}/api/health`).catch(() => null);
  if (!health?.ok()) {
    await browser.close();
    test.skip(true, `后端未在 ${API_BASE_URL} 运行：先执行 make e2e`);
    return;
  }

  const { base_sha, target_sha } = readSampleRef();
  const context = await browser.newContext({ baseURL: WEB_BASE_URL });
  const page = await context.newPage();

  try {
    await page.goto("/");
    await page.getByTestId("repo-path-input").fill(REPO_LOCAL_PATH);
    await page.getByTestId("repo-submit").click();
    // 导入成功后表单会被清空；失败时保留路径并显示 ErrorBanner。
    await expect(page.getByTestId("repo-path-input")).toHaveValue("", { timeout: 30_000 });

    await page.getByTestId("commit-base").fill(base_sha);
    await page.getByTestId("commit-target").fill(target_sha);
    await expect(page.getByTestId("changeset-create")).toBeEnabled({ timeout: 30_000 });
    await page.getByTestId("changeset-create").click();

    await page.waitForURL(/\/changesets\/[^/]+$/);
    const changesetId = new URL(page.url()).pathname.split("/").filter(Boolean).pop() ?? "";
    expect(changesetId).not.toBe("");

    await page.getByTestId("analyze-button").click();
    await expect(page.getByTestId("task-status")).toContainText("reported", { timeout: REPORT_TIMEOUT_MS });

    const impactGraph = page.getByTestId("impact-graph");
    await expect(impactGraph).toBeVisible();
    await expect
      .poll(async () => Number((await impactGraph.getAttribute("data-node-count")) ?? 0), { timeout: 30_000 })
      .toBeGreaterThan(0);

    await page.goto(`/changesets/${changesetId}/report`);
    await expect(page.getByTestId("report-summary")).toContainText(/\S/, { timeout: 30_000 });
    await expect(page.getByTestId("report-risks")).toContainText(/\S/, { timeout: 30_000 });
  } finally {
    await context.close();
    await browser.close();
  }
});