from dataclasses import dataclass, field
from typing import List, Optional

"""
json 数据结构如下：
{
    "orderId": "1986421767380619265",
    "totalAmount": 19900,
    "discounts": [
        {
            "ids": [
                "1901825438733946882",
                "1901825343409999874",
                "1901825496850223105",
                "1901825533789458433",
                "1901825467666255873"
            ],
            "rules": [
                "无门槛抵1元",
                "无门槛抵1元",
                "无门槛抵1元",
                "无门槛抵1元",
                "无门槛抵1元"
            ],
            "discountAmount": 500,
            "discountDetail": {
                "1589905661084430337": 500
            }
        },
        {
            "ids": [
                "1901825496850223105"
            ],
            "rules": [
                "无门槛抵1元"
            ],
            "discountAmount": 100,
            "discountDetail": {
                "1589905661084430337": 100
            }
        }
    ],
    "courses": [
        {
            "id": "1589905661084430337",
            "name": "可能是史上最全的微服务技术栈课程",
            "coverUrl": "/img-tx/dafa5df0b10146a6881d3f26e1d091c4.jpg",
            "price": 19900
        }
    ]
}
"""


@dataclass
class PrePlaceOrder:
    """
    订单预提交信息实体
    """

    count: int = 0
    """课程数量"""

    totalAmount: float = 0.0
    """订单总金额（单位：元）"""

    discountAmount: float = 0.0
    """最大优惠金额（单位：元）"""

    couponName: str = ""
    """优惠券名称"""

    payAmount: float = 0.0
    """实付金额（单位：元）"""

    courseIds: List[str] = field(default_factory=list)
    """课程id列表"""

    orderId: Optional[str] = None
    """订单id"""

    couponId: Optional[str] = None
    """优惠券id"""

    @staticmethod
    def of(data: dict) -> "PrePlaceOrder":
        """
        从字典数据构建 PrePlaceOrder 对象

        Args:
            data (dict): 原始订单数据，包含字段：
                - orderId: 订单id
                - totalAmount: 总金额（单位：分）
                - discounts: 优惠券列表
                - courses: 课程列表

        Returns:
            PrePlaceOrder: 构建好的订单预提交对象
        """
        # 订单总金额：分->元, 保留两位小数
        totalAmount = round(data.get("totalAmount", 0) / 100, 2)

        # 优惠券信息
        discounts_data = data.get("discounts", [])
        discountAmount = 0.0
        couponName = ""
        couponId = "0"

        if discounts_data:
            first_discount = discounts_data[0]
            # 最大优惠金额
            discountAmount = round(first_discount.get("discountAmount", 0) / 100, 2)

            # 优惠券名称
            rules = first_discount.get("rules", [])
            if len(rules) >= 2:
                couponName = f"叠加{len(rules)}券：【优惠{discountAmount}元】"
            else:
                couponName = f"单券：【{rules[0] if rules else ''}】"

            # 优惠券id
            ids = first_discount.get("ids", [])
            couponId = ids[0] if ids else "0"

        # 实付金额，保留两位小数
        payAmount = round(totalAmount - discountAmount, 2)

        # 课程ID列表
        courses = data.get("courses", [])
        courseIds = [c.get("id") for c in courses]

        # 课程数量
        count = len(courses)

        return PrePlaceOrder(
            count=count,
            totalAmount=totalAmount,
            discountAmount=discountAmount,
            couponName=couponName,
            payAmount=payAmount,
            courseIds=courseIds,
            orderId=data.get("orderId"),
            couponId=couponId
        )