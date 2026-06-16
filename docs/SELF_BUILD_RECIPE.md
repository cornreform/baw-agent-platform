# BAW Self-Build Recipe

> 當任務係「幫我 scrape XXX 網站 / 整個 tool / register 落 BAW」，
> **必須**跟呢個 recipe 跑。唔好 freestyle — freestyle 嘅 sub-agent
> 100% 會撞 path 錯、curl missing、verify skip 嘅陷阱。

## TL;DR — 6 個 step

```
0. PRE-FLIGHT — python -m core.preflight <url>  (refuse if BLOCKED)
1. PLAN       — 讀 source、定 fields
2. FETCH      — tools.http_fetch.http_fetch(url)  (auto-detects SPA)
3. PARSE      — BeautifulSoup or regex on raw HTML
4. STORE      — write to data_dir() / "<thing>.json"
5. TOOL       — create tools/<thing>.py + register
6. VERIFY     — run `baw self-test` + 5 verify commands
```

每 step 完成後**必須**用對應嘅 verify command 確認，唔可以用「I think it
worked」做收工依據。Sub-agent 嘅 system prompt 入面已經寫咗：
> *NEVER mark a step as 'done' unless you have evidence (file exists, API
> returned 200, config read back).*

呢個 recipe 將「evidence 係咩」具體化。

---

## Golden Rules（2026-06-12 systemic fix）

呢個 section 將「過去 sub-agent 撞過嘅 systemic problem」寫死。任何 sub-agent 開工前都必須 read 一次：

| Rule | 點解 | 邊度 enforce |
|---|---|---|
| **Default data source = free + no-key + stdlib** | 2026-06-12 sub-agent default Google Places → 卡喺 fake API key | `core/data_sources.py` registry — 8 個 categories 全部有 free entry，sub-agent 必須先 consult |
| **Fetch strategy = `core.http_fetch.http_fetch`** | `urllib` 撞 Next.js SPA → 0 bytes empty shell → 假裝 "upstream limit" | `tools/http_fetch.py` 自動 detect 9 種 SPA fingerprint |
| **`curl` binary 唔存在** | 2026-06-12 sub-agent 用 `subprocess.run(["curl"])` → ImportError | `http_fetch` 唔需要 binary；`recipe` 講明 `urllib` stdlib ONLY |
| **Path = `from core.paths import ...`** | Host `/home/baw/baw/` vs container `/app/` 撞 sub-agent | `core/paths.py` 用 `$BAW_HOME` + repo markers resolve |
| **TOOL_DEF 必須有齊 5 個 key** | 4 個 tool 漏 `risk_level`，3 個 tool 漏 `handler` | `core/tool_schema.py` 喺 `register()` 自動 validate，hard fail 立刻 surface |
| **Verify = `baw self-test`** | "Done" 冇 evidence → 紙上完工 | self-test 而家查 5 樣嘢：path / tool registry / TOOL_DEF schema / data sources / system defaults |
| **Cache = `data/*_cache.json`，gitignore** | 2026-06-12 commit 落咗 576KB restaurant cache | `.gitignore` 寫低咗 |
| **CLI subcommand 兩種 routing** | `baw self-test --no-fetch` 同 `baw pet district 灣仔` 唔同 shape | `cli/main.py` dispatch 用 `uses_argparse` flag |
| **Tool count ≤ 20** | > 20 個 LLM 揀 tool accuracy 跌 | `self-test` 自動 warn |
| **一個 tool 一個 risk_level** | 4 個 tool 漏咗，要 runtime 默認 "low" | `core/tool_schema.RISK_LEVELS = ("low","medium","high")` |

**永遠唔好跳呢個 table。**

---

## Step 0 — PRE-FLIGHT (新增，2026-06-12)

**做咩**：開工前先 verify BAW 嘅 capability 夠唔夠做呢個 task。

**點解要加呢個 step**：2026-06-12 個 sub-agent 撞兩次牆：
1. 用 `urllib` 撞 Next.js SPA → 攞到 0KB empty shell → 假裝 50/1000 係 upstream limit
2. 用 `subprocess.run(["curl"])` → curl binary 唔存在 → ImportError

兩個都係 **capability 唔夠但照開工**。Pre-flight 將呢類 trap 喺 Step 0 揭出嚟。

**跑**：
```bash
cd ~/baw && python -m core.preflight https://example.com
```

