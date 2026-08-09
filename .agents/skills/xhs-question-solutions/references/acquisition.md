# 数据获取

来源优先级：当前环境内的小红书专用 Agent/Skill/连接器；已登录浏览器中的公开可见内容；用户提供的 JSON/JSONL。

只有 URL 时，可参考 [chenxiachan/xhs-claude-skills](https://github.com/chenxiachan/xhs-claude-skills)：复用用户 Chrome 登录态的 Cookies，以 HTTP 请求读取页面，从 `window.__INITIAL_STATE__` 提取笔记正文、媒体和互动计数。该项目不提供评论正文采集，不要据此推断评论接口。

获取评论时记录笔记 ID、URL、标题、正文，以及评论 ID、父评论 ID、匿名化作者、正文、点赞数、时间。另记抓取时间、是否截断、可见总数、实际取得数量和失败原因。浏览器采集时逐步滚动并展开可见回复，按评论 ID 去重，达到上限后停止。页面结构不确定时保存原始导出并报告失败，不猜测字段。
