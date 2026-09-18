# Spellbook 本機校對介面實作計畫

日期：2026-09-18

規格來源：`docs/superpowers/specs/2026-09-18-local-review-interface-design.md`

## 1. 實作原則

- 以小型、可獨立測試的單元逐步交付。
- 每個階段先加入失敗測試，再完成最小實作。
- 不直接改寫現有 A–Z 資料；先建立不可變基準及重建驗證。
- 每個階段結束時，SQLite、JSON、報告與測試都必須維持一致。
- 正式介面不得依賴網路、CDN 或外部服務。
- Windows 可攜式發行前，開發模式必須先能完整完成所有校對流程。

## 2. 預定專案結構

```text
spellbook_reviewer/
  __init__.py
  app.py
  launcher.py
  config.py
  security.py
  domain/
    events.py
    reviews.py
    conflicts.py
  repositories/
    spell_repository.py
    event_repository.py
    draft_repository.py
    git_repository.py
  services/
    review_service.py
    replay_service.py
    rebuild_service.py
    conflict_service.py
    commit_service.py
    pdf_service.py
  web/
    templates/
    static/
schemas/
  review-event.schema.json
migrations/
  002_review_workflow.sql
scripts/
  create_review_baseline.py
  rebuild_review_database.py
  verify_review_repository.py
tests/
  unit/
  integration/
  e2e/
packaging/
  spellbook-reviewer.spec
```

現有擷取、翻譯、資料庫與匯出腳本繼續保留。新程式透過明確服務介面呼叫它們，不把既有邏輯複製到網頁路由。

## 3. 階段一：開發環境與不可變基準

### 3.1 建立依賴與執行入口

新增：

- `pyproject.toml`
- 鎖定版本的發行依賴與開發依賴
- `spellbook_reviewer/__init__.py`
- 最小 `spellbook_reviewer/app.py`

依賴限定為 FastAPI、Uvicorn、Jinja2、表單解析、JSON Schema 驗證、測試工具、瀏覽器端對端測試及 PyInstaller 所需套件。前端資源全部放入 repository。

驗證：

- 開發模式可在隨機本機連接埠啟動及停止。
- `/health` 只回傳非敏感狀態。
- 服務只綁定 `127.0.0.1`。

### 3.2 產生基準資料

新增 `scripts/create_review_baseline.py`：

1. 從目前已驗證的 SQLite 讀取完整資料。
2. 使用固定排序及穩定 JSON 格式產生 `data/baseline/spells-v1.json`。
3. 寫入基準 schema version、來源 PDF hash、法術筆數及內容 hash。
4. 若目標檔已存在且內容不同，預設拒絕覆寫。

測試：

- 基準包含 2,360 筆法術。
- 所有 `spl_` ID 與目前 SQLite 一致。
- 重複執行得到相同位元內容及 SHA-256。
- 基準缺欄或來源資料驗證失敗時不產生檔案。

提交檢查點：`Add immutable review baseline and project runtime`

## 4. 階段二：事件 Schema、資料庫 migration 與重播核心

### 4.1 校對事件模型

新增 `schemas/review-event.schema.json` 與 `domain/events.py`，定義：

- ULID 格式的 `event_id`。
- `schema_version`。
- 編輯者、UTC 時間、法術及條目 ID。
- action enum。
- `base_revision_hash`。
- 欄位變更陣列。
- 四類核對狀態。
- 備註與穩定序列化。

action 至少包含：

- `update_fields`
- `approve_review`
- `resolve_conflict`
- `set_variant_group`
- `merge_spell`
- `revert_event`

測試事件建立、Schema 驗證、欄位路徑白名單、穩定 JSON 及內容 hash。

### 4.2 SQLite migration

新增 `migrations/002_review_workflow.sql` 及 migration runner，建立：

- `review_events`
- `review_checks`
- `review_conflicts`
- `spells.merged_into_id`
- `spells.record_status`
- `spells.reviewed_revision_hash`

約束：

- `merged_into_id` 必須指向另一筆 spell。
- merged 記錄不能再接受一般欄位修改。
- 四類核對只對指定 revision 有效。
- 事件 ID 唯一且不可被更新。

測試 migration 從目前 schema 升級、重複執行、外鍵及失敗回滾。

### 4.3 重播與衝突判斷

新增：

- `event_repository.py`
- `replay_service.py`
- `conflict_service.py`

實作：

- 掃描 `reviews/changes/*.json`。
- 驗證事件並以穩定順序處理。
- 已套用事件跳過，確保冪等。
- 不同欄位修改自動合併。
- 相同欄位相同值視為一致。
- 相同欄位不同值建立衝突，不覆蓋正式值。
- 衝突解決建立新事件。

