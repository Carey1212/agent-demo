import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class CourseInfo:
    """课程信息实体"""
    id: Optional[int] = None # 课程id
    name: Optional[str] = None # 课程名称
    price: float = 0.0  # 单位：元
    validDuration: Optional[int] = None # 有效期
    usePeople: Optional[str] = None # 使用人群
    detail: Optional[str] = None # 课程详情

    @staticmethod
    def of(data: dict) -> Optional['CourseInfo']:
        """
        从 dict 构建 CourseInfo
        """
        if data is None:
            return None

        # 创建 CourseInfo
        course = CourseInfo(
            id=data.get("id"),
            name=data.get("name"),
            validDuration=data.get("validDuration"),
            usePeople=data.get("usePeople"),
            detail=data.get("detail")
        )

        # price: 分→元 四舍五入 2 位小数
        raw_price = data.get("price")
        if raw_price is not None:
            course.price = round(raw_price / 100, 2)
        else:
            course.price = 0.0

        return course

    @staticmethod
    def from_str_data(str_data: str) -> "CourseInfo":
        """
        解析类似：
        CourseInfo(id='123', name='abc', price=199.0, validDuration=12)
        """

        # 去掉前缀 CourseInfo( 和 末尾 )
        inner = str_data.strip()[len("CourseInfo("):-1]

        # 匹配 key=value（value 可以是数字或字符串）
        pattern = r"(\w+)=('.*?'|[\w\.\-]+)"
        pairs = re.findall(pattern, inner)

        data = {}
        for key, val in pairs:
            # 字符串：去掉引号
            if val.startswith("'") and val.endswith("'"):
                data[key] = val[1:-1]
            else:
                # 数字自动转成 float 或 int
                if '.' in val:
                    data[key] = float(val)
                else:
                    # 可能是大整数 ID，不要转 int 比较安全，也可以改成 int
                    try:
                        data[key] = int(val)
                    except ValueError:
                        data[key] = val

        return CourseInfo(**data)