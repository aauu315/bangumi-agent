# Anthropic 格式协议夹具

按官方文档的响应格式手写（2026-11-09），不是真实接口抓包。真实调用跑通后，把脱敏后的响应另存为 `real_*.json`。

| 文件 | 内容 |
|---|---|
| thinking_then_two_tools.json | 思考块 + 文字 + 两个 tool_use（DeepSeek 思考模式的典型回复） |
| final_text.json | 正常结束的文字回复 |
| cut_tool_call.json | 输出达到 max_tokens，tool_use 参数不完整 |
| refusal.json | 安全拒绝，停止原因映射为 other |