使用小型 fixture 覆蓋單一事件、事件鏈、不同欄位平行修改、相同修改、競爭修改及事件撤銷。

提交檢查點：`Add review event replay and conflict model`

## 5. 階段三：原子儲存、本機草稿與專案安全

### 5.1 正式儲存服務

新增 `review_service.py`，實作：

1. 驗證目前 revision 及輸入欄位。
2. 產生事件暫存檔並 flush。
3. 開始 SQLite transaction。
4. 套用修改及事件索引。
5. 原子重新命名事件檔。
6. 提交 transaction。

加入 fault-injection 測試，模擬每個步驟失敗及程式中斷，確認不會遺失正式事件或留下無法重播的資料。

### 5.2 本機設定與草稿

新增 `config.py` 與 `draft_repository.py`：

- 從 Git `user.name` 取得預設編輯者。
- 首次啟動要求確認。
- 設定及草稿保存於 `%LOCALAPPDATA%`。
- 專案以規範化路徑 hash 分隔。
- 草稿記錄 spell、revision、欄位內容及更新時間。
- 正式儲存後刪除對應草稿。
- 若草稿 revision 已落後，顯示復原或比較選項，不直接套用。

### 5.3 單一實例與安全

新增：

- 專案層級單一實例鎖。
- 隨機 session secret。
- Host 與 Origin 驗證。
- CSRF token。
- 專案根目錄與 PDF 路徑限制。

測試第二個實例拒絕寫入、跨站修改請求失敗、任意路徑讀取失敗及 session 失效。

提交檢查點：`Add atomic review writes and local draft recovery`

## 6. 階段四：查詢 API 與全資料校對 API

### 6.1 查詢與佇列

建立 API：

- 健康狀態與專案摘要。
- 法術列表、分頁及排序。
- 中文名稱、英文名稱、ID 與正文搜尋。
- A–Z、狀態、來源、學派、職業、等級及問題篩選。
- 全部、未校對、需要校對、已校對、生成翻譯、同名及衝突佇列。

所有查詢涵蓋全部資料，不以 `needs_review` 限制可見或可編輯條目。

### 6.2 條目與修改

建立 API：

- 取得完整條目、關聯資料、核對狀態及歷程。
- 儲存／刪除／復原本機草稿。
- 儲存正式欄位修改。
- 完成或取消四類核對。
- 通過條目。
- 取得 PDF 指定頁面。

規則：

- 四類核對必須屬於同一 revision。
- 內容修改後舊核對失效。
- 通過一般條目會更新 `reviewed_revision_hash`。
- 通過生成翻譯同時更新 `translation_status = reviewed`。

### 6.3 同名與衝突 API

建立 API：

- 取得同名群組差異。
- 記錄合併、版本、名稱錯誤或暫不確定決定。
- 產生合併預覽。
- 確認合併並保留舊 ID。
- 取得及解決欄位衝突。

提交檢查點：`Add complete spell review API`

## 7. 階段五：三欄校對工作台

### 7.1 應用程式框架

建立共用版面、導覽、狀態訊息、錯誤頁與鍵盤焦點管理。視覺風格以長時間閱讀為主，桌面最小支援寬度 1280px。

不得把重要狀態只以顏色呈現。表單標籤、按鈕、錯誤訊息及鍵盤操作必須符合基本無障礙需求。

### 7.2 左側工作佇列

- 搜尋框與延遲查詢。
- 狀態及問題佇列。
- A–Z 與進階篩選。
- 分頁或虛擬化清單。
- 目前選取、未儲存草稿及衝突標記。
- 上一筆／下一筆快捷鍵。

### 7.3 中間編輯器

- 中英文名稱與完整規則欄位。
- 中文、英文及未加工正文。
- 自動草稿狀態。
- 四類核對控制。
- 儲存修改及通過並前往下一筆。
- revision 不一致、欄位驗證及未儲存導覽提示。

### 7.4 右側來源面板

- PDF 指定頁、上一頁與下一頁。
- 來源、解析信心及問題。
- 修改事件與核對歷程。
- 衝突及同名群組入口。

### 7.5 專用比較畫面

- 生成翻譯中英並排。
- 同名條目欄位 diff 及合併預覽。
- 三方衝突的基準值、事件 A、事件 B 與解決值。

端對端測試使用真實資料的唯讀複本，不直接改動正式 SQLite。

提交檢查點：`Build three-pane spell review workspace`

## 8. 階段六：重建、驗證與 Git Commit

### 8.1 完整重建

新增 `rebuild_service.py` 與 `scripts/rebuild_review_database.py`：

