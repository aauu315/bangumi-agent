import argparse

parser = argparse.ArgumentParser()              # 创建参数解析器
parser.add_argument("subject_id", type=int)      # 要求输入一个整数
args = parser.parse_args()                       # 读取命令行输入

from bangumi_agent.api_client import get_subject

subject = get_subject(args.subject_id)

print(subject["name_cn"])
print(subject["eps"])
print(subject["summary"])