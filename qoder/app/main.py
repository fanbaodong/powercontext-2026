from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError

from app.config import settings, STATIC_DIR
from app.database import check_health, DatabaseError
from app.routers import projects, documents, search, cards, progress

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    logger.info("研读台 ReadTrace 启动中 - seekdb=%s:%s/%s", settings.db_host, settings.db_port, settings.db_name)
    health = check_health()
    if health["status"] != "ok":
        logger.warning("数据库健康检查未通过: %s", health)
    else:
        logger.info("数据库连接正常")
    yield
    logger.info("研读台 ReadTrace 关闭")


app = FastAPI(
    title="研读台 ReadTrace",
    description="本地资料工作台：导入 → 检索 → 收藏 → 导出",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(projects.router)
app.include_router(documents.router)
app.include_router(search.router)
app.include_router(cards.router)
app.include_router(progress.router)


@app.get("/api/health")
def health_check():
    health = check_health()
    status_code = 200 if health["status"] == "ok" else 503
    return JSONResponse(content=health, status_code=status_code)


@app.get("/")
def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "研读台 ReadTrace API 运行中", "docs": "/docs"}


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.exception_handler(DatabaseError)
async def database_error_handler(request: Request, exc: DatabaseError):
    return JSONResponse(
        status_code=503,
        content={"error": "数据库服务暂不可用", "detail": "请检查 seekdb 是否运行"},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    messages = []
    for err in exc.errors():
        loc = " -> ".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")
        messages.append(f"{loc}: {msg}")
    return JSONResponse(
        status_code=422,
        content={"error": "输入验证失败", "detail": "; ".join(messages)},
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={"error": "输入有误", "detail": str(exc)},
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    logger.exception("未预期的错误")
    return JSONResponse(
        status_code=500,
        content={"error": "服务器内部错误", "detail": "请稍后重试"},
    )
