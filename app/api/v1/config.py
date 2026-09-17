"""自定义参数配置 API 路由"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.product import Product
from app.services import config_service

router = APIRouter()


class ConfigUpdate(BaseModel):
    value: str


@router.get("")
async def list_params(session: AsyncSession = Depends(get_session)):
    """列出所有可自定义参数（当前值/默认值/说明）"""
    return await config_service.get_all_params(session)


def _split_asins(raw) -> set:
    return config_service.split_asins(raw)


async def _apply_listing_override(session: AsyncSession, key: str) -> None:
    """排除/保留列表保存后立即应用到产品状态（否则要等重新导入才生效）"""
    if key == "listing_exclude_asins":
        exclude = _split_asins(await config_service.get_param(session, "listing_exclude_asins"))
        keep = _split_asins(await config_service.get_param(session, "listing_keep_asins"))
        to_disable = exclude - keep
        if to_disable:
            await session.execute(
                update(Product)
                .where(Product.asin.in_(sorted(to_disable)), Product.status == True)  # noqa: E712
                .values(status=False, status_text="已排除")
            )
    elif key == "listing_keep_asins":
        keep = _split_asins(await config_service.get_param(session, "listing_keep_asins"))
        if keep:
            await session.execute(
                update(Product)
                .where(Product.asin.in_(sorted(keep)), Product.status == False)  # noqa: E712
                .values(status=True, status_text="在售")
            )
    else:
        return
    await session.commit()
    # 手动改动 ASIN 列表 → 记录操作时间并置待刷新（10 分钟内重新清洗刷新状态）
    await config_service.touch_asin_list(session)


@router.put("/{key}")
async def update_param(key: str, body: ConfigUpdate, session: AsyncSession = Depends(get_session)):
    """更新参数（实时生效；排除/保留列表保存后立即停用/恢复产品）"""
    try:
        result = await config_service.set_param(session, key, body.value)
        await _apply_listing_override(session, key)
        return result
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