**4 個 check 自動行**：
| Check | 查咩 | BLOCK 條件 |
|---|---|---|
| `tool_availability` | `urllib` / `requests` / `bs4` / `yaml` / `web_extract` | 任何一個 critical tool missing |
| `network` | URL 個 host 解唔解決 | DNS failure |
| `disk` | `data_dir()` 仲有幾多 free space | < 50 MB |
| `path_resolution` | `core.paths.repo_root()` 係咪指到真嘅 BAW root | repo root 冇 `cli/main.py` |

**Verdict**：
- `PASS` — 直接開工
- `WARN` — 開得工但要留意（例如 URL 喺 vercel.app 上面，**預期**要 mirror）
- `BLOCK` — 唔開工，`next_steps` 寫低點 fix

**自動 SPA 預警**：如果 URL host 結尾係 `vercel.app` / `netlify.app` / `github.io` / `herokuapp.com` / `pages.dev`，pre-flight 直接 warn：「呢個 host 係 known SPA，記住要用 mirror pattern」。

**Verify**：output 入面 `verdict == "PASS"` 或 `"WARN"`，冇 `"BLOCK"`。

---

## Step 1 — PLAN

**做咩**：寫低要 fetch 邊個 URL、要抽咩 fields、最終 user 想問咩 query。

**Path 安全**：
- 用 `from core.paths import data_dir, tools_dir` 攞 path
- **唔好用** `~/baw/` hardcode
- **唔好用** `/home/baw/baw/` 喺 host side（呢個係個 sub-agent 上次撞嘅坑）

**Verify**：plan 寫低喺 todo list 上面，`baw todo list` 見到。

---

## Step 2 — FETCH

**做咩**：用 Python stdlib 攞 HTML。

### Step 2a: 決定 data source（**唔好直接落 code**）

**第一步：consult `core/data_sources.REGISTRY`**。用 `python -c "from core.data_sources import summary_block; print(summary_block())"` 睇下 8 個 categories 入面你嘅 data type 屬於邊個 entry。

如果個 category 已經喺 registry 入面（例如 `restaurants` / `geocoding` / `weather`），**直接用個 default source** — 唔好自己揀 Google Places / 任何 paid service。如果係新 category，先**加新 entry 入 registry** 然後先用。

**Step 2 入面就做呢個 check** — 唔好 plan 之後先做，否則寫低 paid service 之後又要 refactor。

**唯一推薦方法**：
```python
import urllib.request
req = urllib.request.Request(url, headers={"User-Agent": "BAW/1.0"})
with urllib.request.urlopen(req, timeout=30) as r:
    html = r.read().decode("utf-8", errors="replace")
```

**禁止**：
- `subprocess.run(["curl", ...])` — `curl` binary 喺 Python venv 唔存在
- `os.system("wget ...")` — 同一問題
- `requests.get(...)` — 要 pip install，先用 stdlib

**Verify**：
```bash
python3 -c "import json; d=json.load(open('/home/user/baw/data/<thing>.json')); print('rows:', len(d.get('restaurants', d.get('items', []))))"
```
個 file 要存在、size > 0、有至少 1 條 record。

---

## Step 3 — PARSE

**做咩**：抽 fields 出嚟。

**推薦**：
```python
from html.parser import HTMLParser
# or
import re
# or
try:
    from bs4 import BeautifulSoup  # optional dep, fall back to regex
except ImportError:
    pass
```

**禁忌**：
- 唔好用 JavaScript-rendered page（urllib 攞唔到 React/Vue 嘅 output）
- 唔好假設 HTML 結構穩定 — 寫 tolerant parser，每個 field 都要 handle missing

**Verify**：print 抽到嘅 sample records，confirm 結構正確。

---

## Step 4 — STORE

**做咩**：將 parsed records 寫成 JSON dataset。

**Path**：`data_dir() / "<thing>.json"` （**唔好** hardcode）

**Schema 推薦**：
```json
{
  "source_url": "https://...",
  "scraped_at": "2026-06-12T21:30:00+08:00",
  "total_announced": 1000,
  "available_in_dataset": 50,
  "note": "Only 50 published as of scrape. Re-run when upstream updates.",
  "schema": { "fields": ["name", "district", ...] },
  "items": [ {"id": "PR-0001", "name": "...", ...} ]
}
```

**Verify**：
```bash
python3 -c "import json; d=json.load(open(<path>)); assert d['items']; print('OK', len(d['items']))"
```

---

## Step 5 — TOOL + REGISTER

**做咩**：將 dataset 變成可 query 嘅 tool + CLI command。

### 5a) Tool file

