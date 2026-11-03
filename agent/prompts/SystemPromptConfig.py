import threading
import time
import hashlib
from dataclasses import dataclass, field
from config import logger, nacos_config, config_manager
from common import *


def md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


@dataclass
class SystemPromptConfig:
    """支持热更新（轮询）的系统提示词配置"""

    chat_route_message: str = ""
    chat_recommend_message: str = ""
    chat_buy_message: str = ""
    chat_consult_message: str = ""
    chat_knowledge_message: str = ""
    chat_unknown_message: str = ""
    chat_text_message: str = ""
    chat_a2a_message: str = ""

    _config_map: dict = field(default_factory=lambda: {
        PROMPT_ROUTE_CHAT_DATA_ID: "chat_route_message",
        PROMPT_RECOMMEND_CHAT_DATA_ID: "chat_recommend_message",
        PROMPT_BUY_CHAT_DATA_ID: "chat_buy_message",
        PROMPT_CONSULT_CHAT_DATA_ID: "chat_consult_message",
        PROMPT_KNOWLEDGE_CHAT_DATA_ID: "chat_knowledge_message",
        PROMPT_UNKNOWN_CHAT_DATA_ID: "chat_unknown_message",
        PROMPT_TEXT_CHAT_DATA_ID: "chat_text_message",
        PROMPT_A2A_CHAT_DATA_ID: "chat_a2a_message",
    })

    _snapshots: dict = field(default_factory=dict)  # 用于存放 MD5 快照
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        self.client = nacos_config.get_config_client()

        # 初次加载
        self.load_all_configs()

        # 启动热更新线程
        threading.Thread(target=self._watch_loop, daemon=True).start()
        logger.info("[HOT-UPDATE] 配置热更新线程已启动")

    # -------------------------------------------------
    # 初始化读取
    # -------------------------------------------------
    def load_all_configs(self):
        for data_id_name, attr_name in self._config_map.items():
            data_id = config_manager.get(data_id_name)
            self._load_single(data_id, attr_name)

    def _load_single(self, data_id, attr_name):
        try:
            value = self.client.get_config(data_id, "DEFAULT_GROUP", timeout=5) or ""
            with self._lock:
                setattr(self, attr_name, value)
                self._snapshots[data_id] = md5(value)
            logger.info(f"[INIT] 加载成功: {data_id}")
        except Exception as e:
            logger.error(f"[INIT] 加载失败 {data_id}: {e}")

    # -------------------------------------------------
    # 轮询线程（每 60 秒检查一次）
    # -------------------------------------------------
    def _watch_loop(self):
        while True:
            time.sleep(60)
            self._check_updates()

    def _check_updates(self):
        for data_id_name, attr_name in self._config_map.items():
            data_id = config_manager.get(data_id_name)

            try:
                new_val = self.client.get_config(data_id, "DEFAULT_GROUP", timeout=5) or ""
                new_md5 = md5(new_val)

                # 是否变化？
                if self._snapshots.get(data_id) != new_md5:
                    with self._lock:
                        setattr(self, attr_name, new_val)
                        self._snapshots[data_id] = new_md5

                    logger.warning(f"[HOT-UPDATE] 发现变更: {data_id} → {attr_name}")

            except Exception as e:
                logger.error(f"[HOT-UPDATE] 检查失败 {data_id}: {e}")


system_prompt_config = SystemPromptConfig()