1. 驗證基準及事件。
2. 建立新的暫存 SQLite。
3. 套用 schema 與 migration。
4. 重播事件並產生衝突。
5. 重建 FTS5、JSON 及報告。
6. 執行完整驗證。
7. 原子替換正式衍生檔。

以 hash、資料筆數、ID 集合與抽樣內容證明增量更新及完整重建結果一致。

### 8.2 Git repository service

新增 `git_repository.py` 與 `commit_service.py`：

- 讀取 repository、分支、HEAD、merge／rebase 狀態。
- 檢查既有 staged changes。
- 顯示本批事件及實際檔案 diff 摘要。
- 只以明確路徑 stage 允許清單。
- 建立 `Review <count> spell entries` commit。
- 回傳 commit hash，不執行 push。

若存在非介面建立的 staged changes，停止並提示使用者處理。其他 unstaged changes 保留且不納入 commit。

### 8.3 Git 衍生檔衝突

偵測 SQLite、JSON 或報告的 Git conflict。確認基準及事件沒有未解決的文字衝突後，允許執行完整重建並將衍生檔標記為已解決。

測試使用臨時 repository 建立兩個協作者分支，涵蓋非衝突、同欄位衝突、二進位衝突重建及範圍受限 commit。

提交檢查點：`Add deterministic rebuild and safe Git commits`

## 9. 階段七：啟動器、診斷與非技術使用流程

### 9.1 Windows 啟動器

`launcher.py` 負責：

- 讓使用者選擇或自動辨識 spellbook repository。
- 執行啟動檢查。
- 選擇隨機 localhost port。
- 啟動服務並開啟瀏覽器。
- 顯示可理解的狀態視窗。
- 關閉時正常停止服務。

### 9.2 唯讀診斷頁

涵蓋：

- 資料或 PDF 遺失。
- schema／應用程式版本不相容。
- SQLite integrity 或外鍵失敗。
- 事件格式錯誤或尚未套用。
- Git merge conflict、detached HEAD 或非 repository。
- 編輯者資料缺失。

每項錯誤顯示原因、影響、可安全執行的修復及診斷輸出複製按鈕。檢查失敗時預設唯讀，不自動覆寫資料。

### 9.3 使用者文件

新增：

- 校稿者快速入門。
- GitHub Desktop clone／pull／commit／push 流程。
- 編輯者名稱設定。
- 衝突解決及衍生檔重建。
- 備份與還原。
- SmartScreen、SHA-256 驗證及離線使用說明。

提交檢查點：`Add Windows launcher and reviewer documentation`

## 10. 階段八：Windows 可攜式發行

### 10.1 PyInstaller

新增 `packaging/spellbook-reviewer.spec`，明確包含：

- Python runtime。
- FastAPI／Uvicorn／Jinja2。
- templates、static assets、JSON Schema 及 migration。
- 啟動器圖示及版本資訊。

發行 ZIP 不內嵌使用者資料庫、PDF 或 repository；執行時選擇現有專案資料夾。

### 10.2 CI 發行流程

新增 Windows GitHub Actions：

1. 安裝鎖定依賴。
2. 執行所有單元及整合測試。
3. 建置 EXE。
4. 執行封裝後 smoke test。
5. 建立可攜式 ZIP。
6. 產生 SHA-256 校驗檔。
7. 將 ZIP 與校驗檔上傳為 workflow artifact；正式 tag 時附加至 release。

第一版不執行程式碼簽章，但 workflow 保留簽章步驟掛載點。

提交檢查點：`Package portable Windows spell reviewer`

## 11. 最終驗收

### 資料驗收

- 基準資料為 2,360 筆，A–Z 齊全。
- SQLite、FTS5 及 JSON ID 集合一致。
- 目前 71 筆生成翻譯及 54 筆同名標記仍存在。
- 任何已通過後再修改的條目都會回到待校對。
- 全部事件可以在乾淨環境重播。

### 功能驗收

- 非問題條目也可完成四類核對。
- 草稿可在意外關閉後復原，但不進 Git。
- 一般、翻譯、同名及衝突流程皆可完成。
- 同名合併保留舊 ID 及歷史。
- Git commit 不包含範圍外檔案且不會 push。

### 發行驗收

- Windows 10／11 x64 無 Python 環境可執行。
- 斷網可完成搜尋、編輯、核對、PDF 檢視與本機 commit。
- ZIP 解壓後直接啟動，無需管理員權限。
- README 清楚說明未簽章 SmartScreen 警告。
- SHA-256 與發行檔一致。

## 12. 建議執行順序

依序完成階段一至八，不平行建立 UI 與事件核心。第一個可供實際資料驗證的里程碑是階段四；第一個可供校稿者試用的里程碑是階段五；Windows 發行只在重建及 Git 工作流程穩定後進行。
