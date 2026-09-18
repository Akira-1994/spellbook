# A-Z 法術資料庫設計

日期：2026-09-18

## 1. 目標

將 `spellbook_doc_v1.1.pdf` 第 192 至 1144 頁的 A-Z 法術詳細資料，轉換為可離線查詢、維護與執行 CRUD 的結構化資料庫。

SQLite 是唯一可信資料來源。JSON 只由 SQLite 自動匯出，不接受直接修改後再回寫資料庫，避免雙向同步衝突。

第一版優先完成資料基礎，不包含職業法術清單、領域清單、第 1145 至 1148 頁的降咒擴充規則，以及第 1149 頁以後的召喚附錄。

## 2. 成功條件

- A-Z 每個字母分區至少包含一筆資料。
- 每筆法術具有永久且唯一的公開 ID。
- 每筆法術同時具有非空的中文標準名稱與英文標準名稱。
- 英文原文條目具備完整中文翻譯，並保留英文原文。
- 每筆條目可以追溯到 PDF 起訖頁碼與未加工原文。
- 無法可靠解析的內容仍會入庫，不得靜默丟棄。
- SQLite 與 JSON 匯出的法術 ID 集合完全相同。
- 所有外鍵、唯一約束、JSON Schema 與自動測試通過。
- 產出解析統計、待校對清單與翻譯校對清單。

## 3. 來源範圍

| 區段 | PDF 頁碼 | 是否納入第一版 |
| --- | ---: | --- |
| A-Z 法術詳細資料 | 192-1144 | 是 |
| 降咒擴充規則 | 1145-1148 | 否 |
| 召喚術與召喚生物附錄 | 1149-1162 | 否 |

PDF 頁碼使用檔案實際頁序，不使用印刷頁碼。

## 4. 儲存架構

```text
PDF 原文
   -> 逐頁文字擷取
   -> 條目切分與欄位解析
   -> 英文條目翻譯
   -> 正規化、去重與驗證
   -> SQLite 主資料庫
      -> FTS5 全文搜尋索引
      -> JSON 自動匯出
      -> 校對與統計報告
```

SQLite 使用外鍵、交易、唯一約束與 migration 管理結構。所有寫入流程必須以交易執行；失敗時回滾整批變更。

## 5. 唯一 ID

`spells.id` 使用字串格式 `spl_` 加 ULID，例如 `spl_01K5QY4NVK0G4P6JYH8WQ2T9XM`。

- ID 在首次建立時產生，之後永久不變。
- 中文名稱、英文名稱、來源或正文修改時，不重新產生 ID。
- 匯入程式必須先比對既有來源定位與內容指紋，盡可能沿用既有 ID。
- 自增整數可作內部排序用途，但不得成為公開識別值。

## 6. 資料模型

### 6.1 `spells`

保存法術的穩定身分：

- `id TEXT PRIMARY KEY`
- `name_zh TEXT NOT NULL`
- `name_en TEXT NOT NULL`
- `alphabet TEXT NOT NULL`
- `variant_group_id TEXT NULL`
- `review_status TEXT NOT NULL`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

`alphabet` 僅允許 A-Z。`review_status` 僅允許 `unreviewed`、`needs_review`、`reviewed`。

### 6.2 `spell_entries`

保存 PDF 中實際出現的一個規則版本：

- `id TEXT PRIMARY KEY`
- `spell_id TEXT NOT NULL`
- `school TEXT NULL`
- `subschool TEXT NULL`
- `components TEXT NULL`
- `casting_time TEXT NULL`
- `range_text TEXT NULL`
- `target_text TEXT NULL`
- `area_text TEXT NULL`
- `effect_text TEXT NULL`
- `duration TEXT NULL`
- `saving_throw TEXT NULL`
- `spell_resistance TEXT NULL`
- `description_zh TEXT NOT NULL`
- `description_en TEXT NULL`
- `additional_costs TEXT NULL`
- `pdf_page_start INTEGER NOT NULL`
- `pdf_page_end INTEGER NOT NULL`
- `raw_text TEXT NOT NULL`
- `parse_confidence REAL NOT NULL`
- `review_status TEXT NOT NULL`
- `translation_status TEXT NOT NULL`
- `translation_method TEXT NULL`

`translation_status` 僅允許 `not_required`、`generated`、`reviewed`。英文原文條目初次翻譯後使用 `generated`，並將 `review_status` 設為 `needs_review`。

### 6.3 關聯表

- `spell_names`：其他譯名、舊譯、英文拼寫或大小寫變體。
- `classes`：標準化職業名稱。
- `spell_levels`：法術、規則版本、職業與法術等級的多對多關係。
- `sources`：標準化來源書籍名稱。
- `spell_sources`：法術版本與來源書籍的多對多關係。
- `descriptors`：火焰、善良、影響心靈等描述符。
- `spell_descriptors`：法術版本與描述符的多對多關係。
- `extraction_issues`：解析、翻譯、去重及欄位衝突。
- `schema_migrations`：資料庫結構版本。

### 6.4 全文搜尋

建立 FTS5 虛擬表 `spell_search`，索引：

