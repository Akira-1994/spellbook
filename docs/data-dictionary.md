# A-Z 法術資料字典

`data/spellbook.sqlite` 是擷取流程產生的種子資料庫，隨應用程式唯讀發佈；`data/export/spells.json` 是它的唯讀匯出。

應用程式第一次執行時，會把種子複製成使用者自己的資料庫（見文末「使用者資料庫」），之後所有編輯都寫在那一份。

## 核心狀態

- `not_required`：原文已有中文正文，不需補譯。
- `generated`：中文翻譯由自動流程生成（介面顯示為「機器翻譯」）。

`review_status`（`unreviewed`／`needs_review`／`reviewed`）是舊校對流程留下的欄位，應用程式已不再讀寫。

## `spells`

| 欄位 | 說明 |
| --- | --- |
| `id` | `spl_` 加 ULID 的永久公開 ID |
| `source_key` | 擷取流程用的穩定來源鍵 |
| `name_zh` | 中文標準名稱，必填 |
| `name_en` | 英文標準名稱，必填 |
| `alphabet` | PDF 的 A-Z 分區 |
| `variant_group_id` | 同名異效條目的關聯群組 |
| `review_status` | 舊校對流程欄位，已不使用 |

## `spell_entries`

保存 PDF 中一個具體規則版本。`school`、`subschool`、`components`、`casting_time`、`range_text`、`target_text`、`area_text`、`effect_text`、`duration`、`saving_throw`、`spell_resistance` 對應原條目的規則欄位。

| 欄位 | 說明 |
| --- | --- |
| `description_zh` | 中文效果正文；英文條目使用生成翻譯 |
| `description_en` | PDF 中原有的英文正文；沒有時為空 |
| `raw_text` | 未加工原始條目文字 |
| `pdf_page_start/end` | PDF 實際起訖頁碼 |
| `parse_confidence` | 0 至 1 的解析信心 |
| `translation_status` | 翻譯狀態 |
| `translation_method` | 翻譯方法；自動初稿為 `contextual_glossary` |

## 關聯表

- `spell_names`：別名、其他譯名及拼寫變體。
- `classes`、`spell_levels`：職業與法術等級。
- `sources`、`spell_sources`：來源書籍。
- `descriptors`、`spell_descriptors`：法術描述符。
- `extraction_issues`：解析與翻譯問題。
- `spell_search`：可重建的 FTS5 全文搜尋索引。

`duplicate_english_name` 表示英文標準名稱相同、但尚未人工判定應合併或視為規則版本的條目。第一版保留各自的永久 ID，不自動刪除或合併。

## 翻譯來源目錄

`data/translations.generated.json` 保存 71 筆自動翻譯初稿及其 `provider`。這些條目在資料庫中皆為 `translation_status = generated`。

## 使用者資料庫

位置：`%LOCALAPPDATA%\Spellbook\spellbook.sqlite`（可用環境變數 `SPELLBOOK_DATA_DIR` 改變資料夾）。由種子複製而來，啟動時套用 migration 3：

- 刪除舊校對流程的空資料表：`review_events`、`review_checks`、`review_conflicts`、`duplicate_decisions`。
- `spells.edited_at`：內容與原始內容不同時為最後一次修改時間；與原始內容相同時為 `NULL`。
- `spell_versions`：每次編輯、還原版本或還原原始內容前，先保存被取代的內容。每個法術只保留最近 5 筆。

| 欄位 | 說明 |
| --- | --- |
| `spell_id` | 所屬法術 |
| `snapshot_json` | 被取代時的可編輯欄位內容 |
| `content_saved_at` | 這份內容當初儲存的時間；`NULL` 代表它是原始內容 |
| `replaced_at` | 被取代的時間 |
| `replaced_by` | 取代它的動作：`edit`、`rollback` 或 `restore_original` |

「還原成原始內容」一律從種子資料庫讀取，所以原始內容永遠不會因為超過 5 個版本而遺失。

## 學派與職業正規化

原始資料的 `school` 有約 100 種寫法、`classes` 有 207 種職業寫法（含領域、錯字與擷取錯誤）。正規化結果由人工審閱的 [taxonomy-review.xlsx](../data/taxonomy/taxonomy-review.xlsx) 決定，再由 `scripts/import_taxonomy.py` 轉成 `spellbook/taxonomy.json`。應用程式每次啟動都依它重建下列衍生表（migration 4），不修改原始文字：

- `class_catalog`：113 個標準職業／領域（`kind` 為 `class`、`domain` 或 `other`）。「術士/法師」這類寫法同時對應術士與法師。
- `spell_class_levels`：法術在各標準職業的等級。15 個職業與等級黏在一起或混入其他書版本的法術，改用審閱過的正確等級。
- `spell_schools`：法術的標準學派（`abjuration`、`conjuration`、`divination`、`enchantment`、`evocation`、`illusion`、`necromancy`、`transmutation`、`universal`，無法判斷時為 `unclassified`）。跨學派法術會有多筆。原文把 Evocation 譯成「招魂」、Abjuration 譯成「放棄」、Enchantment 譯成「魅力」，規則已將它們歸回正確學派。使用者編輯學派文字時會立即重新計算。

修改分類的流程：編輯活頁簿 → 執行 `.venv\Scripts\python.exe -m scripts.import_taxonomy` → 重新啟動應用程式。

## 種子資料修正

首次擷取有兩類問題，已用目前的擷取程式修正種子資料：

- **欄位混進說明**：欄位名稱被換行切斷、用分號或其他譯法（作用距離、施展時間、時效…），或正文表格排在欄位之間時，後續欄位全被當成說明。`scripts/repair_fields.py` 依每筆的 `raw_text` 重新解析，修正了 310 個法術，逐項變更見 `reports/field-repair.csv`。
- **漏掉的法術**：等級行寫成「法術等級」、沒有標籤或被換行切斷的法術沒被偵測到，內容留在前一個法術的說明尾端；學派行帶英文括號時被誤當成標題（名稱變成「變化系」「幻術系」）。`scripts/add_missing_spells.py` 比對重新擷取的結果，新增 13 個法術、更正 3 個名稱，並清掉前一個法術說明裡夾帶的內容。

使用者資料庫是首次執行時的種子副本，不會自動得到這些修正，因此 `spellbook/seed_fixes.json` 依序記錄每批修正，應用程式每次啟動時套用：欄位與名稱只在仍是舊值時才更新（使用者編輯過的保留），新增的法術從種子複製。

