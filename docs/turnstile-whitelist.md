# Turnstile 回调白名单：怎么申请

2026-11-05 查阅。给 S8-02、S8-03 用。这里只整理资料，**还没有提交申请**；什么时候提交由用户决定。

## 官方怎么说

没有单独的申请页面或表单，也没有查到审核标准。官方说明只有 `GET /p1/turnstile` 接口描述里的一句：

> 为防止滥用，Redirect URI 为白名单机制，如需添加请提交 PR。

- 接口文档：`https://next.bgm.tv/p1/`（本地副本 `work/bangumi-p1-openapi.json`，搜 `/p1/turnstile`）
- 白名单代码：[`bangumi/server-private` 的 `routes/private/routes/auth.ts`](https://github.com/bangumi/server-private/blob/master/routes/private/routes/auth.ts)，数组 `allowedRedirectUris`，查阅时在第 34 行
- 判断方式：回调地址只要以白名单里的某一项开头就算通过，否则接口返回错误，提示可以提交 PR

## 别人是怎么申请的

| PR | 项目 | 回调地址 | 提交 → 合并 | PR 说明 |
|---|---|---|---|---|
| [#1784](https://github.com/bangumi/server-private/pull/1784) | bangumi-cmp | `bangumi-cmp://` | 2026-08-19 当天 | 无 |
| [#1765](https://github.com/bangumi/server-private/pull/1765) | Kaku | `kaku://auth/turnstile` | 2026-08-08 → 08-11 | 中英文各一段：在做移动端 App、申请加入白名单、项目地址 |
| [#1694](https://github.com/bangumi/server-private/pull/1694) | AniMikan | `animikan://api/bangumi/turnstile/callback` | 2026-06-11 → 07-02 | 无 |

这三个 PR 都只改 `auth.ts` 一个文件，增加 2 行：一行项目地址注释，一行回调地址。

Kaku 的同名 PR [#1764](https://github.com/bangumi/server-private/pull/1764) 当天关闭，随后提交的 #1765 被合并，原因未查。

## 申请时要改的内容（示例）

回调地址还没定，下面只是格式：

```ts
  // https://github.com/aauu315/bangumi-agent
  'bangumi-agent://turnstile/callback',
```

PR 说明可以写：项目是什么、地址、回调用途（用户本人通过验证后回到应用发布内容）。

## 提交前要确定

1. **回调地址**：手机用自定义协议没有问题；电脑端要选自定义协议还是 `http://127.0.0.1`，后者没有先例，白名单里现有的 https 地址只有一个远程服务。选型见 PLAN 的 S8-02。
2. **一次覆盖电脑和手机**：判断是按开头匹配，同一个前缀可以都用。
3. **项目状态**：上面的申请没有公开的门槛。但我们会把用户的令牌用于发布内容，最好等 README 和实际功能都能说明用途。
4. **重新核对**：提交前再看一次最新的 `auth.ts` 和接口描述，规则可能已变。
