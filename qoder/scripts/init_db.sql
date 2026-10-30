-- ========================================
-- 研读台 ReadTrace - seekdb 数据库初始化
-- 仅创建 qoder_research 专属库和应用表
-- ========================================

CREATE DATABASE IF NOT EXISTS qoder_research
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_general_ci;

USE qoder_research;

-- 研究项目
CREATE TABLE IF NOT EXISTS projects (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- 资料文档
CREATE TABLE IF NOT EXISTS documents (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    title VARCHAR(500) NOT NULL,
    author VARCHAR(255) DEFAULT NULL,
    source_url VARCHAR(2000) DEFAULT NULL,
    tags VARCHAR(1000) DEFAULT NULL,
    content_hash CHAR(64) NOT NULL COMMENT 'SHA-256 of full raw content',
    char_count INT NOT NULL DEFAULT 0,
    chunk_count INT NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE KEY uk_project_content (project_id, content_hash)
) ENGINE=InnoDB;

-- 段落分块
CREATE TABLE IF NOT EXISTS chunks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    document_id INT NOT NULL,
    paragraph_number INT NOT NULL COMMENT '从1开始的段落序号',
    content TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL COMMENT 'SHA-256 of this chunk content',
    char_count INT NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
    UNIQUE KEY uk_doc_para (document_id, paragraph_number),
    FULLTEXT INDEX ft_content (content) WITH PARSER ngram
) ENGINE=InnoDB;

-- 证据卡片（收藏的摘录 + 批注）
CREATE TABLE IF NOT EXISTS cards (
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
) ENGINE=InnoDB;

-- 阅读进度
CREATE TABLE IF NOT EXISTS reading_progress (
    id INT AUTO_INCREMENT PRIMARY KEY,
    document_id INT NOT NULL,
    status ENUM('unread', 'reading', 'read') NOT NULL DEFAULT 'unread',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
    UNIQUE KEY uk_document (document_id)
) ENGINE=InnoDB;

-- 插入演示项目
INSERT INTO projects (id, name, description) VALUES
    (1, '学习方法研究', '收集与整理关于高效学习方法和科研技巧的资料笔记'),
    (2, '认知科学笔记', '认知心理学与脑科学相关的学习材料')
ON DUPLICATE KEY UPDATE name=VALUES(name);
