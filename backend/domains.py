from config import get_config, save_config

DOMAIN_OPTIONS = [
    {"key": "pro.yuketang.cn", "label": "Hetang Yuketang", "label_zh": "荷塘雨课堂"},
    {"key": "www.yuketang.cn", "label": "Yuketang", "label_zh": "雨课堂"},
    {"key": "changjiang.yuketang.cn", "label": "Changjiang Yuketang", "label_zh": "长江雨课堂"},
    {"key": "huanghe.yuketang.cn", "label": "Huanghe Yuketang", "label_zh": "黄河雨课堂"},
]

DEFAULT_DOMAIN = "pro.yuketang.cn"


def get_domain() -> str:
    cfg = get_config()
    return cfg.get("domain", DEFAULT_DOMAIN)


def set_domain(domain: str) -> None:
    cfg = get_config()
    cfg["domain"] = domain
    save_config(cfg)
