"""
页面功能：
    -  ① 上传数据文件（CSV / Excel）→ 预览校验 → 确认入库
    -  ② 一键导入示例数据（固定种子，可复现）
    -  ③ 已有数据表管理（清单 / 字段结构 / 数据预览 / 删除）

后端接口（方案B：直接调用函数式 API，不使用 DBManager 类）
    config.settings                  -> DB_PATH
    database.init_db.init_db()       -> 建表（幂等）
    database.db_manager
        get_connection()             -> sqlite3 连接（row_factory=Row）
        get_table_list()             -> 业务表名列表（已排除系统表）
        get_table_columns(table)     -> [{name,type,notnull,default}]
        get_table_preview(table)     -> DataFrame（前 20 行）
    utils.data_loader
        sniff_and_read(file_obj, filename) -> (DataFrame, 编码)
        register_table(table, file, rows, cols) -> None  写 table_registry

本页自备的页面级数据访问（后端未提供的能力，在此就地实现）
    _db/table_exists/drop_table/get_table_rowcount/get_dataset_info
    ★ 这些是"页面需要但后端没有"的补丁，若后端将来补齐，应改为调用后端
"""

import hashlib
import json
import re

import numpy as np
import pandas as pd
import streamlit as st

from config import settings
from database.db_manager import (
    get_connection,
    get_table_columns,
    get_table_list,
    get_table_preview,
)
from database.init_db import init_db
from utils.data_loader import register_table, sniff_and_read


# ==================== 依赖就绪检测 ====================

def ensure_db() -> tuple[bool, str | None]:
    """确保数据库文件与表结构就绪（幂等）。返回 (是否就绪, 错误信息)。"""
    try:
        init_db()
        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ==================== 页面级数据访问（后端缺失能力的补丁）====================

def table_exists(table_name: str) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def get_table_rowcount(table_name: str) -> int | None:
    if not table_exists(table_name):
        return None
    conn = get_connection()
    try:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0])
    except Exception:
        return None
    finally:
        conn.close()


def drop_table(table_name: str) -> None:
    """删表 + 清理 table_registry 元信息。"""
    conn = get_connection()
    try:
        conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        conn.execute("DELETE FROM table_registry WHERE table_name=?", (table_name,))
        conn.commit()
    finally:
        conn.close()


def get_dataset_info(table_name: str) -> dict:
    """从 table_registry 读元信息；列为空时回退到 PRAGMA。"""
    conn = get_connection()
    try:
        conn.row_factory = None
        cur = conn.execute(
            "SELECT source_file, row_count, columns_json, imported_at "
            "FROM table_registry WHERE table_name=?",
            (table_name,),
        )
        row = cur.fetchone()
    except Exception:
        row = None
    finally:
        conn.close()

    if row:
        source_file, row_count, columns_json, imported_at = row
        try:
            cols = json.loads(columns_json) if columns_json else []
        except Exception:
            cols = []
        return {
            "source_file": source_file,
            "row_count": row_count,
            "col_count": len(cols) if cols else "—",
            "imported_at": imported_at,
        }
    # 回退：注册表里没有，就用 PRAGMA 推断列数
    try:
        return {
            "source_file": "—",
            "row_count": get_table_rowcount(table_name) or "—",
            "col_count": len(get_table_columns(table_name)) or "—",
            "imported_at": "—",
        }
    except Exception:
        return {"source_file": "—", "row_count": "—", "col_count": "—", "imported_at": "—"}


# ==================== 解析与校验工具 ====================

ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030")
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED = {"SELECT", "FROM", "WHERE", "TABLE", "DROP", "DELETE", "INSERT", "UPDATE"}


def validate_table_name(name: str) -> tuple[bool, str]:
    name = (name or "").strip()
    if not name:
        return False, "表名不能为空"
    if len(name) > 50:
        return False, "表名不能超过 50 个字符"
    if not _TABLE_NAME_RE.match(name):
        return False, "表名只能用字母、数字、下划线，且不能以数字开头（请用英文）"
    if name.upper() in _RESERVED:
        return False, "表名不能使用 SQL 关键字"
    return True, ""


def normalize_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    warns = []
    df.columns = [str(c).replace("\ufeff", "").strip() for c in df.columns]
    dups = df.columns[df.columns.duplicated()].tolist()
    if dups:
        warns.append(f"存在重复列名：{dups}，入库前请先重命名")
    unnamed = [c for c in df.columns if c == "" or c.lower().startswith("unnamed")]
    if unnamed:
        warns.append(f"存在未命名列（{len(unnamed)} 个），通常是「表头所在行」设置不对")
    return df, warns


