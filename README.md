# 📊 数据分析 Agent

用自然语言提问，自动查数、画图、出报告的 Streamlit 应用。

上传一份 CSV / Excel，用中文提问「各品类销售额占比如何」，Agent 会自己看表结构、
写 SQL、执行、画图、给出结论，并把这次分析存进历史记录。

---

## 功能

| 能力 | 说明 |
|:---|:---|
| 📁 数据导入 | 上传 CSV / Excel 自动入库；支持逗号/分号/Tab/竖线分隔，支持 UTF-8 / GBK / GB18030 / BOM，支持中文表名与列名 |
| 💬 对话分析 | 自然语言提问 → Agent 自动 `inspect_table` → 生成 SQL 或 pandas 代码 → 执行 → 出图 → 给结论 |
| 📈 可视化 | 生成交互式 Plotly 图表（饼图/柱状图/折线图/散点图/直方图） |
| 🧾 报告导出 | 单条分析可导出为 Markdown 报告（含问题、结论、SQL、结果表） |
| 📜 历史记录 | 会话与任务记录落库，可回看、筛选、导出 |
| ⚙️ 系统配置 | 页面内配置模型 ID / API Key / Base URL，无需改代码 |

---

## 技术栈

| 组件 | 版本 | 用途 |
|:---|:---|:---|
| Python | 3.14 | 已实测兼容（含全部 C/Rust 扩展的 cp314 wheel） |
| Streamlit | 1.65.0 | Web UI |
| pandas | 3.0.6 | 数据处理 |
| Plotly | 7.1.0 | 交互式图表 |
| LangGraph + LangChain | 1.4.6 | Agent 编排 |
| SQLite | 内置 | 业务数据 + 会话日志 |
| DeepSeek | `deepseek-flash` | LLM（OpenAI 兼容接口） |

---

## 目录结构

```
PythonProject1/
├── config.py                    # 全局配置（DB 路径、模型参数）
├── main.py                      # Streamlit 入口页
├── requirements.txt
├── .env                         # API Key 等敏感配置（已在 .gitignore 中）
├── .env.example                 # 配置模板
│
├── database/
│   ├── __init__.py              # 空文件（只标记包，不放逻辑）
│   ├── init_db.py               # 建表：sessions / task_records / table_registry
│   ├── db_manager.py            # 数据访问层：连接、查询、写入、元数据
│   └── app.db                   # SQLite 数据库（业务数据 + 会话日志）
│
├── agent/
│   ├── state.py                 # Agent 图状态 + 通道归约器
│   ├── tools.py                 # 三个工具：看结构 / 跑 SQL / 跑 pandas
│   ├── prompt_templates.py      # 系统提示词、SQL 生成、报告提示词
│   └── agent_builder.py         # 构建 Agent、提取结果
│
├── utils/
│   ├── sql_safety_check.py      # SQL 安全校验（白名单 + 黑名单 + 单语句）
│   ├── data_loader.py           # CSV/Excel 解析、编码探测、入库
│   ├── visualizer.py            # Plotly Figure ↔ 可序列化 dict
│   └── report_generator.py      # Markdown 报告生成
│
├── pages/
│   ├── 01_数据分析Agent对话.py     # 主页面：聊天式分析
│   ├── 02_数据管理.py             # 上传、预览、删除数据表
│   ├── 03_历史任务记录.py          # 会话与任务记录、报告导出
│   └── 04_系统配置.py             # 模型配置
│
└── temp/                        # 临时缓存（.gitignore 已忽略）
```

---

## 快速开始

### 1. 安装依赖

```powershell
cd C:\Users\19674\PycharmProjects\PythonProject1
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果没有虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. 配置 API Key

复制 `.env.example` 为 `.env`，填入你的密钥：

```ini
DEEPSEEK_MODEL_ID="deepseek-flash"
DEEPSEEK_API_KEY="sk-你的密钥"
LLM_BASE_URL="https://api.deepseek.com"
LLM_TIMEOUT=60
```

> **也可以不改 `.env`**：启动后在「系统配置」页填写并保存，页面会写回 `.env`。

### 3. 初始化数据库

```powershell
.\.venv\Scripts\python.exe -c "from database.init_db import init_db; init_db()"
```

> 这一步是幂等的（全部 `CREATE TABLE IF NOT EXISTS`），`main.py` 每次启动也会调用一次。
> 表已存在时不会报错，也不会清空数据。

### 4. 启动

```powershell
.\.venv\Scripts\streamlit.exe run main.py
```

浏览器打开 <http://localhost:8501>。

---

## 使用流程

```
① 数据管理  →  上传 CSV / Excel，确认表结构与数据
                    ↓