- 中文標準名稱
- 英文標準名稱
- 別名
- 中文正文
- 英文原文
- 學派與描述符
- 來源書籍

FTS5 索引由主資料表重建，不作為獨立可信資料來源。

## 7. 名稱與版本規則

- `name_zh` 與 `name_en` 分欄儲存且皆為必填。
- 其他譯名與拼寫變體存入 `spell_names`，不得覆蓋標準名稱。
- 英文名稱相同且規則實質相同時，使用同一筆 `spells`，來源以多對多關係保存。
- 英文名稱相同但規則效果不同時，建立不同的 `spells.id`，並使用相同 `variant_group_id` 關聯。
- 去重不得只依賴中文名稱。
- 名稱正規化可以移除多餘空白及統一大小寫以供比對，但必須保留原始顯示文字。

## 8. 擷取流程

1. 逐頁擷取第 192 至 1144 頁文字，保留頁碼邊界。
2. 以字母標題、法術名稱、學派行及 `等級` 或 `Level` 欄位辨識條目起點。
3. 將跨頁正文拼接為同一條目。
4. 統一全形與半形標點，以及同義欄位標籤。
5. 解析名稱、學派、子學派、描述符及規則欄位。
6. 解析職業與法術等級的多值關係。
7. 解析一個或多個來源書籍。
8. 執行候選去重與同名異效判斷。
9. 寫入暫存資料後執行驗證。
10. 在單一交易中更新正式 SQLite 資料庫。
11. 重建 FTS5 索引並匯出 JSON。

欄位標籤正規化至少涵蓋：

- `法術成分`、`成分`、`Components`
- `施法時間`、`Casting Time`
- `距離`、`Range`
- `目標`、`Target`
- `區域`、`範圍`、`Area`
- `效果`、`Effect`
- `持續時間`、`Duration`
- `豁免檢定`、`Saving Throw`
- `法術抗力`、`Spell Resistance`

## 9. 英文條目翻譯

只有英文內容的法術條目必須完整翻譯：

- 法術名稱
- 學派、子學派與描述符
- 規則欄位和值
- 效果正文
- 材料、法器、經驗值、祭獻或其他代價

翻譯優先使用同一 PDF 已出現的職業、學派、距離、豁免、法術抗力及規則術語。英文原文完整保存在 `description_en` 與 `raw_text` 中，不被中文翻譯覆蓋。

初次翻譯標記：

- `translation_status = generated`
- `translation_method = contextual_glossary`
- `review_status = needs_review`

人工確認後才可改為 `translation_status = reviewed`。

## 10. 異常處理

解析器不得因單一條目異常而丟棄整筆資料。遇到無法判定的欄位時：

- 保存完整 `raw_text`。
- 保存 PDF 起訖頁碼。
- 將可辨識欄位照常入庫。
- 在 `extraction_issues` 記錄問題類型、嚴重度與說明。
- 將條目設為 `needs_review`。

高風險情況包括跨頁名稱切割、缺少 `等級` 欄、欄位順序異常、同名異效、混合中英文正文及原始排版錯字。

## 11. JSON 匯出

`data/export/spells.json` 是 SQLite 的完整巢狀唯讀匯出，包含法術、規則版本、別名、職業等級、來源與描述符。

- JSON 不作為 CRUD 寫入來源。
- 匯出結果使用 `data/export/spells.schema.json` 驗證。
- SQLite 與 JSON 的法術 ID 集合及筆數必須一致。
- 所有 JSON 檔使用 UTF-8。

## 12. 驗證與測試

### 結構驗證

- 外鍵檢查通過。
- 所有 `spells.id` 唯一且符合 `spl_` 加 ULID 格式。
- 中英文標準名稱均非空。
- PDF 起頁不大於迄頁，且介於 192 至 1144。
- `alphabet` 與英文名稱首字母一致；例外必須記錄原因。

### 完整性驗證

- A-Z 全部分區均有條目。
- 每筆法術至少有一個 `spell_entries`。
- 每筆條目都保留 `raw_text`。
- 所有解析問題均出現在報告中。
- 重新執行擷取時，已存在法術盡可能沿用原 ID。

### 抽樣驗證

- 每個字母至少抽查一筆。
- 抽查跨頁條目。
- 抽查同名異效條目。
- 抽查多來源、多職業等級及多描述符條目。
- 逐筆檢查所有英文原文條目的翻譯完整性與術語一致性。

## 13. 交付檔案

```text
data/
  spellbook.sqlite
  export/
    spells.json
    spells.schema.json
scripts/
  extract_pdf.py
  build_database.py
  translate_entries.py
  export_json.py
  validate_data.py
reports/
  extraction-summary.json
  needs-review.csv
  translation-review.csv
tests/
  test_parser.py
  test_database.py
  fixtures/
docs/
  data-dictionary.md
```

## 14. 非目標

第一版不包含：

- 離線查找應用的使用者介面。
- 職業法術清單與詳細法術的交叉校驗。
- 領域清單正規化。
- 降咒擴充規則與召喚附錄。
- 多人同步、帳號、權限或遠端伺服器。

這些項目可在 A-Z 法術資料庫穩定後另行設計。
