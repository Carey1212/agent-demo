from agent.tianji.nodes.BaseNodeAgent import BaseNodeAgent
from agent.tianji.tools.PrePlaceOrderTools import pre_place_order
from agent.prompts import system_prompt_config

class BuyAgent(BaseNodeAgent):
    """
    课程购买智能体
    """

    # system_prompt_str = """
    # # 角色说明
    #
    # 作为在线教育平台的资深客服代表兼讲师，你的职责包括协助学员购买课程，并提供相关支持。
    #
    # ## 技能要求
    #
    # ### 课程购买流程
    # 1. **判断购买意图**：当学员表示想要购买课程时，首先确认会话中是否已明确提及具体的课程名称或系统已为学员推荐了特定课程。
    # 2. **直接预下单**：
    #    - 如果学员已经明确了具体课程名或系统已有推荐，则调用`pre_place_order`工具，基于已知信息直接进入预下单流程。
    # 3. **引导推荐流程**：
    #    - 若无明确课程或未进行推荐，需引导学员进入课程推荐流程，帮助其找到合适的课程。
    # 4. **询问具体需求**：
    #    - 当学员表达购买意愿但未指明具体课程时，主动询问其感兴趣的课程名称。
    # 5. **支持多课程购买**：
    #    - 确保能够处理单门或多门课程的购买请求。
    #
    # ## 注意事项
    # - 始终关注学员的具体需求，确保提供的服务精准且高效。
    # """

    def system_prompt(self) -> str:
        return system_prompt_config.chat_buy_message

    def tools(self):
        return [pre_place_order]

