"""租户配置加载。

从 config/tenants.yaml 读取租户配置并应用环境变量覆盖
（LLM api key / base_url / model 及存储连接串）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

import yaml
from dotenv import load_dotenv

from trpc_service.config.tenant_config import TenantConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "tenants.yaml"
ENV_FILE = PROJECT_ROOT / ".env"


def load_dotenv_if_present() -> None:
    """加载项目根目录的 .env（不存在则跳过）。"""
    if ENV_FILE.exists():
        load_dotenv(dotenv_path=ENV_FILE)


def _apply_env_overrides(cfg: TenantConfig) -> TenantConfig:
    """用环境变量覆盖租户模型配置。

    优先级：租户 YAML 里显式配置的 api_key > 环境变量 > 空。
    base_url / model_name 同理（URL / MODEL）。
    """
    key = os.getenv("KEY") or os.getenv("API_KEY")
    url = os.getenv("URL") or os.getenv("BASE_URL")
    model = os.getenv("MODEL")
    if key:
        cfg.model.api_key = key
    if url:
        cfg.model.base_url = url
    if model:
        cfg.model.model_name = model
    return cfg


def load_config(path: Optional[str] = None) -> Dict[str, TenantConfig]:
    """加载全部租户配置，返回 tenant_id -> TenantConfig 的映射。"""
    load_dotenv_if_present()
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"租户配置文件不存在: {config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    tenants = (raw or {}).get("tenants") or []
    result: Dict[str, TenantConfig] = {}
    for item in tenants:
        cfg = TenantConfig(**item)
        _apply_env_overrides(cfg)
        result[cfg.tenant_id] = cfg
    return result


def get_tenant_config(tenant_id: str, configs: Optional[Dict[str, TenantConfig]] = None) -> Optional[TenantConfig]:
    """按 tenant_id 获取单个租户配置。"""
    return (configs or load_config()).get(tenant_id)
