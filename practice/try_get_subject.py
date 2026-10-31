from bangumi_agent.api_client import get_subject

subject = get_subject(638497)

print(subject["name_cn"])
print(subject["eps"])