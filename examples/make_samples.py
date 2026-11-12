"""生成几份**完全虚构**的示例简历(.docx),用来试跑。姓名、公司、学校经历、联系方式都是编的。

用法:  python examples/make_samples.py <工作目录>/简历/待处理
"""
import sys
from pathlib import Path

SAMPLES = {
    "示例简历-张三.docx": """张三 个人简历
电话:000-0000-0001  邮箱:zhangsan@example.com  现居:长沙

教育经历
2012.09-2016.06 清华大学 计算机科学与技术 本科 统招全日制

工作经历
2019.07-至今 示例科技有限公司 高级后端工程师
负责订单系统的微服务拆分,使用 Python 和 Go,维护 Kubernetes 集群。
2016.07-2019.06 样板软件有限公司 后端工程师
负责支付网关开发,曾参与与虚构重工的联合项目交付。

专业技能
Python、Go、微服务、Kubernetes、MySQL、消息队列
""",
    "示例简历-李四.docx": """李四 个人简历
电话:000-0000-0002  邮箱:lisi@example.com  现居:苏州

教育经历
2013.09-2017.06 示例学院 机械设计制造及其自动化 本科

工作经历
2017.07-至今 样板机械有限公司 机械工程师
负责液压系统设计和样机试制。

专业技能
SolidWorks、液压系统、机械设计
""",
    "示例简历-王五.docx": """王五 个人简历
电话:000-0000-0003  邮箱:wangwu@example.com  现居:武汉

教育经历
2014.03-2017.01 浙江大学 工商管理 本科(函授)

工作经历
2010.07-至今 示例商贸有限公司 销售主管
负责华中区域的渠道销售。
""",
    "示例简历-赵六.docx": """赵六 个人简历
电话:000-0000-0004  邮箱:zhaoliu@example.com  现居:上海

教育经历
2015.09-2018.06 浙江大学 机械工程 硕士 统招全日制
2011.09-2015.06 湖南大学 机械设计制造及其自动化 本科 统招全日制

工作经历
2018.07-至今 虚构重工有限公司 结构工程师
负责起重机臂架的结构设计和有限元分析。

专业技能
ANSYS、结构设计、有限元分析、SolidWorks
""",
}


def write_docx(path, text):
    from docx import Document
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    doc.save(str(path))


def main(target):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    for name, text in SAMPLES.items():
        write_docx(target / name, text)
        print("已生成", target / name)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
