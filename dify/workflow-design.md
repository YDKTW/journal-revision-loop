# Dify Workflow 設計：期刊稿件審查與修訂迴圈

> 應用類型：**工作流（Workflow）**，請新建一個，不要改現有的聊天流。
> 節點名稱以你介面上的中文為準（循環、條件分支、程式碼執行、變數賦值等）。
> 我無法連到你的 Dify 確認細節，下列設定若與你的版本介面不同，以原則為準，名稱可能略有出入。

---

## 一、先決條件

1. 已安裝並設定模型供應商：**Anthropic（Claude）**、**Google（Gemini）**、**OpenAI**。
2. 稿件已去識別化。稿件會送往三家外部服務；若涉及 IRB 受試者資料或匿名審查資訊，務必先處理。
3. 修稿者 Skill 全文（`journal-manuscript-reviser/SKILL.md`，去除最上方 frontmatter）與審查標準（`review-rubric.md`）已備妥，會貼進 Dify。

---

## 二、環境變數（存放長文字常數）

在工作流的 ENV（環境變數）新增：

| 名稱 | 內容 |
|---|---|
| `RUBRIC` | `review-rubric.md` 全文 |
| `REVISER_SKILL` | `SKILL.md` 本文（去除 frontmatter）＋下方「自動化模式」一行 |
| `JOURNAL_RULES` | 目標期刊規範（可改為開始節點輸入） |

`REVISER_SKILL` 結尾加上：「**本次為自動化模式**，請依『自動化模式的輸出包裝』輸出四個標籤。」

若環境變數對長度有限制而放不下，可改放在「模板轉換」節點內，當作常數輸出。

---

## 三、節點總覽

```
[開始]
  ↓
[文件提取器]（若上傳檔案；直接貼文字則略過）
  ↓
[循環]  ← 循環變數：見第四節
  │  ┌─────────────────────────────────────────────┐
  │  │ ① Gemini 審查（LLM）  ┐ 平行                   │
  │  │ ② GPT 審查（LLM）     ┘                        │
  │  │ ③ 程式碼：解析並判斷（code-node-1）             │
  │  │ ④ 變數賦值 A：decision、streak、prior 等        │
  │  │ ⑤ 條件分支：decision == "continue" ?           │
  │  │     是 → ⑥ Claude 修稿（LLM）                   │
  │  │          ⑦ 程式碼：解析修訂（code-node-2）       │
  │  │          ⑧ 變數賦值 B：稿件、輪次、decision      │
  │  │     否 → （不做事，循環將依退出條件結束）         │
  │  └─────────────────────────────────────────────┘
  ↓
[人工輸入]（最終審閱）
  ↓
[結束]
```

**設計理由**：審查 → 判斷 → 才修稿。若第 N 輪審查已達停止條件，就不再多修一輪，所以最終稿件就是最後一次被審查的版本，審查分數與稿件一致。

---

## 四、開始節點與循環變數

### 開始節點輸入

| 變數 | 型別 | 說明 |
|---|---|---|
| `manuscript` | 段落（長文字） | 稿件全文，含參考文獻；重要區塊先包上 `<!--LOCK-->…<!--/LOCK-->` |
| `paper_type` | 下拉選單 | quantitative／qualitative／theoretical／teaching_practice／mixed |
| `max_rounds` | 數字 | 預設 4 |
| `required_pass_rounds` | 數字 | 預設 2（想省成本可設 1） |

### 循環節點的「循環變數」（初始值）

| 變數 | 型別 | 初始值 |
|---|---|---|
| `current_manuscript` | String | `manuscript` |
| `round_no` | Number | 1 |
| `decision` | String | `continue` |
| `prev_total` | Number | 0 |
| `prev_major` | Number | 0 |
| `pass_streak` | Number | 0 |
| `stall_streak` | Number | 0 |
| `history` | String | 空字串 |
| `prior_gemini` | String | `[]` |
| `prior_gpt` | String | `[]` |
| `response_summary` | String | 空字串 |
| `last_response_table` | String | 空字串 |
| `last_marked` | String | 空字串 |
| `last_flags` | String | 空字串 |
| `last_reason` | String | 空字串 |

**退出條件**：`decision` 不等於 `continue`。
**最大循環次數**：設為比 `max_rounds` 大一些（例如 8），僅作保險；真正的輪數上限由程式碼節點判斷。

---

## 五、各節點設定

### ① Gemini 審查（LLM 節點）
- 模型：Gemini 高階模型；temperature 0.2–0.3。
- System：`reviewer-gemini.md` 的 System prompt 區塊，其中 `{{rubric}}` 與 `{{paper_type}}` 改用 Dify 變數插入。
- User：該檔的 User prompt 模板。變數對應：

| 模板變數 | Dify 來源 |
|---|---|
| `{{round}}` | 循環變數 `round_no` |
| `{{paper_type}}` | 開始節點 `paper_type` |
| `{{journal_specific_rules}}` | 環境變數 `JOURNAL_RULES` |
| `{{prior_issues_gemini}}` | 循環變數 `prior_gemini` |
| `{{revision_response_summary}}` | 循環變數 `response_summary` |
| `{{manuscript}}` | 循環變數 `current_manuscript` |
| `{{rubric}}` | 環境變數 `RUBRIC` |

- 若節點有「結構化輸出」選項，可貼入 `reviewers/review-schema.json`；沒有也無妨，程式碼節點會驗證。
- 啟用節點的「失敗重試」，處理暫時性連線錯誤。