def read_with_encoding(buf, filename: str, encoding: str, header_row: int) -> pd.DataFrame:
    """按指定编码读取（让用户能主动切换编码）。

    ★ 先用 sep=None 让 pandas 自动嗅探分隔符（支持逗号 / 分号 / Tab / 竖线）。
      不加 sep 时 pandas 只按逗号切分，分号 CSV 会被静默读成"只有一列"，
      上传还提示成功，之后 Agent 拿到一列无意义数据，很难排查。

    ★ 嗅探失败时回退到默认逗号解析：单列 CSV（没有分隔符）和"表头行设为 1"
      这类文件，csv.Sniffer 会抛 "Could not determine delimiter"，但内容本身正常。
      注意回退前必须 seek(0) —— 第一次尝试已经读过流，不复位会读到空内容。

    ★ UnicodeDecodeError 必须原样抛出：编码探测依赖它来判断"这个编码不对"。
      （它是 ValueError 的子类，若被下面的 except Exception 吞掉，
         auto_detect_read 就永远试不到 gbk / gb18030 了。）
    """
    buf.seek(0)
    if filename.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(buf, header=int(header_row), engine="openpyxl")
    try:
        return pd.read_csv(buf, encoding=encoding, header=int(header_row),
                           sep=None, engine="python")
    except UnicodeDecodeError:
        raise                                  # 交给上层继续试下一个编码
    except Exception:
        buf.seek(0)                            # 回退前复位指针
        return pd.read_csv(buf, encoding=encoding, header=int(header_row))


def auto_detect_read(buf, filename: str, header_row: int) -> tuple[pd.DataFrame, str]:
    """自动探测编码：依次尝试 ENCODINGS，与后端 sniff_and_read 的顺序一致。

    ★ 异常处理分两类：
      1. UnicodeDecodeError —— 这个编码不对，换下一个继续试；
      2. 其它异常（如 sep=None 嗅探失败抛的 "Could not determine delimiter"）
         —— 与编码无关，是文件内容问题（单列无分隔符 / 空文件 / 全空白）。
         此时立刻停下，并把原始信息报出去，让用户知道不是编码的锅。
      若对第 2 类也继续循环，会把"内容问题"误报成"编码都不支持"，
      用户会一直去切编码，永远修不好。
    """
    if filename.lower().endswith((".xlsx", ".xls")):
        buf.seek(0)
        return pd.read_excel(buf, header=int(header_row), engine="openpyxl"), "excel"

    tried = []
    for enc in ENCODINGS:
        try:
            return read_with_encoding(buf, filename, enc, header_row), enc
        except UnicodeDecodeError:
            tried.append(enc)               # 编码不匹配，继续试下一个
            continue
        except Exception as e:
            # 编码能解开，但内容解析不了 —— 再换编码也没用，直接报原始原因
            if type(e).__name__ == "EmptyDataError":
                raise ValueError(
                    "文件没有可解析的内容（可能是空文件、只有空行，"
                    "或表头所在行的设置不对）。请检查文件后重试。"
                ) from e
            raise ValueError(
                f"文件内容无法解析（已用 {enc} 成功解码，问题不在编码）："
                f"{type(e).__name__}: {e}"
            ) from e

    raise ValueError(f"尝试了 {ENCODINGS} 均无法解码，已排除：{tried}")


def human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024:
            return f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"


def make_token(payload: str) -> str:
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:10]


def clear_parse_cache() -> None:
    for k in ("parsed_df", "parse_error", "parse_token", "parse_meta"):
        st.session_state.pop(k, None)


def build_sample_df() -> pd.DataFrame:
    """示例数据：固定随机种子，可复现（与 01 页面同源）。"""
    rng = np.random.default_rng(42)
    n = 200
    df = pd.DataFrame({
        "日期": pd.date_range("2026-01-01", periods=n, freq="D"),
        "类别": rng.choice(["电子产品", "服装鞋帽", "食品饮料", "家居用品", "美妆个护"], size=n),
        "地区": rng.choice(["华东", "华南", "华北", "西南"], size=n),
        "销量": rng.integers(10, 300, size=n),
        "单价": np.round(rng.uniform(3, 80, size=n), 2),
    })
    df["销售额"] = np.round(df["销量"] * df["单价"], 2)
    df["成本"] = np.round(df["销售额"] * rng.uniform(0.4, 0.7, size=n), 2)
    df["利润"] = np.round(df["销售额"] - df["成本"], 2)
    return df


# ==================== 页面配置 ====================

st.set_page_config(page_title="数据管理", page_icon="🗂️", layout="wide")
st.markdown('<div class="app-header"><h1>🗂️ 数据管理</h1></div>', unsafe_allow_html=True)

# ---------- 数据库列表（每次渲染重读，保证入库后立刻刷新）----------
db_ok, db_err = ensure_db()
if not db_ok:
    st.error(f"数据库初始化失败：{db_err}")
    st.stop()

