# Spellbook 校對工作台使用說明

## 開始使用

1. 若只在單機校對，將 `SpellbookReviewer-windows-x64.zip` 完整解壓縮到一般資料夾。若要和其他人透過 Git 協作，先用 GitHub Desktop clone `spellbook` repository，再把 ZIP 內容解壓到該 repository 根目錄（可看見 `.git`、`data` 與 PDF 的那一層）。不要直接在壓縮檔內執行。
2. 雙擊 `SpellbookReviewer.exe`。校稿者不需要安裝 Python、Node.js 或其他開發工具；Git 協作模式需安裝 GitHub Desktop 或 Git。
3. 瀏覽器會自動開啟本機工作台。首次使用請確認校對者名稱；此名稱會留在每一筆校對事件中。
4. Windows SmartScreen 若顯示「Windows 已保護您的電腦」，請先核對 ZIP 的 SHA-256，再選「其他資訊」與「仍要執行」。目前程式未簽章，因此這是預期提示。

程式只監聽 `127.0.0.1`，校對內容不會送到外部網路。Git pull/push 仍需由 GitHub Desktop 或命令列執行。

## 每筆法術的校對流程

1. 從左欄搜尋或用狀態、問題、A–Z 篩選選取條目。
2. 在中欄比對中英文名稱、規則與正文；右欄顯示原始 PDF、來源、問題與歷程。
3. 輸入會自動保存為本機草稿。草稿不會進入 Git；按「儲存修改」才會建立正式不可變事件。
4. 依序確認「名稱、規則、正文、來源」四項。任何內容修改都會使舊核對失效。
5. 四項完成後，按「通過並前往下一筆」。生成翻譯也會同時改為人工已審。

同名條目可從右欄開啟比較，判定為合併、不同版本、名稱錯誤或暫不確定。合併只會把次要記錄標成 `merged` 並指向保留 ID，不刪除舊 ID 或歷史。

## 與 Git 協作

此功能只有在程式所在專案是 Git checkout 時可用。建議非技術協作者用 GitHub Desktop clone 專案、執行校對，建立 commit 後回到 GitHub Desktop 檢查與 push。

按頂端「建立校對 Commit」會先預覽檔案，只加入：

- `reviews/changes/*.json`
- `data/spellbook.sqlite`
- `data/export/spells.json`
- `reports/*`

工具不會加入其他工作檔，也不會自動 push。若暫存區已有檔案、Git 正在合併，或作者資料未設定，工具會停止。Commit 建立後，請在 GitHub Desktop 檢查並自行 push。

## 備份與疑難排解

- 請定期 push `reviews/changes`；它們與基準資料是可重建的校對歷史。
- 程式若提示已有另一個實例，請切換到先前開啟的瀏覽器或關閉舊程式。
- 若頁面顯示 revision 已落後，請重新載入條目再比較；系統不會以最後寫入強行覆蓋。
- ZIP 的 `.sha256` 檔可用 PowerShell 驗證：`Get-FileHash .\SpellbookReviewer-windows-x64.zip -Algorithm SHA256`。
