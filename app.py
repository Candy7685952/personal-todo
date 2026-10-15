"""第五周作业：使用 SQLite 与 SQL 实现的个人待办事项管理系统。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "pmis.db"
LEGACY_CSV_PATH = DATA_DIR / "records.csv"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
PRIORITIES = ["高", "中", "低"]
STATUSES = ["待办", "已完成"]
DEFAULT_CATEGORIES = [
    ("学习", "violet"),
    ("工作", "blue"),
    ("生活", "orange"),
    ("健康", "green"),
    ("其他", "gray"),
]


def now_text() -> str:
    return datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def today_local() -> date:
    return datetime.now(SHANGHAI_TZ).date()


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """连接 SQLite，并开启外键约束。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database() -> int:
    """创建满足第三范式的分类表和待办表，并迁移旧 CSV。"""
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                color TEXT NOT NULL DEFAULT 'gray',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL CHECK(length(trim(title)) > 0),
                category_id INTEGER NOT NULL,
                due_date TEXT NOT NULL,
                priority TEXT NOT NULL CHECK(priority IN ('高', '中', '低')),
                status TEXT NOT NULL DEFAULT '待办' CHECK(status IN ('待办', '已完成')),
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(category_id) REFERENCES categories(id)
                    ON UPDATE CASCADE ON DELETE RESTRICT
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date);
            CREATE INDEX IF NOT EXISTS idx_tasks_category ON tasks(category_id);
            """
        )
        stamp = now_text()
        conn.executemany(
            "INSERT OR IGNORE INTO categories(name, color, created_at) VALUES (?, ?, ?);",
            [(name, color, stamp) for name, color in DEFAULT_CATEGORIES],
        )

        task_count = conn.execute("SELECT COUNT(*) FROM tasks;").fetchone()[0]
        if task_count or not LEGACY_CSV_PATH.exists():
            return 0

        try:
            old_data = pd.read_csv(LEGACY_CSV_PATH, encoding="utf-8-sig").fillna("")
        except (OSError, UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError):
            return 0

        migrated = 0
        for _, row in old_data.iterrows():
            category = str(row.get("category", "其他")).strip() or "其他"
            conn.execute(
                "INSERT OR IGNORE INTO categories(name, color, created_at) VALUES (?, 'gray', ?);",
                (category, stamp),
            )
            title = str(row.get("title", "")).strip()
            if not title:
                continue
            status = str(row.get("status", "待办"))
            if status not in STATUSES:
                status = "待办"
            priority = str(row.get("priority", "中"))
            if priority not in PRIORITIES:
                priority = "中"
            due_date = str(row.get("due_date", today_local().isoformat())) or today_local().isoformat()
            created_at = str(row.get("created_at", stamp)) or stamp
            completed_at = str(row.get("completed_at", "")).strip() or None
            conn.execute(
                """
                INSERT INTO tasks(
                    title, category_id, due_date, priority, status, notes,
                    created_at, updated_at, completed_at
                )
                VALUES (
                    ?, (SELECT id FROM categories WHERE name = ?), ?, ?, ?, ?, ?, ?, ?
                );
                """,
                (
                    title,
                    category,
                    due_date,
                    priority,
                    status,
                    str(row.get("notes", "")),
                    created_at,
                    created_at,
                    completed_at,
                ),
            )
            migrated += 1
        return migrated


def get_categories() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT name FROM categories ORDER BY id;").fetchall()
    return [row["name"] for row in rows]


def load_tasks(status: str = "全部", category: str = "全部", keyword: str = "") -> pd.DataFrame:
    """用 JOIN 连接两张表，并用 WHERE 条件完成数据库筛选。"""
    sql = """
        SELECT
            t.id,
            t.title,
            c.name AS category,
            c.color AS category_color,
            t.due_date,
            t.priority,
            t.status,
            t.notes,
            t.created_at,
            t.updated_at,
            COALESCE(t.completed_at, '') AS completed_at
        FROM tasks AS t
        INNER JOIN categories AS c ON c.id = t.category_id
        WHERE 1 = 1
    """
    params: list[object] = []
    if status != "全部":
        sql += " AND t.status = ?"
        params.append(status)
    if category != "全部":
        sql += " AND c.name = ?"
        params.append(category)
    keyword = keyword.strip()
    if keyword:
        sql += " AND (t.title LIKE ? OR t.notes LIKE ?)"
        pattern = f"%{keyword}%"
        params.extend([pattern, pattern])
    sql += """
        ORDER BY
            CASE t.status WHEN '待办' THEN 0 ELSE 1 END,
            t.due_date,
            CASE t.priority WHEN '高' THEN 0 WHEN '中' THEN 1 ELSE 2 END,
            t.id;
    """
    with get_connection() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def get_task(task_id: int) -> dict[str, object] | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT t.*, c.name AS category
            FROM tasks AS t
            JOIN categories AS c ON c.id = t.category_id
            WHERE t.id = ?;
            """,
            (task_id,),
        ).fetchone()
    return dict(row) if row else None


