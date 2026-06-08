# BAW Version Workflow

> BAW 使用 **Snapshot-based versioning** — 每個版本係完整獨立嘅備份，唔會直接修改基準。

---

## 版本管理哲學

```
v0.10 ── 基準（凍結、永不修改）
  │
  ├── 複製 → v0.11 (開發中)
  │     └── 完成後 tag + archive
  │
  ├── 複製 → v0.12 (開發中)
  │     └── ...
  │
  └── 複製 → v1.0 (正式版，可選)
        └── ...
```

- **v0.10 係基準 (Baseline)** — 永遠唔會改
- 每次新版本從基準 **完整複製** 一份出嚟，喺 copy 上面改
- 每個完成嘅版本會被 **tag + archive（read-only）**
- `~/baw/` 永遠係 **當前活躍開發目錄**

---

## 基準存檔位置

| Location | Description | Protection |
|----------|-------------|------------|
| `~/baw-archive/v0.10/` | 完整 frozen copy（file=444, dir=555） | 🔒 Read-only |
| `https://github.com/cornreform/baw-agent-platform` | Git repo on GitHub | Git tag `v0.10` |
| `~/baw/` | 活躍開發目錄 | ⚡ 可修改 |

---

## 版本號命名規則

| Format | Meaning | Example |
|--------|---------|---------|
| **v0.xx** | 測試版本，可能 major breaking change | v0.10, v0.11, v0.12 |
| **v1.xx** | 正式版本，stable API | v1.0, v1.1, v1.2 |
| **vX.xx→vY.yy** | Major version bump = 重大 redesign | v0.11 → v1.0 |

Version number 由 Sunny 決定，冇固定 increment 規則。

---

## 建立新版本流程

### 步驟

```bash
# 1. 確認而家嘅 ~/baw/ 係你滿意嘅狀態
cd ~/baw
git status                    # 檢查有冇未 commit 嘅改動
baw --version                 # 確認當前版本

# 2. Git tag
git tag -a v0.11 -m "BAW v0.11 — 詳細說明改動"
git push origin v0.11

# 3. 建立 frozen archive（完整複製 + 鎖 read-only）
mkdir -p ~/baw-archive
cp -a ~/baw ~/baw-archive/v0.11
find ~/baw-archive/v0.11/ -type f -exec chmod 444 {} +
find ~/baw-archive/v0.11/ -type d -exec chmod 555 {} +

# 4. 複製基準開始新版本開發
rm -rf ~/baw                          # 刪除舊嘅活躍目錄
cp -a ~/baw-archive/v0.10 ~/baw      # 從基準複製全新嘅開發目錄
# 或者用任何舊版本做基礎都得
```

### 注意事項

- **永遠唔好直接修改 `~/baw-archive/v*/` 入面嘅檔案** — 佢哋係 read-only 嘅
- 新版本開發喺 `~/baw/` 做
- 完成後重複步驟 1-4

---

## 當前狀態

| 版本 | 狀態 | 位置 |
|------|------|------|
| v0.10 | ✅ Baseline（凍結） | `~/baw-archive/v0.10/` |
| v0.11 | ✅ Released | `~/baw-archive/v0.11/` + GitHub tag |
| ~/baw/ | 🔄 活躍開發 | `~/baw/` |

---

## FAQ

**Q: 可唔可以從舊嘅 archive copy 開始新版本？**
A: 可以。你指定任何一個 archive 版本做 base 就得。

**Q: 如果改咗 base config（SOUL.md / config.yaml）點算？**
A: Copy 嘅時候會保留 base 嘅 user config。新版本可以自由改。

**Q: Archive 佔幾多位？**
A: 約 2.8MB per version（純 code，dependencies 喺 system level）。

**Q: Git tags 同 archive 嘅關係？**
A: Git tag 記錄 code 嘅 snapshot；archive 係完整嘅獨立檔案系統備份（含 .git）。
兩者互相印證。
