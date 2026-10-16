# ============ utils/sql_safety_check.py ============
# SQL 安全校验：只允许只读 SELECT / WITH 查询
#
# 设计要点（顺序不可调换）：
#   1. 剥 Markdown 围栏 + 结尾分号     —— LLM 实测会输出 ```sql 围栏
#   2. 剥 SQL 注释                   —— 注释既是绕过手段也是误判来源
#   3. NONE 哨兵                     —— 模型判定无法生成 SQL 时返回
#   4. 单语句检查                     —— 堵住 ";DROP TABLE t"
#   5. 白名单首词                     —— 按词切分，不用 startswith
#   6. 黑名单词边界                   —— 不用"前后空格"假设
#   7. 危险函数                      —— readfile 可读出 .env 内容
import re

# ---------- 常量 ----------
BLOCKED_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE",
    "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX", "TRIGGER",
}

DANGEROUS_FUNCTIONS = ("load_extension", "readfile", "writefile")

ALLOWED_STARTS = {"SELECT", "WITH"}

NONE_SENTINEL = "NONE"

# ---------- 正则 ----------
# 块注释：SQLite 的块注释不嵌套（实测验证过），非贪婪匹配与其行为一致
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT_RE = re.compile(r"--[^\n]*")
# 字符串字面量：SQL 用 '' 表示一个单引号
STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'", re.DOTALL)
# 标识符/关键词：字母或下划线开头
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def strip_sql_fence(s):
    """反复剥离 Markdown 围栏和结尾分号，直到稳定。

    必须循环：真实 LLM 输出形如
        ```sql\n...LIMIT 100;\n```
    围栏与分号互相遮挡，剥一次不够。
    """
    s = s.strip()
    changed = True
    while changed:
        changed = False
        # 去掉结尾的 ```
        if s.endswith("```"):
            s = s[:-3].strip()
            changed = True
        # 去掉结尾的分号
        if s.endswith(";"):
            s = s[:-1].strip()
            changed = True
        # 去掉开头的 ```lang（无换行时退化为 s[3:]，防越界）
        if s.startswith("```"):
            nl = s.find("\n")
            s = (s[nl + 1:] if nl != -1 else s[3:]).strip()
            changed = True
    return s


def _strip_comments(sql):
    """剥离 SQL 注释。先块注释后行注释。

    行注释替换为空格而非空串：否则 'SELECT 1--x\\nFROM t'
    会粘成 'SELECT 1FROM t'，导致后续切词错误。
    """
    s = BLOCK_COMMENT_RE.sub(" ", sql)
    s = LINE_COMMENT_RE.sub(" ", s)
    return s


def check_sql_safety(sql: str) -> tuple[bool, str]:
    """是否通过, 拒绝原因。

    返回 (ok, reason)：
      - ok=True  : reason 为空串
      - ok=False : reason 说明拒绝原因，可直接反馈给 LLM 让它重写
    """
    if not sql or not sql.strip():
        return False, "SQL 为空"

    # 1 + 2：剥围栏、剥注释
    cleaned = _strip_comments(strip_sql_fence(sql)).strip()
    if not cleaned:
        return False, "SQL 剔除注释后为空"

    # 3：NONE 哨兵（模型判定无法用 SQL 回答）
    if cleaned.upper() == NONE_SENTINEL:
        return False, "模型判定该问题无法用 SQL 回答"

    # 4：单语句检查（先挖掉字符串字面量，避免 'a;b' 被误判为多语句）
    sql_no_str = STRING_LITERAL_RE.sub("''", cleaned)
    parts = [p for p in sql_no_str.split(";") if p.strip()]
    if len(parts) > 1:
        return False, "只允许执行单条 SQL 语句，检测到多条语句"

    # 5：白名单首词（切词而非 startswith，防 SELECTX 蒙混过关）
    words = WORD_RE.findall(sql_no_str)
    if not words:
        return False, "无法识别 SQL 语句"
    first = words[0].upper()
    if first not in ALLOWED_STARTS:
        return False, f"只允许 SELECT / WITH 开头的只读查询，当前为 {first}"

    # 6：黑名单按词边界比对（不用"前后空格"假设，可抓 ;DELETE / \nDELETE）
    hit = {w.upper() for w in words} & BLOCKED_KEYWORDS
    if hit:
        return False, f"检测到写入/危险关键词：{', '.join(sorted(hit))}"

    # 7：危险函数（readfile 可读出 .env，load_extension 可加载动态库）
    low = sql_no_str.lower()
    for fn in DANGEROUS_FUNCTIONS:
        if re.search(rf"\b{fn}\s*\(", low):
            return False, f"检测到危险函数：{fn}"

    return True, ""