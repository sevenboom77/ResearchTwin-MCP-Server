# OpenTrek Skill setup

明天在 Skills Hub 安装生成的 Skill，再执行工具转化（API/MCP），绑定当前 Bailian MCP 的实际 `toolCode` 与 `toolVersion`，最后生成 Agent 行为规则。不要硬编码未来版本号；以控制台当前 MCP 版本为准。

安装后核对：16 个 canonical tool 是否全部出现；工具名非空；Skill 行为是否禁止自动 promote、自动把 search result 变成 Candidate、绕过 `confirm_write`、自动记录普通问答、把 Brief 当 Knowledge。若 OpenTrek 将 array/list 展平为 STRING，保留后端已有 STRING→ARRAY 兼容，不要手改生成字段类型。