② 对话分析  →  左侧选中数据表，用中文提问
                    ↓
③ 历史记录  →  回看分析过程，导出 Markdown 报告
```

**示例提问**：

- 各品类销售额占比如何？用饼图展示
- 每个月销售额的变化趋势
- 找出销售额最高的 5 个品类
- 分析一下这张表的字段含义和数据分布
- 计算销售额与利润的相关性

---

## 架构说明

### 一次提问的数据流

```
用户提问
   │
   ▼
┌──────────────────────────────────────────────┐
│  LangGraph Agent（create_agent）              │
│                                              │
│  模型决定调用哪个工具：                        │
│    · inspect_table    看字段名和类型           │
│    · run_sql_query    执行只读 SQL            │
│    · run_pandas_code  执行 pandas / 画图       │
│                                              │
│  工具通过 Command(update={...}) 把结果写回状态  │
└──────────────────────────────────────────────┘
   │
   ▼
state = { messages, result_table, last_sql,
          chart_spec, steps, last_error }
   │
   ▼
agent_result_to_page_dict()  →  { answer, table, figure, sql, steps }
   │
   ├─→ 页面渲染（结论 / 图表 / 结果表 / 执行过程）
   └─→ insert_task_record() 落库 → 历史页面可回看
```

### 三个关键设计

**1. 工具只能通过 `Command(update=...)` 更新状态**

LangGraph 的工具若直接写 `runtime.state["x"] = v`，改动会丢失——那只是一份快照。
必须返回 `Command(update={...})`，框架才会把它合并进图状态。
（`agent/tools.py` 里用 `_reply()` 辅助函数统一处理。）

**2. 所有会被工具写入的字段都带通道归约器**

模型有时会在同一步并行调用多个工具，两个工具都写 `result_table` 时，
LangGraph 的默认通道（LastValue）会抛 `InvalidUpdateError`。
`agent/state.py` 给每个字段挂了归约器：

| 字段 | 归约器 | 语义 |
|:---|:---|:---|
| `messages` | `add_messages` | 追加 |
| `steps` | `_add_steps` | 追加（执行轨迹要保留每一步） |
| `result_table` / `chart_spec` / `last_sql` / `last_error` | `_keep_last` | 覆盖（"最新状态"语义） |

**3. 图表走「dict 中转」，Figure 对象不进状态**

Plotly 的 `Figure` 不可 JSON 序列化，放进状态会污染 checkpoint。
所以 `run_pandas_code` 里先 `figure_to_spec(fig)` 转成 dict 存入 `chart_spec`，
页面再用 `spec_to_figure(spec)` 还原：

```
工具内 fig (Figure) ──figure_to_spec──> state["chart_spec"] (dict)
                                              │