try:
    TABLE_LIST = sorted(get_table_list())
except Exception as e:
    st.error(f"读取表清单失败：{type(e).__name__}: {e}")
    TABLE_LIST = []

st.caption(f"数据库：`{settings.DB_PATH}` · 已有数据表 {len(TABLE_LIST)} 张")

# ==================== ① 上传与入库 ====================

st.subheader("① 上传数据文件")

up = st.file_uploader(
    "选择 CSV 或 Excel 文件",
    type=["csv", "xlsx"],
    help="支持 .csv 和 .xlsx；老式 .xls / .ods 需额外解析引擎，暂不支持",
)

if up is None:
    clear_parse_cache()
else:
    a, b = st.columns(2)
    with a:
        encoding_choice = st.selectbox(
            "文件编码", ["自动探测"] + list(ENCODINGS), index=0,
            help="自动探测会依次尝试 utf-8-sig / utf-8 / gbk / gb18030；出现乱码时手动指定",
        )
    with b:
        header_row = st.number_input(
            "表头所在行（从 0 开始）", min_value=0, max_value=50, value=0, step=1,
            help="预览里出现 Unnamed 列或首行数据变成列名时，把这里加 1",
        )

    token = make_token(f"{up.name}|{up.size}|{encoding_choice}|{header_row}")
    if st.session_state.get("parse_token") != token:
        st.session_state.parse_token = token
        try:
            from io import BytesIO
            raw = up.getvalue() if hasattr(up, "getvalue") else up.read()
            if len(raw) > 100 * 1024 * 1024:
                raise ValueError(f"文件过大（{human_size(len(raw))}），请拆分到 100MB 以内")
            buf = BytesIO(raw)
            if encoding_choice == "自动探测":
                df, used_enc = auto_detect_read(buf, up.name, header_row)
            else:
                df = read_with_encoding(buf, up.name, encoding_choice, header_row)
                used_enc = encoding_choice
            df, col_warns = normalize_columns(df)
            st.session_state.parsed_df = df
            st.session_state.parse_meta = {
                "name": up.name, "size": len(raw), "encoding": used_enc,
                "warnings": col_warns, "raw": raw,
            }
            st.session_state.parse_error = None
        except Exception as e:
            st.session_state.parsed_df = None
            st.session_state.parse_meta = {}
            st.session_state.parse_error = f"{type(e).__name__}: {e}"

    # ---------- 解析失败 ----------
    if st.session_state.get("parse_error"):
        st.error(f"解析失败：{st.session_state.parse_error}")
        st.caption("常见原因：编码不对（试着手动指定 gbk / gb18030）、表头行不对、"
                   "文件其实是 .xls、或不是标准表格结构。")

    df = st.session_state.get("parsed_df")
    if df is not None:
        meta = st.session_state.get("parse_meta", {})
        st.success(
            f"解析成功：**{df.shape[0]:,} 行 × {df.shape[1]} 列**"
            f"（{meta.get('name', up.name)}，{human_size(meta.get('size', up.size))}，"
            f"编码 {meta.get('encoding', '—')}）"
        )
        for w in meta.get("warnings", []):
            st.warning(w)

        st.markdown("###### 数据预览（前 50 行）")
        st.dataframe(df.head(50), width="stretch")

        st.markdown("###### 字段概况")
        profile = pd.DataFrame({
            "字段名": [str(c) for c in df.columns],
            "类型": [str(t) for t in df.dtypes],
            "非空数": [int(df[c].notna().sum()) for c in df.columns],
            "空值数": [int(df[c].isna().sum()) for c in df.columns],
            "空值率": [f"{df[c].isna().mean() * 100:.1f}%" for c in df.columns],
            "唯一值数": [int(df[c].nunique(dropna=True)) for c in df.columns],
        })
        st.dataframe(profile, width="stretch", hide_index=True)

        null_mask = (df.isna().mean().values > 0.9)
        high_null = profile.loc[null_mask, "字段名"].tolist()
        if high_null:
            st.warning(f"以下字段空值率超过 90%：**{', '.join(high_null)}**。"
                       "如果不是预期情况，请检查「表头所在行」和「文件编码」。")

        # ---------- 确认入库 ----------
        st.divider()
        st.markdown("###### 确认入库")

        default_name = re.sub(r"[^A-Za-z0-9_]", "_", up.name.rsplit(".", 1)[0])
        if not _TABLE_NAME_RE.match(default_name or ""):
            default_name = "data_" + make_token(up.name)

        table_name = st.text_input("入库表名", value=default_name,
                                   help="只能用英文、数字、下划线，且不能以数字开头")
        ok, reason = validate_table_name(table_name)

        exists = bool(ok and table_exists(table_name))
        overwrite = True
        if exists:
            st.error(f"表 `{table_name}` 已存在。换一个表名，或先去「③ 已有数据表」删掉旧表。")
            overwrite = st.checkbox("我确认要覆盖这张表（原数据将被永久替换）", key="ovw_ok")

        if not ok and table_name:
            st.caption(f"⚠️ {reason}")

        if st.button("✅ 确认入库", type="primary", disabled=not (ok and overwrite)):
            with st.spinner("正在写入数据库…"):
                try:
                    from io import BytesIO
                    payload = BytesIO(meta.get("raw", up.getvalue()))

                    # 统一转换数值列（与 utils.data_loader.infer_numeric 同策略）
                    out = df.copy()
                    for col in out.columns:
                        try:
                            out[col] = pd.to_numeric(out[col], errors="raise")
                        except (ValueError, TypeError):
                            pass

                    conn = get_connection()
                    try:
                        out.to_sql(table_name, conn, if_exists="replace", index=False)
                        conn.commit()
                    finally:
                        conn.close()

                    register_table(table_name, up.name, len(out), list(out.columns))
                    st.success(f"已入库：表 `{table_name}`，共 {len(out):,} 行 × {out.shape[1]} 列")
                    clear_parse_cache()
                    st.session_state.pop("ovw_ok", None)
                    st.rerun()
                except Exception as e:
                    st.error(f"入库失败：{type(e).__name__}: {e}")
                    del payload

