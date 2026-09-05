"""预算 Redis 双后端测试（fakeredis 模拟，无需真实 Redis 服务）。"""
import pytest
from fakeredis import FakeRedis

from trpc_service.gateway.budget import BudgetExceeded, BudgetManager


def _mgr(fake=None) -> BudgetManager:
    """构造 Redis 模式的管理器；传入共享 fake 可模拟多节点连同一 Redis。"""
    m = BudgetManager()
    m._redis = fake if fake is not None else FakeRedis(decode_responses=True)
    return m


def test_cross_manager_shared_counter():
    """两个"节点"共享同一 Redis：计数必须合并（Redis 化的存在理由）。"""
    shared = FakeRedis(decode_responses=True)   # 两节点必须连同一个 Redis
    m1, m2 = _mgr(shared), _mgr(shared)
    for _ in range(3):
        m2.record("tenant_002")
    m1.record("tenant_002")
    assert m2.usage_of("tenant_002").api_calls == 4


def test_redis_check_blocks_when_exhausted():
    """Redis 计数超限时 check 拦截。"""
    m = _mgr()
    m.record("tenant_002", api_calls=999999, tokens=99999999)
    with pytest.raises(BudgetExceeded):
        m.check("tenant_002")


def test_redis_expire_set_on_both_keys():
    """review 回归测试：record 后两个键都必须带 TTL，防内存泄漏。"""
    m = _mgr()
    m.record("tenant_002", api_calls=2, tokens=100)
    base = f"budget:tenant_002:{m._today()}"
    calls_ttl = m._redis.ttl(f"{base}:calls")
    tokens_ttl = m._redis.ttl(f"{base}:tokens")
    assert 0 < calls_ttl <= 172800, "calls 键必须设置 48h TTL"
    assert 0 < tokens_ttl <= 172800, "tokens 键必须设置 48h TTL（漏设=永久残留）"


def test_memory_fallback_when_no_redis():
    """不配 BUDGET_REDIS_URL → 纯内存模式照常工作（降级正确性）。"""
    m = BudgetManager()
    m.record("tenant_002", api_calls=5)
    assert m.usage_of("tenant_002").api_calls == 5
