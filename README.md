# Spellbook

從 `spellbook_doc_v1.1.pdf` 整理出的 2,360 筆 A–Z 法術資料，以及供人工校對使用的離線工作台。

## 開發模式

需要 Python 3.12 以上：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\spellbook-reviewer.exe
```

啟動器只監聽 `127.0.0.1` 的隨機連接埠並自動開啟瀏覽器。正式修改會更新 SQLite 並在 `reviews/changes` 建立不可變事件；未完成草稿保存在 `%LOCALAPPDATA%`，不會提交至 Git。

執行測試：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Windows 可攜式版本

```powershell
.\packaging\build-portable.ps1
```

輸出位於 `dist/SpellbookReviewer-windows-x64.zip`，並同時產生 SHA-256 檔。校稿者不需安裝開發環境；若要使用內建 Git commit，請將 ZIP 內容解壓到 Git checkout 的專案根目錄。詳見 [校對者使用說明](docs/REVIEWER-GUIDE.md)。

## 資料權責

- 不可變基準：`data/baseline/spells-v1.json`
- 不可變人工校對事件：`reviews/changes/*.json`
- 本機 CRUD／搜尋投影：`data/spellbook.sqlite`
- 衍生匯出：`data/export/spells.json` 與 `reports/*`

跨分支協作以「基準 + 事件」為合併依據，不直接手工合併 SQLite 二進位檔。
