#文件数据加载器
import pandas as pd
from database.db_manager import get_connection




def sanitize_table_name(name: str) -> str:
    '''从文件名生成合法的 SQLite 表名：
    去掉扩展名；把非字母/数字/下划线/汉字的字符替换为下划线；
    合并连续下划线；
    空名时用时间戳兜底。
    '''
    import re
    import os
    name = os.path.splitext(name)[0]  # 去掉扩展名
    name = re.sub(r"[^\w\u4e00-\u9fff]", "_", name)  # 非"字母数字下划线汉字"一律变下划线
    name = re.sub(r"_+", "_", name).strip("_")  # 多个下划线合并为一个
    if not name:
        name = "table_" + pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    return name[:64]  # SQLite 表名长度限制为 64 字符



def sniff_and_read(file_obj, filename: str) -> tuple[pd.DataFrame, str]:
    '''尝试多种编码和分隔符读取 CSV 文件和 Excel 文件，返回 DataFrame'''
    # 1. 先尝试 Excel 文件
    if filename.lower().endswith(('.xls', '.xlsx')):
        try:
            df = pd.read_excel(file_obj, engine='openpyxl')
            return df, "excel"
        except Exception as e:
            print(f"Error reading Excel file: {e}")
            raise e
    # 2. 再尝试 CSV 文件
    for encoding in ["utf-8-sig", "utf-8", "gbk", "gb18030"]:
        file_obj.seek(0)
        try:
            df = pd.read_csv(file_obj, encoding=encoding, sep=None, engine="python")
            return df, encoding
        except (UnicodeDecodeError, UnicodeError) as e:
            continue
    raise ValueError("Failed to read the file with any supported encoding or separator.")






def infer_numeric(series: pd.Series) -> pd.Series:
    '''尝试将一列转为数值类型，失败则保持原样'''
    #coerce: 转换失败的值设为 NaN
    #raise: 整列放弃，数据保留
    try:
        return pd.to_numeric(series, errors='raise')
    except (ValueError, TypeError):
        return series




def register_table(table_name, source_file, row_count, columns) -> None:
    '''在数据库中注册新表的元信息，供前端展示'''
    import json
    from datetime import datetime
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO table_registry"
            "(table_name, source_file, row_count, columns_json, imported_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (table_name, source_file, row_count,
             json.dumps(columns,ensure_ascii=False),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    finally:
        conn.close()





def load_file_to_sqlite(file_obj, filename: str, table_name: str = None) -> dict:
    '''加载 CSV 或 Excel 文件到 SQLite 数据库，返回表信息'''
    # 1. 定表名
    table_name = table_name or sanitize_table_name(filename)
    #2. 读文件
    df, enc = sniff_and_read(file_obj, filename)
    #3.逐列尝试转数值
    for col in df.columns:
        df[col] = infer_numeric(df[col])
    #4.写入数据库
    conn = get_connection()
    try:
        df.to_sql(table_name, conn, if_exists='replace', index=False)
        conn.commit()
    finally:
        conn.close()
    #5.注册元信息
    register_table(table_name, filename, len(df), list(df.columns))
    #6.返回表信息
    return {
        "table": table_name,
        "row_count": len(df),
        "columns": list(df.columns),
        "encoding": enc}