def add_task(title: str, category: str, due_date: date, priority: str, notes: str) -> int:
    title = title.strip()
    if not title:
        raise ValueError("事项名称不能为空。")
    if priority not in PRIORITIES:
        raise ValueError("优先级无效。")
    stamp = now_text()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tasks(
                title, category_id, due_date, priority, status, notes,
                created_at, updated_at, completed_at
            )
            VALUES (
                ?, (SELECT id FROM categories WHERE name = ?), ?, ?, '待办', ?, ?, ?, NULL
            );
            """,
            (title, category, due_date.isoformat(), priority, notes.strip(), stamp, stamp),
        )
        if cursor.rowcount != 1:
            raise ValueError("类别不存在。")
        return int(cursor.lastrowid)


def update_task(
    task_id: int,
    title: str,
    category: str,
    due_date: date,
    priority: str,
    notes: str,
) -> None:
    title = title.strip()
    if not title:
        raise ValueError("事项名称不能为空。")
    if priority not in PRIORITIES:
        raise ValueError("优先级无效。")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE tasks
            SET title = ?,
                category_id = (SELECT id FROM categories WHERE name = ?),
                due_date = ?, priority = ?, notes = ?, updated_at = ?
            WHERE id = ?;
            """,
            (title, category, due_date.isoformat(), priority, notes.strip(), now_text(), task_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("找不到该事项。")


def set_task_status(task_id: int, status: str) -> None:
    if status not in STATUSES:
        raise ValueError("完成状态无效。")
    stamp = now_text()
    completed_at = stamp if status == "已完成" else None
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE tasks
            SET status = ?, completed_at = ?, updated_at = ?
            WHERE id = ?;
            """,
            (status, completed_at, stamp, task_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("找不到该事项。")


def delete_task(task_id: int) -> None:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM tasks WHERE id = ?;", (task_id,))
        if cursor.rowcount != 1:
            raise ValueError("找不到该事项。")


@st.dialog("编辑待办")
def edit_task_dialog(task_id: int) -> None:
    task = get_task(task_id)
    if task is None:
        st.error("找不到该事项。")
        return
    categories = get_categories()
    with st.form(f"edit_form_{task_id}"):
        title = st.text_input("事项名称 *", value=str(task["title"]))
        left, middle, right = st.columns(3)
        category = left.selectbox(
            "类别", categories, index=categories.index(str(task["category"]))
        )
        due_date = middle.date_input("截止日期", value=date.fromisoformat(str(task["due_date"])))
        priority = right.segmented_control(
            "优先级", PRIORITIES, default=str(task["priority"]), selection_mode="single"
        )
        notes = st.text_area("备注", value=str(task["notes"]), height=90)
        submitted = st.form_submit_button(
            "保存修改", type="primary", icon=":material/save:", width="stretch"
        )
    if submitted:
        try:
            update_task(task_id, title, category, due_date, priority or "中", notes)
            st.success("修改已写入数据库。")
            st.rerun()
        except (ValueError, sqlite3.Error) as exc:
            st.error(f"更新失败：{exc}")


@st.dialog("确认删除")
def confirm_delete_dialog(task_id: int, title: str) -> None:
    st.warning(f"删除“{title}”后无法在系统中恢复。")
    cancel, confirm = st.columns(2)
    if cancel.button("取消", width="stretch"):
        st.rerun()
    if confirm.button("确认删除", type="primary", icon=":material/delete:", width="stretch"):
        try:
            delete_task(task_id)
            st.rerun()
        except (ValueError, sqlite3.Error) as exc:
            st.error(f"删除失败：{exc}")


def input_form(categories: list[str]) -> None:
    with st.container(border=True):
        st.subheader("添加待办")
        with st.form("add_form", clear_on_submit=True):
            title = st.text_input("事项名称 *", placeholder="例如：完成数据库课程作业")
            left, middle, right = st.columns(3)
            category = left.selectbox("类别", categories)
            due_date = middle.date_input("截止日期", value=today_local())
            priority = right.segmented_control(
                "优先级", PRIORITIES, default="中", selection_mode="single"
            )
            notes = st.text_area("备注", placeholder="可选：资料、地点或下一步行动", height=80)
            submitted = st.form_submit_button(
                "写入数据库", type="primary", icon=":material/database:", width="stretch"
            )
    if submitted:
        try:
            add_task(title, category, due_date, priority or "中", notes)
            st.success("新增记录已提交。", icon=":material/check_circle:")
            st.rerun()
        except (ValueError, sqlite3.Error) as exc:
            st.error(f"新增失败：{exc}")


def render_task_cards(tasks: pd.DataFrame) -> None:
    if tasks.empty:
        st.info("当前条件下没有事项。", icon=":material/event_available:")
        return
    priority_colors = {"高": "red", "中": "orange", "低": "blue"}
    for _, row in tasks.iterrows():
        task_id = int(row["id"])
        is_done = row["status"] == "已完成"
        overdue = not is_done and str(row["due_date"]) < today_local().isoformat()
        with st.container(border=True):
            details, actions = st.columns([5, 2], vertical_alignment="center")
            with details:
                st.markdown(f"### {row['title']}")
                status_badge = ":green-badge[已完成]" if is_done else ":orange-badge[待办]"
                color = str(row["category_color"])
                if color not in {"red", "orange", "yellow", "green", "blue", "violet", "gray"}:
                    color = "gray"
                badges = (
                    f"{status_badge} :{color}-badge[{row['category']}] "
                    f":{priority_colors.get(row['priority'], 'gray')}-badge[{row['priority']}优先级]"
                )
                if overdue:
                    badges += " :red-badge[已逾期]"
                st.markdown(badges)
                st.caption(f":material/calendar_today: 截止 {row['due_date']}")
                if str(row["notes"]).strip():
                    st.markdown(f":material/notes: {row['notes']}")
            with actions:
                target = "待办" if is_done else "已完成"
                status_label = "恢复待办" if is_done else "一键完成"
                if st.button(
                    status_label,
                    key=f"status_{task_id}",
                    icon=":material/undo:" if is_done else ":material/check_circle:",
                    type="secondary" if is_done else "primary",
                    width="stretch",
                ):
                    set_task_status(task_id, target)
                    st.rerun()
                edit_col, delete_col = st.columns(2)
                if edit_col.button("编辑", key=f"edit_{task_id}", icon=":material/edit:", width="stretch"):
                    edit_task_dialog(task_id)
                if delete_col.button(
                    "删除", key=f"delete_{task_id}", icon=":material/delete:", width="stretch"
                ):
                    confirm_delete_dialog(task_id, str(row["title"]))


def render_database_notes() -> None:
    with st.expander("数据库结构与 SQL", icon=":material/schema:"):
        st.graphviz_chart(
            """
            digraph ER {
                rankdir=LR;
                node [shape=record, fontname="Microsoft YaHei"];
                categories [label="分类 categories|{PK id|name UNIQUE|color|created_at}"];
                tasks [label="待办 tasks|{PK id|FK category_id|title|due_date|priority|status|notes|created_at|updated_at|completed_at}"];
                categories -> tasks [label="1 对多"];
            }
            """
        )
        st.caption("tasks.category_id 外键指向 categories.id。分类名称只保存一次，符合第三范式。")
        st.code(
            """SELECT t.id, t.title, c.name AS category, t.status
FROM tasks AS t
JOIN categories AS c ON c.id = t.category_id
WHERE t.status = ?
ORDER BY t.due_date;""",
            language="sql",
        )


def main() -> None:
    st.set_page_config(page_title="个人待办事项管理系统·数据库版", page_icon="🗄️", layout="wide")
    migrated = init_database()
    categories = get_categories()

    st.title("个人待办事项管理系统 · 数据库版")
    st.caption("SQLite 双表存储｜SQL 增删改查｜分类与待办一对多关联")
    if migrated:
        st.success(f"已将旧 CSV 中的 {migrated} 条记录迁移到 SQLite。")

    input_form(categories)

    all_tasks = load_tasks()
    total = len(all_tasks)
    completed = int((all_tasks["status"] == "已完成").sum()) if total else 0
    pending = total - completed
    overdue = 0
    if total:
        overdue = int(
            ((all_tasks["status"] == "待办") & (all_tasks["due_date"] < today_local().isoformat())).sum()
        )
    rate = completed / total if total else 0

    st.subheader("完成情况")
    with st.container(horizontal=True):
        st.metric("全部事项", total, border=True)
        st.metric("待办", pending, border=True)
        st.metric("已完成", completed, border=True)
        st.metric("已逾期", overdue, border=True)
    st.progress(rate, text=f"总体完成率 {rate:.0%}")

    st.subheader("数据库查询结果")
    filter_left, filter_right = st.columns([3, 2])
    status_filter = filter_left.segmented_control(
        "状态筛选", ["全部", "待办", "已完成"], default="全部", selection_mode="single"
    )
    category_filter = filter_right.selectbox("类别筛选", ["全部"] + categories)
    keyword = st.text_input(
        "搜索事项",
        placeholder="SQL 模糊查询：事项名称或备注",
        icon=":material/search:",
        label_visibility="collapsed",
    )
    shown_tasks = load_tasks(status_filter or "全部", category_filter, keyword)
    render_task_cards(shown_tasks)

    with st.expander("数据表与下载", icon=":material/table_view:"):
        display = shown_tasks.drop(columns=["category_color"]).rename(
            columns={
                "id": "编号",
                "title": "事项",
                "category": "类别",
                "due_date": "截止日期",
                "priority": "优先级",
                "status": "状态",
                "notes": "备注",
                "created_at": "创建时间",
                "updated_at": "更新时间",
                "completed_at": "完成时间",
            }
        )
        st.dataframe(display, hide_index=True)
        st.download_button(
            "下载查询结果 CSV",
            data=display.to_csv(index=False).encode("utf-8-sig"),
            file_name="待办事项_SQL查询结果.csv",
            mime="text/csv",
            icon=":material/download:",
        )
    render_database_notes()


if __name__ == "__main__":
    main()
