from langgraph.graph import MessagesState


class RouteState(MessagesState):
    intent: str # 意图
    # messages: Annotated[list[AnyMessage], add_messages] #继承