### ② GPT 審查（LLM 節點）
- 同上，換成 `reviewer-gpt.md`，`{{prior_issues_gpt}}` 對應 `prior_gpt`。
- ① 與 ② 都從循環起點連出，會平行執行；③ 同時接收兩者。

### ③ 程式碼執行：解析並判斷
- 貼入 `code-node-1-decide.py`，語言 Python 3。
- 輸入變數對應：

| 程式變數 | 來源 |
|---|---|
| `gemini_raw` | ① 的輸出文字 |
| `gpt_raw` | ② 的輸出文字 |
| `paper_type` | 開始節點 |
| `round_no`、`prev_total`、`prev_major`、`pass_streak`、`stall_streak`、`history` | 同名循環變數 |
| `max_rounds`、`required_pass_rounds` | 開始節點 |

- 輸出變數型別：`decision`、`reason`、`prior_gemini`、`prior_gpt`、`issues_for_reviser`、`history_next` 為 String；`round_total`、`major_count`、`pass_streak`、`stall_streak` 為 Number。

### ④ 變數賦值 A（在條件分支之前）
把下列循環變數設為節點 ③ 的輸出：
`decision`←`decision`、`last_reason`←`reason`、`pass_streak`、`stall_streak`、`prev_total`←`round_total`、`prev_major`←`major_count`、`prior_gemini`、`prior_gpt`、`history`←`history_next`。

### ⑤ 條件分支
IF `decision` 等於 `continue` → 修稿路徑；ELSE → 空分支。

### ⑥ Claude 修稿（LLM 節點）
- 模型：Claude 高階模型；temperature 0.3。
- **最大輸出 token 要調高**，修訂稿全文加對照表很長，被截斷時第二支程式會偵測並作廢。
- System：環境變數 `REVISER_SKILL`。
- User：

```
【輪次】{{round_no}}
【論文類型】{{paper_type}}
【審查標準】
{{RUBRIC}}

【兩位審查者的未解決意見（JSON）】
{{issues_for_reviser}}

【當前稿件】
{{current_manuscript}}
```

### ⑦ 程式碼執行：解析修訂
- 貼入 `code-node-2-parse-revision.py`。
- 輸入：`reviser_raw` ← ⑥ 的輸出；`old_manuscript` ← 循環變數 `current_manuscript`。
- 輸出皆為 String。

### ⑧ 變數賦值 B
- `current_manuscript` ← `manuscript_clean`
- `last_marked` ← `manuscript_marked`
- `last_response_table` ← `response_table`
- `response_summary` ← `dispositions_summary`
- `last_flags` ← `flags`
- `decision` ← `override_decision`
- `round_no` ← `round_no` ＋ 1

---

## 六、循環結束後

### 人工輸入節點
顯示以下內容供你最終審閱（皆來自循環變數）：
- 結束原因：`decision` 與 `last_reason`
- 逐輪紀錄：`history`
- 程式警示：`last_flags`
- 最終稿件：`current_manuscript`

### 結束節點輸出
`final_manuscript`、`decision`、`history`、`last_response_table`、`last_marked`（帶修改標記版，方便比對）、`prior_gemini` 與 `prior_gpt`（最後仍未解決的意見）。

---

## 七、停止判斷與保護機制對照

| 機制 | 實作位置 | 行為 |
|---|---|---|
| 連續通過 | 程式碼 ③ | `stop_pass` |
| 輪數上限 | 程式碼 ③ | `stop_limit` |
| 停滯 | 程式碼 ③ | `stop_stalled` |
| 審查者分歧 | 程式碼 ③ | `stop_human` |
| 審查輸出格式錯誤 | 程式碼 ③ | `stop_human` |
| 鎖定區塊被改 | 程式碼 ⑦（逐字比對） | 作廢本輪修訂，`stop_human` |
| 輸出被截斷 | 程式碼 ⑦（長度 < 70%） | 作廢本輪修訂，`stop_human` |
| 分數計算 | 程式碼 ③ 重算 | 不採信模型自己算的 `weighted_total` |

所有「停止」都不是結束就算數，而是交給你最後審閱。

---

## 八、建議的試跑步驟

1. 先貼一段約兩千字的短稿，`max_rounds` 設 1，確認 ①②③ 都能輸出並解析。
2. 在各節點的「最後一次運行」檢視輸入輸出，特別看 JSON 是否被程式碼節點成功解析。
3. 設 `max_rounds` 為 2，確認修稿路徑（⑥⑦⑧）與輪次遞增。
4. 在稿件加一段 `<!--LOCK-->` 區塊，測試是否被保留。
5. 最後才用完整稿件跑 4 輪，並與真人審查意見比對。

---

## 九、已知限制

- **程式碼節點的字串長度有上限**（預設約 8 萬字元，部署時可由管理員調整）。中文稿件通常不超過，但若很長需調整設定。
- **權重加總**：rubric 第三節中，質性與教學實踐類型調整後的權重加總超過 100%，程式碼會自動正規化。建議之後回頭修正 rubric 的數字。
- **稿件中的 `<!--LOCK-->` 標記**會被審查者看到，無害，但匿名審查的最終稿送出前記得移除。
- **「人工輸入」節點每輪暫停**：本設計只在最後暫停。若你希望每輪都確認，需要確認該節點能否置於循環內；不行的話，可改為每次只跑一輪，由你手動接續。
- **費用**：每輪最少 3 次大模型呼叫，稿件越長越貴。建議先用 `max_rounds` 1–2 估算。
