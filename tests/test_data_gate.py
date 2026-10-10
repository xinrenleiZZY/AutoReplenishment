"""B-05 数据门禁判定（纯函数，不连库）"""

from app.services.data_gate import CRITICAL_TABLES, decide


def _fresh(table, ok, lag=0, latest="2026-10-10"):
    return {"table": table, "latest_date": latest, "age_days": 0, "allowed_lag_days": lag, "ok": ok}


def _breaker(sync_type, tripped, bad=0):
    return {"sync_type": sync_type, "consecutive_bad": bad, "tripped": tripped,
            "detail": f"最近连续 {bad} 次非成功（阈值 3）"}


def test_all_fresh_passes():
    result = decide([_fresh("库存快照", True), _fresh("采购计划明细", True)], [_breaker("sales", False)])
    assert result["ok"] is True
    assert result["blocked"] is False
    assert result["reasons"] == []


def test_critical_stale_blocks():
    result = decide([_fresh("库存快照", False), _fresh("逐日销量", True)], [_breaker("sales", False)])
    assert result["ok"] is False
    assert result["blocked"] is True
    assert any("库存快照" in r for r in result["reasons"])


def test_non_critical_stale_only_warns():
    result = decide([_fresh("逐日销量", False), _fresh("库存快照", True)], [_breaker("sales", False)])
    assert result["ok"] is True
    assert result["blocked"] is False
    assert any("逐日销量" in w for w in result["warnings"])
    assert result["reasons"] == []


def test_breaker_tripped_blocks():
    result = decide([_fresh("库存快照", True)],
                    [_breaker("purchase_sources", True, bad=3), _breaker("sales", False)])
    assert result["ok"] is False
    assert any("熔断" in r and "purchase_sources" in r for r in result["reasons"])


def test_unknown_date_table_is_ignored():
    result = decide([_fresh("products", None), _fresh("库存快照", True)], [_breaker("product", False)])
    assert result["ok"] is True


def test_critical_tables_cover_sales_inventory_plan():
    assert {"领星销售快照", "库存快照", "采购计划明细"} <= CRITICAL_TABLES
