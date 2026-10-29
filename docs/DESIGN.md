# CrossCast 观众页设计说明

- 定位：静态项目介绍，不是发布控制台。没有登录、真实发布按钮或表单，不收集密钥，无外部 CDN/字体/遥测。
- 结构：品牌头图 → 一句话价值 → 可复制的自然语言安装指令 → “你说 → AI 处理”场景 → 平台支持表 → 三步上手 → FAQ（原生 `details`）→ GitHub。
- 视觉：深色创作者工具风，粉→紫渐变作为品牌色；各平台用自身识别色小圆点。状态标签分三档：绿色（直接路径）、琥珀色（宿主浏览器接力）、灰色（不支持）。
- 文案：默认简体中文（`zh-CN`），`site.js` 内置繁體中文（`zh-TW`）与 English（`en`）词典；顶部“简 / 繁 / EN”按钮即时切换正文、FAQ、标题与描述、复制反馈、`aria-label` 和安装指令，并同步 `aria-pressed`。选择保存在 `localStorage`（`crosscast-lang`），也支持 `?lang=zh-CN|zh-TW|en`。文档链接分别指向 `README.md`、`README_ZH_TW.md`、`README_EN.md`。不启用脚本时页面仍是完整简体中文。
- 诚实边界：明确写出 alpha 状态、五站全自动发布未验收、排队≠发布成功、CLI 只准备交接、TikTok 与评论/数据功能暂不支持。
- 可访问性：跳转链接、可见焦点环、表格横向滚动区域可聚焦、复制结果通过 `role=status` 播报，失败时自动选中文本便于手动复制；`prefers-reduced-motion` 关闭动画；≤720px 导航改为非吸顶，避免遮挡内容。
- README 头图 `assets/hero.svg`：纯 SVG、仅用系统字体回退栈，无脚本与外链，可在 GitHub Markdown 中直接显示。
