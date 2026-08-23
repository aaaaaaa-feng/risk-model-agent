# 开发记录 · 2026-08-23/24

**主题：shadcn/ui 全量迁移收尾 → 对话栏交互修复 → Tofu 嘉年华视觉重构 → 说明文案体系改造**

涉及分支：

- `重构/shadcn组件迁移`（基于 `重构/taste-skill视觉语言`，HEAD `16a109f`）
- `重构/tofu嘉年华风格`（基于上一分支，HEAD `b0f3935`，当前所在分支）

每次提交前均通过四道门：`npm run typecheck && npm run lint && npm run test && npm run build`。

---

## 一、shadcn/ui 迁移收尾（分支 `重构/shadcn组件迁移`）

接上一会话已完成的 6 个阶段（基础设施+ChatInput、Button/Badge/Progress、表单原语、Tabs、Dialog、Sheet），本会话完成剩余阶段：

### 1. 表格（`69138bd`）
- 新增 `ui/table.tsx`（Table/TableHeader/TableBody/TableRow/TableHead/TableCell 结构封装，`Table` 默认挂载 `data-table` 类）。
- 7 处表格全部迁入：HistoryView、RunWorkbench、DecisionWorkbench ×3、ReportView ×2、DataWorkbench。
- `components.css` 全局 `table/th/td` 裸选择器收敛为 `.data-table` 作用域；Markdown 渲染表格走独立 `.markdown` 作用域，不受影响。

### 2. toast → sonner（`0bbcd24`）
- 新增 `src/lib/notify.ts`：签名与旧 `useToast().notify` 完全一致，但改为各调用点直接 import。
- **拆掉 8 处 props 逐级下传**：5 个 hook（useProjects/useSettings/useWorkspace/useProjectData/useRunData）+ 6 个组件 + AppStateContext 的 notify 字段全部移除。
- App.tsx 挂载 `<Toaster position="top-right" unstyled classNames.toast="app-toast">`，样式沿用绿/红实心 11px。
- 删除 `useToast.ts`。

### 3. 死代码清理（`1b23f3c`）
- 移除失效的 `.visually-hidden` 工具类、`--z-toast` 令牌（sonner 自管层级）、过期注释。
- 全量扫描确认 CSS 顶层类与 tsx 引用一一对应。

### 4. 右侧 Agent 面板 → AI 助手卡片风格（`3f32cd8`）
- 新增 `ui/card.tsx`（shadcn Card 家族）。
- AgentChat 重构：空对话显示欢迎卡 + 4 个风控语境快捷提问（点击填入输入框）；底部输入条新增工具行（模型标识 + 重载钮 + 发送钮）；保留 Markdown 流式渲染与赞/踩反馈。

## 二、对话栏交互修复

### 1. 输入区叠压（`47bdedf`）
- **根因**：`.chat-rail .agent-chat` 的 grid 第三行仍钉死 `48px`（矮屏媒体查询 `39px`），新 ChatInput（textarea + 工具行约 110px）被硬压导致叠在一起。
- 改 `auto`；placeholder 缩短为「补充要求或询问阶段…」防窄栏折行裁切。
- 用 Chrome headless 截图验证修复前后对比。

### 2. 模型可切换 + 发送钮缩小（`16a109f`）
- 用户反馈「模型名不能点」。排查确认：只保存了 deepseek 一个 Provider 配置，旧的「多配置才出现下拉」逻辑永远触发不了。
- 新逻辑（AgentChat `modelVariants` + Select）：
  - `m:` 前缀选项 = 当前 Provider 常用模型（deepseek 可选 v4-flash / deepseek-chat / deepseek-reasoner 等），走 `PUT /providers/settings {model}`；
  - `p:` 前缀选项 = 其他已保存 Provider 配置，走 `POST /providers/profiles/{id}/activate`；
  - 无可切换项时退化为只读 Badge。
- 发送钮从 24px 大圆缩到 30px，与重载钮对齐。

## 三、Tofu 嘉年华视觉重构（分支 `重构/tofu嘉年华风格`，`758aa29`）

按用户提供的 DesignFest 模板重构 UI/UX，**LOCAL-FIRST 不破**（字体走 @fontsource 本地包，运行时零 CDN）：

- **令牌**：画布 `#F2F2F0`、面板纯白、行动层纯黑、accent 豆腐紫 `#5C5FEF`、三俏色（绿 `#00C65E` / 橙 `#FF5C00`）令牌；状态色绿→俏绿、琥珀→俏橙。
- **圆角**：体系从 2-10px 翻到 8-32px；Button/Input/Select 胶囊化（后修正，见四）；弹窗/抽屉 24px；头部 / 页签 / 阶段条全部胶囊化（当前阶段为黑色小胶囊嵌在白色胶囊条里）。
- **字体**：`@fontsource/shrikhand`（拉丁显示体）+ `@fontsource/dm-sans`（正文），卸载 `@fontsource/ibm-plex-sans`，数据保留 IBM Plex Mono；中文回退苹方。
- **Welcome**：三条事实栏 → 绿/紫/橙 bento 大卡 + hover 微放大。
- **滚动条**：模板同款 8px 细圆角滚动条。

## 四、设置抽屉修复 + 说明文案体系（`b0f3935`）

### 1. 下拉布局 bug
- **根因**：旧规则 `label > button[role="combobox"] { display: block }` 未分层，覆盖了 SelectTrigger 的 Tailwind `flex`，导致文字居中、箭头掉到下一行。
- 修复：该规则只保留 `width/margin-top`，`display: block` 仅作用于 input/textarea。

### 2. 表单控件圆角回调
- 密集表单里的全胶囊输入框观感不对 → Input / SelectTrigger 从 `rounded-full` 回调为 16px（`rounded-lg`）；对话栏模型 Select 保留胶囊（调用点覆盖）。

### 3. 说明小字全面下线 → Hint 组件
- 新增 `ui/hint.tsx`：问号图标，**hover 即显示气泡**（CSS `:hover`），**点击锁定展开**（`aria-expanded` + `.open`），触屏/键盘可用。
- 全站清扫「标题 + 一行解释这是什么」模式：
  - SettingsDrawer：5 个导航项的说明小字删除；`SettingsSection` 的 `description` 改 `hint`（渲染在标题旁）；「选择后编辑并保存」删除。
  - AgentChat / ProjectSidebar / HistoryView / DataWorkbench ×6 / DecisionWorkbench ×4 / ReportView ×7 / RunWorkbench ×2：标题下说明段全部转为 Hint。
  - 保留：空态/错误态等动态状态文案（属功能信息，非装饰说明）。
- 清理失效 CSS：`.settings-section > p`、`.settings-nav-item span`、`.network-note p`、`.chat-head span`、`.stage-line p` 等。

---

## 当前状态与待办

- 首屏已经 headless Chrome 截图验证（胶囊头部/页签/阶段条、紫色链接、俏色徽章均生效）。
- **未目检**：Welcome bento 卡（应用自动选中项目，需清选择状态才可见）、各弹窗/抽屉实际打开效果、Hint 气泡在极窄位置的溢出情况。
- 模型清单为按厂商公开型号整理的前端常量；若某型号账号无权限，发送时会返回 API 错误，下拉换回即可。
- 预览：dev server 当时在 http://127.0.0.1:5174（5173 为用户自有进程）。