页面 st.plotly_chart <──spec_to_figure────────┘
```

---

## 安全设计（三层纵深）

SQL 由 Agent 直接生成并执行，因此做了三层防护：

| 层 | 机制 | 作用 |
|:---|:---|:---|
| 第 1 层 | `utils/sql_safety_check.py` | 剥 Markdown 围栏与注释 → 单语句检查 → 白名单首词（仅 `SELECT`/`WITH`）→ 黑名单按词边界拦截写入关键词 → 危险函数（`readfile` / `writefile` / `load_extension`） |
| 第 2 层 | 只读连接 `?mode=ro` | **引擎级**拒绝写入。即使字符串校验被绕过，数据也改不了 |
| 第 3 层 | pandas 代码静态检查 | AST 白名单 + 受限 `__builtins__` + 禁用子串（`import` / `open` / `eval` 等） |

**第 2 层是最重要的一层**——它不依赖「判断是否正确」。
实测：绕过字符串校验直接执行 `WITH x AS (SELECT 1) DELETE FROM t`，
数据库返回 `attempt to write a readonly database`。

**为什么必须拦 `readfile`**：SQLite 的 `readfile()` 能把任意文件内容当查询结果返回。
若模型被诱导生成 `SELECT readfile('.../.env')`，你的 API Key 会被渲染到页面上。

> ⚠️ `run_pandas_code` 的静态检查**不是完备沙箱**。当前是「同进程 + AST 白名单 + 受限 builtins」，
> 能挡住绝大多数误操作，但理论上的属性穿透类逃逸仍可能存在。
> 生产环境建议改为子进程 / 容器隔离（见 `agent/tools.py` 末尾的 TODO）。

---

## 已知限制

| 限制 | 说明 |
|:---|:---|
| pandas 代码非完备沙箱 | 见上文「安全设计」，生产环境需容器隔离 |
| pandas 分析有 20 万行上限 | `tools.py` 的 `MAX_PANDAS_ROWS`。超限会明确报错并建议改用 SQL 聚合，**不会静默截断**（静默截断会让结果错误却不报错） |
| 报告只展示前 50 行 | `report_generator.MAX_REPORT_ROWS`，防止报告体积失控 |
| 历史预览最多 50 行 | `task_records.result_preview` 存的是截断后的 JSON |
| 复杂问题需要多轮工具调用 | 单次响应受模型速度限制，请耐心等待 |
| 不校验字段语义 | Agent 靠表结构和样例行推断字段含义，字段名过于隐晦时效果会下降 |

---

## 常见问题

**Q：页面提示「后端调用失败，已回退到预览模式」**

通常是 API Key 未配置或失效。到「系统配置」页检查，或确认 `.env` 中
`DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL_ID` / `LLM_BASE_URL` 三项都已填写。

**Q：Agent 说「当前没有选中的数据表」**

先到「数据管理」页上传数据。若已上传仍看不到，检查 `config.py` 的 `DB_PATH`
指向的文件是否与写入的是同一个（默认 `database/app.db`）。

**Q：上传分号分隔的 CSV，结果只有一列**

该问题已在 `pages/02_数据管理.py` 修复（`sep=None` 自动嗅探分隔符）。
若仍复现，请在「数据管理」页手动切换编码后重试。

**Q：图表没有显示**

先看页面是否给出「图表渲染失败」的具体原因。若模型没有生成图，
换一种问法（明确说「用饼图展示」/「画柱状图」）通常能触发。

**Q：修改代码后页面没变化**

Streamlit 有文件监听，保存后右上角会出现「Rerun」提示。
若未出现，按 `R` 键或重启服务。

**Q：如何调整分析风格 / 让回答更简洁**

编辑 `agent/prompt_templates.py` 里的 `SYSTEM_PROMPT`。

---

## 开发备忘

**新增一个工具**（供模型调用）：

1. 在 `agent/tools.py` 写函数并加 `@tool` 装饰器，
   参数里带 `runtime: ToolRuntime` 即可读取图状态；
2. **返回值必须是 `Command`**（用文件内已有的 `_reply()` 辅助函数），
   否则自定义 state 字段不会更新；
3. 把工具加进文件末尾的 `ANALYSIS_TOOLS` 列表；
4. 若工具要写新字段，先在 `agent/state.py` 声明**并挂归约器**。

**往数据库加一张记账表**：

1. 在 `database/init_db.py` 加 `CREATE TABLE IF NOT EXISTS`；
2. 把表名加进 `database/db_manager.py` 的 `SYSTEM_TABLES` 集合，
   否则它会被当作业务表喂给模型，污染 SQL 生成上下文；
3. 写入函数写在 `db_manager.py`，**页面里不要出现裸 SQL**。

**数据访问约定**：

| 场景 | 用哪个连接 |
|:---|:---|
| Agent 查询（`execute_sql`） | `get_readonly_connection()` 只读 |
| 内部写入、建表、记账 | `get_connection()` 读写 |
| 数据导入 | `get_connection()` 读写 |

---

## 依赖清单

```
streamlit==1.65.0
pandas==3.0.6
numpy==2.5.4
plotly==7.1.0
openpyxl==3.1.5
tabulate==0.10.0
langgraph
langchain==1.4.6
langchain-openai==1.7.2
python-dotenv==1.2.4
requests==2.34.2
```

`tabulate` 是 `pandas.to_markdown()` 的依赖，用于报告导出。
即使它缺失，`utils/report_generator.py` 也会退回内置的简易表格实现，功能不会中断。

---

## 说明

- `.env` 含密钥，已在 `.gitignore` 中，**不要提交到仓库**
- `database/app.db` 也已忽略（`*.db`），数据库由 `init_db()` 自动创建
- `temp/` 目录用于临时缓存，内容同样被忽略
