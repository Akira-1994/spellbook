# Spellbook 法術書

從 `spellbook_doc_v1.1.pdf` 整理出的 2,405 個法術（A–Z 詳述與召喚附錄），做成可以離線查詢、自行編輯的個人法術書。

## 使用方式

1. 下載 `Spellbook.exe`，放在任何資料夾。
2. 雙擊執行，程式會自動用預設瀏覽器開啟法術書。不需要安裝 Python，也不需要網路。
3. 用左側的搜尋或字母瀏覽法術；按「編輯」可以修改任何欄位。

- **篩選**：可以依學派（以不同顏色標示，可複選）、職業或領域、等級 0–9（可複選）篩選，也能和搜尋、字母一起使用。選了職業時，等級指的是該職業的等級；在法術頁點等級標籤（例如「法師 3」）可以直接篩出同職業同等級的法術。篩選條件會自動記住。
- **版本紀錄**：每次儲存前，修改前的內容會存進版本紀錄。每個法術保留最近 5 個版本，可以預覽差異並還原；還原本身也會留下紀錄，所以可以再還原回來。
- **還原成原始內容**：隨時可以把法術回復成原書擷取的內容，這份原始內容不佔版本名額，永遠不會遺失。
- **關閉程式**：按右上角「結束程式」會立即結束；或直接關掉瀏覽器分頁，所有法術書分頁都關閉約 15 秒後程式會自動結束（重新整理頁面不會關閉程式）。再次開啟 `Spellbook.exe` 即可；程式已在執行時，會直接開啟既有的頁面。
- **資料位置**：你的修改存在 `%LOCALAPPDATA%\Spellbook\spellbook.sqlite`。更新 `Spellbook.exe` 不會影響這份資料；要備份就複製這個檔案（先關閉程式）。刪除這個資料夾，就會回到全新的原始法術書。

第一次執行未簽章的 exe 時，Windows SmartScreen 可能會顯示警告，點「其他資訊 → 仍要執行」即可。

## 開發

需要 Python 3.12 以上：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\spellbook.exe
```

開發時可以用環境變數 `SPELLBOOK_DATA_DIR` 把使用者資料庫指到其他資料夾，避免動到自己平常使用的資料。

執行測試：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

應用程式圖示（exe 與網頁 favicon）由 `packaging/make_icon.py` 繪製，修改設計後重新產生：

```powershell
.\.venv\Scripts\python.exe packaging\make_icon.py
```

打包成單一執行檔（輸出 `dist/Spellbook.exe` 與 `.sha256`）。打包使用 Nuitka 把程式編譯成原生執行檔，需要先安裝 Visual Studio 2022 Build Tools 的「使用 C++ 的桌面開發」工作負載；第一次打包約需數分鐘：

```powershell
.\packaging\build-portable.ps1
```

## 資料

- `data/spellbook.sqlite`：擷取流程產生的種子資料，隨 exe 唯讀發佈，也是「還原成原始內容」的來源。
- `%LOCALAPPDATA%\Spellbook\spellbook.sqlite`：使用者自己的資料庫，第一次執行時由種子複製。
- `scripts/`：從 PDF 擷取、翻譯、建庫與驗證的流程工具，只在重新產生種子資料時使用。
- `data/taxonomy/taxonomy-review.xlsx`：人工審閱的學派與職業對照，經 `scripts/import_taxonomy.py` 轉成 `spellbook/taxonomy.json`。
- 欄位說明見 [資料字典](docs/data-dictionary.md)。

舊版的多人校對工作台保留在 git tag `reviewer-final`。