# ==================== ② 示例数据 ====================

st.divider()
st.subheader("② 一键导入示例数据")
st.caption("没有现成数据？导入一份固定随机种子的模拟数据，立刻能到「对话」页试用。")

if st.button("📦 导入示例数据"):
    sample_name = "sample_sales"
    try:
        if table_exists(sample_name):
            st.info(f"示例表 `{sample_name}` 已存在，无需重复导入。"
                    "（如需重置，请到 ③ 区删除后重新导入）")
        else:
            sdf = build_sample_df()
            conn = get_connection()
            try:
                sdf.to_sql(sample_name, conn, if_exists="replace", index=False)
                conn.commit()
            finally:
                conn.close()
            register_table(sample_name, "(内置示例数据)", len(sdf), list(sdf.columns))
            st.success(f"示例数据已导入：表 `{sample_name}`，共 {len(sdf):,} 行")
            st.rerun()
    except Exception as e:
        st.error(f"导入示例数据失败：{type(e).__name__}: {e}")

# ==================== ③ 已有数据表 ====================

st.divider()
st.subheader("③ 已有数据表")

if not TABLE_LIST:
    st.caption("还没有任何数据表。上传一个文件，或点上面的「导入示例数据」。")
else:
    rows = []
    for t in TABLE_LIST:
        info = get_dataset_info(t)
        n_rows = info.get("row_count")
        if not isinstance(n_rows, int):
            n_rows = get_table_rowcount(t)
        rows.append({
            "表名": t,
            "行数": n_rows if n_rows is not None else "—",
            "列数": info.get("col_count", "—"),
            "来源文件": info.get("source_file", "—"),
            "导入时间": str(info.get("imported_at", "—"))[:19],
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    picked = st.selectbox("查看表详情", TABLE_LIST, key="detail_table")

    col_schema, col_preview = st.columns([1, 2])

    with col_schema:
        st.markdown("###### 字段结构")
        try:
            cols = get_table_columns(picked)
            st.dataframe(
                pd.DataFrame([
                    {
                        "字段名": c["name"],
                        "类型": c["type"],
                        "非空": "是" if c["notnull"] else "否",
                        "默认值": c["default"] if c["default"] is not None else "—",
                    }
                    for c in cols
                ]),
                width="stretch", hide_index=True,
            )
        except Exception as e:
            st.error(f"读取表结构失败：{type(e).__name__}: {e}")

    with col_preview:
        st.markdown("###### 数据预览（前 20 行）")
        try:
            st.dataframe(get_table_preview(picked, limit=20), width="stretch")
        except Exception as e:
            st.caption(f"无法预览（{type(e).__name__}: {e}）")

    with st.expander("🗑 删除数据表（危险操作）"):
        st.warning(f"将永久删除表 `{picked}` 及其登记信息，不可撤销。")
        ck = f"del_confirm_{picked}"
        if st.checkbox("我确认要删除这张表", key=ck):
            if st.button("确认删除", type="primary", key=f"del_btn_{picked}"):
                try:
                    drop_table(picked)
                    st.success(f"已删除表 `{picked}`")
                    st.session_state.pop(ck, None)
                    st.rerun()
                except Exception as e:
                    st.error(f"删除失败：{type(e).__name__}: {e}")