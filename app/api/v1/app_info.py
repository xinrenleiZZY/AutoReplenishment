"""应用信息 API 路由"""

from fastapi import APIRouter

from app.config import settings

router = APIRouter()


@router.get("", summary="获取应用信息")
async def get_app_info():
    """返回应用名称、版本号、环境等信息，供前端动态展示版本号（侧边栏/个人中心等）。"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "env": settings.APP_ENV,
    }
