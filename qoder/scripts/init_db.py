"""
数据库初始化脚本（Python版）

从 settings 读取凭据，先连接不指定数据库，创建库和表。
可重复执行（IF NOT EXISTS / ON DUPLICATE KEY）。

使用方法:
  python scripts/init_db.py
"""
import sys
import os
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pymysql
from app.config import settings


def init_database():
    db_name = settings.db_name
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", db_name):
        print(f"数据库名不合法: {db_name!r}，仅允许字母、数字和下划线，且以字母或下划线开头", file=sys.stderr)
        sys.exit(1)

    print(f"连接 seekdb {settings.db_host}:{settings.db_port} 用户={settings.db_user}")

    conn = pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=10,
    )

    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE DATABASE IF NOT EXISTS `%s` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci" % db_name
            )
            print(f"  数据库 {db_name} 已就绪")

        conn.select_db(db_name)

        with conn.cursor() as cur:
            statements = [
                """CREATE TABLE IF NOT EXISTS projects (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    description TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB""",

                """CREATE TABLE IF NOT EXISTS documents (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    project_id INT NOT NULL,
                    title VARCHAR(500) NOT NULL,
                    author VARCHAR(255) DEFAULT NULL,
                    source_url VARCHAR(2000) DEFAULT NULL,
                    tags VARCHAR(1000) DEFAULT NULL,
                    content_hash CHAR(64) NOT NULL,
                    char_count INT NOT NULL DEFAULT 0,
                    chunk_count INT NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    UNIQUE KEY uk_project_content (project_id, content_hash)
                ) ENGINE=InnoDB""",

                """CREATE TABLE IF NOT EXISTS chunks (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    document_id INT NOT NULL,
                    paragraph_number INT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash CHAR(64) NOT NULL,
                    char_count INT NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
                    UNIQUE KEY uk_doc_para (document_id, paragraph_number),
                    FULLTEXT INDEX ft_content (content) WITH PARSER ngram
                ) ENGINE=InnoDB""",

                """CREATE TABLE IF NOT EXISTS cards (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    project_id INT NOT NULL,
                    document_id INT NOT NULL,
                    chunk_id INT NOT NULL,
                    annotation TEXT DEFAULT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
                    FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE,
                    UNIQUE KEY uk_project_doc_chunk (project_id, document_id, chunk_id)
                ) ENGINE=InnoDB""",

                """CREATE TABLE IF NOT EXISTS reading_progress (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    document_id INT NOT NULL,
                    status ENUM('unread', 'reading', 'read') NOT NULL DEFAULT 'unread',
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
                    UNIQUE KEY uk_document (document_id)
                ) ENGINE=InnoDB""",
            ]

            for sql in statements:
                cur.execute(sql)
            print("  5 张表已就绪")

            cur.execute(
                "INSERT INTO projects (id, name, description) VALUES "
                "(1, '学习方法研究', '收集与整理关于高效学习方法和科研技巧的资料笔记'), "
                "(2, '认知科学笔记', '认知心理学与脑科学相关的学习材料') "
                "ON DUPLICATE KEY UPDATE name=VALUES(name)"
            )
            print("  演示项目已插入")

        print("\n数据库初始化完成。")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        init_database()
    except Exception as e:
        print(f"初始化失败: {e}", file=sys.stderr)
        sys.exit(1)