`tools/<thing>.py` 必須有齊 **5 個** required key（`core/tool_schema.py` 會喺 `register()` 自動 validate）：

```python
"""BAW built-in: <thing> — <one-line description>"""
from core.paths import data_dir
# ... 邏輯 ...

TOOL_DEF = {
    "name": "<thing>",                            # required
    "description": "<one-line, <600 chars>",     # required
    "handler": <entry_fn>,                        # required, callable
    "parameters": { ... JSON Schema ... },        # required, {"type": "object", ...}
    "risk_level": "low|medium|high",              # required — pick the lowest that fits
}
```

**Risk level guide**:
- `low`: read-only (web_search, http_fetch, read_file, vision, memory read)
- `medium`: writes local files / makes outbound stateful calls (write_file, tts, image_generate, restaurant cache writes)
- `high`: runs code, mutates config, deletes, network-destructive (bash, execute_code, delegate_task, browser, restaurant with pet intersect)

**唔可以加** `examples` / `category` / 任何 ALLOWED_KEYS 以外嘅 key — 會被 schema reject。

### 5b) Register at boot

`tools/__init__.py`：
```python
from . import bash, read_file, ..., <thing>  # 加 import

def register_all():
    register(**bash.TOOL_DEF)
    ...
    register(**<thing>.TOOL_DEF)  # 加一行
```

### 5c) CLI command (optional but recommended)

`cli/commands/<thing>_cmd.py` + `cli/main.py` router entry。

**Verify — 全部 4 個都 run**：
```bash
cd ~/baw
python3 -c "from tools.<thing> import TOOL_DEF; print(TOOL_DEF['name'])"
python3 -c "import tools; tools.register_all(); from core.tools import get_tool; t=get_tool('<thing>'); print('OK' if t else 'NOT REGISTERED')"
python3 -c "from cli.commands.<thing>_cmd import main; main(['list'])"
baw <thing> --help
```

**5/5 pass 先算 done**。任何一個 fail → fix → 再 verify。

---

## 常見坑（從 2026-06-12 pet-restaurant sub-agent 學到）

| 坑 | 點解 | 點 fix |
|---|---|---|
| 用 `/home/baw/baw/` | 喺 host 唔存在（呢個係 Docker container 入面嘅 path） | 用 `from core.paths import repo_root, data_dir` |
| `subprocess.run(["curl", ...])` | BAW Python venv 冇 `curl` binary | 用 `urllib.request` stdlib |
| `len(text) > 0` 就當 fetch 成功 | 攞咗 0KB HTML 都當 pass | `assert len(html) > 1000` + parse 抽樣 5 條 |
| 「Done」冇 evidence | 紙上完工 | Step 5 verify 5 個命令全部 pass |
| Syntax error in tool file | 冇 import-test 就 ship | 寫完即 `python3 -c "import ast; ast.parse(open('tools/<thing>.py').read())"` |
| NYC 預設座標 | IP geolocation 假設失敗 | 用 Hong Kong 已知 district centroid，或者直接 query by district name |
| 永遠 NYC IP | 冇 retry 唔同 geolocation service | 加 fallback：Cloudflare headers > ipinfo.io > 預設 HK |
| **Next.js / Gatsby / React SPA** | `urllib` 攞到嘅只係 static HTML shell，餐廳 data 係 JS runtime 注入。`__NEXT_DATA__` 可能唔存在。`requests_html` / `playwright` 唔係 stdlib | **用 mirror pattern**：用 `web_extract`（內置 browser-fetch 工具）攞 rendered markdown → save 入 `data/<thing>_source.md` → tool parse markdown。**Annotate 個 source file 解釋點解咁做** |
| 假設 upstream publish 咗 1000/1000 | Lottery 2026-06-12，petwellhk.com 只 publish 50 sample | Tool 出 stats() 顯示 available_in_dataset vs total_announced，user 一睇就知上游未出齊 |

---

## 何時用呢個 recipe

**用**：
- 「幫我 scrape 呢個 URL」
- 「整個 tool 嚟 query <dataset>」
- 「Build me a 餐廳 / 餐牌 / 巴士 / 天氣 tool」

**唔好用**（用其他 pattern）：
- 純粹 debug 現有 code
- 純粹改 config
- 純粹查 / 讀 file

---

## Self-test

跑 `baw self-test` 自動行 recipe 嘅 Step 2+3+4 喺一個 sample URL 上面（eg. 一個
公開 Wikipedia page），verify end-to-end 通。失敗嘅話 BAW 而家會講得出邊 step 死。
