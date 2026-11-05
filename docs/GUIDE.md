# 使用说明

**简体中文** · [繁體中文](GUIDE_ZH_TW.md) · [English](GUIDE_EN.md)

[← 回到首页](../README.md)

这里是完整说明：每个功能试到哪一步、每个平台的注意事项、安装和手动流程。

## 新手完整提示词

在中国大陆使用 YouTube、Instagram 或 X，需要自己想办法哦。

把方括号里的内容换成你的，再把整段发给能运行本机命令的 AI 助手。暂时不知道的留空，助手只问缺少的信息；不要把密码填进去。

> 我不熟悉电脑，请用简短的话带我完成第一次发布。
>
> 请从 https://github.com/whitecarp/publishops-crosscast 安装 CrossCast，使用独立 Python 虚拟环境，不改动其他 Python 程序的环境。
>
> 先运行 vpub doctor，读取它给出的 skill，并解释需要处理的问题。根据已有偏好和这段对话，一次只问还不知道的设置：我想用哪些平台、是否已有这些平台的账号；若要用 YouTube/Instagram/X，这些网站是否能访问；若选 X，是否已订阅且确实能进入 Media Studio；以后是否每次发布前都先给我看。
>
> 记住我选择的平台和“每次先给我看”的偏好，不保存账号名、所在地或访问方式。只为我选的可用平台运行 onboard。没有 X 订阅或实际 Media Studio 权限，就先不选 X。
>
> 登录由我在官方页面亲自完成；不要向我要密码、Cookie、令牌或验证码。需要平台验证时停下来等我处理，不要反复尝试。
>
> 视频：[完整文件路径]；封面：[路径，没有就写没有]；目标平台：[我选的平台]；视频语言：[语言]；是否用 AI 制作：[是/否]；内容：[真实内容简述]；谁能看：[公开/私密/账号设置]。不清楚的必要信息请先问我，不要默认全平台或公开。
>
> 用 prepare 准备文案和封面，保留原文件。Instagram 和 X 跟随账号的公开范围；Instagram 封面要在手机上换。先做 go --dry-run 本地预演，展示各平台的文案、账号、公开范围、AI 声明和封面安排。每个平台首次发布都要等我看过当前方案并说“发”。确认码不代表同意，不要自动代填审批参数。
>
> 该平台之后按我的发布指令执行；只有我保存了“每次先给我看”或临时要求预览，才每次等我看过方案。方案有实质变化仍要重新核对。偏好不是长期发布授权，CLI 命令执行中不要弹交互问题。
>
> 完成后给我已核验的作品链接，结果不明的单独说明。超时不要盲目再发，先看原任务回执并按状态继续。
>
> 最后在 CrossCast 和 bili-cli 的数据目录（VPUB_HOME、BILI_CLI_HOME 或各自默认位置）之外建立反馈文件夹，只放 doctor、status 和相关 receipts 的脱敏摘录、复现步骤与必要的脱敏对话片段。输出可能含账号 ID、私密文案和路径，不要直接复制。不要导出任何一个数据目录、登录资料、浏览器资料、完整聊天或原始回执。让我检查后，再由我决定是否分享。

助手可用 `vpub preferences --platforms youtube,instagram --show-first yes` 保存你实际选择的平台和每次预览偏好；这里只是例子，不要照搬平台。`vpub preferences --show-first no` 关闭该平台后续每次预览；当前本地数据目录里，每个平台的首次发布仍要展示并等我同意。这不按账号或频道分别记录。只填写你要改的选项。启用每次预览后，助手应按当前方案的确认码执行，方案变化后旧确认不能复用；不会在命令运行到一半时问你。

### 反馈前先脱敏

1. 在本机查看 `vpub doctor`、`vpub status` 和 `vpub receipts`，不要为收集反馈重新发布或登录。**这些输出不是自动脱敏的，可能含账号或作品 ID、私密文案和文件路径。**
2. 在 CrossCast 数据目录（`VPUB_HOME`）和 bili-cli 数据目录（`BILI_CLI_HOME` 或默认位置）之外新建反馈文件夹。仅保存版本、系统、命令、复现步骤、预期与实际结果、错误码，以及本次问题需要的脱敏摘录。账号名、ID、作品链接、邮箱、私密文字和本机路径改为一致的占位符；移除密码、Cookie、令牌和验证码。
3. 对话只摘录复现必需的片段并逐条脱敏。**不要提交 CrossCast 或 bili-cli 的整个数据目录、登录资料（包括 `credential.json` 和 `pending_login.json`）、浏览器资料、完整聊天导出、原始日志或未经脱敏的回执。**
4. 自己打开反馈文件逐项检查，再决定是否提交到 [GitHub Issues](https://github.com/whitecarp/publishops-crosscast/issues)。助手不应自动上传反馈。

bili-cli 的登录资料独立存放：若设置了 `BILI_CLI_HOME`，使用该目录；否则 Windows 使用 `%APPDATA%\bili-cli`（未设置 `APPDATA` 时使用用户主目录下的 `bili-cli`），macOS/Linux 使用 `$XDG_CONFIG_HOME/bili-cli`（未设置 `XDG_CONFIG_HOME` 时使用 `~/.config/bili-cli`）。排除 CrossCast 数据目录并不会自动排除这些位置；它们都不能放入反馈包。

## 🎬 发一条视频是什么样

> **你**：把 final.mp4 公开发到这五个平台，标题口语一点。

- ✍️ **自动写文案**：B站、抖音写简体中文，YouTube、Instagram、X 写英文；视频是别的语言时，先保留原文，再附译文<br>YouTube 按视频内容选好分类；长度、标签都检查过，不夹带外链
- 🖼️ **自动配封面**：横版：B站 16:10，YouTube、X 16:9<br>竖版：AI 助手自动生成一张竖版图，抖音和 Instagram 共用；视频保持原样
- 🧾 **自动出方案**：列清平台、账号、公开范围和 AI 声明，生成确认码
- 🔁 **先预演**：`--dry-run` 走一遍，不碰平台
- 📮 **后台提交**：登录后自动在后台提交视频

> **AI 助手**：五个平台都提交好了。已经读回到作品的标成“已核验”，还在审核的，通过后我再更新。Instagram 的封面请在手机上换成那张竖版图。

<sub>流程示意，不是真实发布记录。</sub>

## ✅ 能做什么，试到哪一步

这张表只算免费版。有些真实记录来自更早的版本，也都是作者自己的账号，不代表在你的账号、地区和平台状态下一定成功。

✅ 在真实账号上跑通过 · 🟡 做好了，还没在真实账号上确认

**发布时**

- **公开发布并读回**：✅ YouTube、抖音、Instagram、X · 🟡 B站
- **定时发布**：🟡 B站、YouTube、抖音、X
- **存草稿或停在预览**：✅ B站、YouTube、抖音、X · 🟡 Instagram
- **上传时加入合集或播放列表**：🟡 B站、YouTube、抖音
- **上传时带章节**：🟡 B站、YouTube、抖音

**发布之后**

- **修改已发布的作品**：✅ YouTube、Instagram · 🟡 B站、抖音、X
- **删除一条已发布作品**：🟡 YouTube、抖音、Instagram、X；B站不支持
- **认领以前发的作品**：✅ B站、抖音、Instagram、X · 🟡 YouTube
- **作品列表和数据**：✅ 全部五个平台
- **读评论并回复**：✅ 全部五个平台
- **管理单条评论**：✅ YouTube、抖音、X · 🟡 B站、Instagram
- **@ 提醒**：✅ B站、抖音、X · 🟡 Instagram
- **单条私信**：✅ 抖音、Instagram、X · 🟡 B站
- **点赞和收藏**：✅ 全部五个平台
- **管理合集和播放列表**：✅ 全部五个平台
- **互动组件**：🟡 B站

<sub>✅ 表示这一行的主要动作至少跑通过一次，不是每个细项都试过。比如 YouTube 的单条评论管理，确认过的是置顶和取消置顶；私信确认的是发送方看得到，不代表对方已收到。</sub>

<sub>🟡 的功能已经写好，也用假平台测过，但真实页面上可能还有没发现的问题。每一格的依据见[验证记录](VALIDATION.md)，更细的范围见[功能范围](FEATURES.md)。</sub>

<sub>表里没列出的平台，就是这个平台没有这项，或免费版不提供。定时发布还没有一条真正走到发布时间。</sub>

## ✨ 每一项怎么工作

<details>
<summary><b>✍️ 自动写好每个平台的文案</b></summary>


B站、抖音用简体中文，YouTube、Instagram、X 用英文。视频本身是别的语言时，先保留原文，再附上平台语言的译文；中文内容在 B站、抖音只写简体。文案由你正在用的 AI 助手来写，vpub 给出写作要求并检查长度和格式，不内置模型，也不需要另配模型 API Key。YouTube 的分类按视频内容来选。

</details>

<details>
<summary><b>🖼️ 封面自动适配各平台画布</b></summary>


按各平台画布另存封面，完整保留画面，不裁切、不拉伸、不覆盖原图：B站 16:10，YouTube 16:9，X 通过 Media Studio 设置横版封面。抖音和 Instagram 的竖版封面，由 AI 助手（如 Codex）自动生成一张专用竖版图，两边共用，不会拿横版图加黑边凑数。没有 AI 画图时，`vpub covers portrait` 不用 AI，也能把封面里选定的部分重新排成这张竖版图。视频本身不转竖版，保留原比例。Instagram 的封面要在手机上换。

B站（含存草稿）需要提供封面，或在 PATH 中有 ffmpeg 自动截图。`prepare` 和演练会提前检查；缺少时用 `--cover 图片` 或安装 ffmpeg。找到工具不代表已经验证视频能解码。

</details>

<details>
<summary><b>🧾 先出方案，再动手</b></summary>


每次发布先生成一份方案：平台、账号、公开范围和 AI 声明都写得清清楚楚，并生成一个确认码。视频、封面或文案一改，确认码就变，旧的确认自动作废，保证发出去的就是你确认过的那一版。`--dry-run` 预演不会碰平台。

</details>

<details>
<summary><b>📮 登录一次，之后后台发布</b></summary>


首次登录之后，CLI 自己完成上传：B站走 bili-cli，YouTube、抖音、Instagram、X 由 CLI 在后台操作专用的 Chrome。YouTube、X 和 Instagram 的官方 API 路径需要付费附加组件，附加组件目前还没有开售；这里的其他功能都不需要它。上传时可以带章节（B站、YouTube、抖音），并加入播放列表（YouTube）或合集（B站、抖音）。B站、YouTube、抖音和 X 都支持定时发布；Instagram 不支持，要求定时的方案会被拒绝。`vpub init --draft-only` 只上传、不发布：B站和 YouTube 会留下未发布的草稿，抖音会按它自己的“暂存离开”，之后上传页可能会在“继续编辑”下提供这份草稿（CrossCast 只看不点，因为这个入口说不出是谁的草稿，而且打开它可能会把草稿用掉），Instagram 和 X 则停在预览，预览不一定能保留，所以不会被说成已保存。

</details>

<details>
<summary><b>🔁 中断了也能接着来</b></summary>


上传途中断开，`vpub resume` 接着原来的任务继续：X 接着已经存好的 Studio 素材，YouTube 用付费附加组件时从同一个上传会话续传。X 的浏览器如果在 Media Studio 收完整个文件前就关了，媒体库里没有这个文件名，续传会再上传一次。YouTube 免费路径和抖音会回到上传留下的草稿，从那里接着完成。只有没有它的草稿、且 YouTube 没给过 ID 时，才会重新上传。Instagram 在按下分享前不会留下任何内容，所以停在那之前的尝试会从头开始。如果一直卡在封面，`vpub resume KEY --no-cover` 可以不带封面完成；`--cover 文件` 可以给方案里没有封面的尝试加上封面。结果不明的提交不会自动再点一次。如果 YouTube 读回显示作品仍是草稿，说明什么都没有公开，`vpub resume` 会在同一份草稿上按发布。`vpub status` 只读本地记录，列出没做完的事和下一步的确切命令；`vpub cleanup` 预览可清理的副本和运行临时文件。发布流程保留这些文件，待用户明确要求后用 `vpub cleanup --apply` 清理，不绕过宿主的删除确认。文件改变、所属进程仍在运行或状态不明时会保留。运行临时文件的安全删除目前仅支持 Windows，其他系统保留这些文件。字节数是保守的逻辑大小，不是实际磁盘可用空间增量；保留的硬链接仍可能占用全部空间。

</details>

<details>
<summary><b>🔍 每一步都有回执</b></summary>


每个平台的进度都叫它本来的名字：排队中 → 发布中 → 已提交，待核验 → 已核验。只有从平台页面或 API 读回到作品，才算“已核验”；排队、登录链接和本地回执都不等于发布成功。

</details>

<details>
<summary><b>✏️ 发出去还能改</b></summary>


`vpub edit` 原地修改已发布的作品：B站的标题、简介、标签、封面和章节，YouTube 的标题、简介、缩略图和章节，抖音的标题、简介、竖版封面和章节，Instagram 的配文，X 在出现编辑入口时也能改。不换视频，不改公开范围。不是 CrossCast 发布的旧作品，先用 `vpub ops adopt` 认领，再用 `vpub edit-work` 修改。

X 编辑尚未通过真实账号验收。若结果为 `EDIT_SUBMITTED_UNVERIFIED`，编辑可能已经成功；原链接仍显示旧文字不能证明失败。请在 X 打开帖子并检查当前内容和编辑历史，不要根据这次读回再次提交。

`vpub widgets` 可以在你自己的 B站视频上添加互动组件：关联视频、关注或三连按钮、投票和评分。可以写在方案里，也可以加到已经发布的视频上。视频公开之前 B站不会列出这些组件，所以方案里的组件要等到公开后，由 `vpub verify` 发送。`vpub verify` 和 B站修改也会报告视频上现有的组件。预约组件不包括在内，因为删除它会通知所有已预约的人。这条路径还没有通过 `vpub` 在真实账号上跑过。

</details>

### 删除一条已发布作品

<details>
<summary><b>命令、逐条批准与验证限制</b></summary>

只接受已核验的 CrossCast 发布回执键，不接受平台 ID、网址、认领的旧作品、草稿或结果未确认的发布。先预览目标；每条作品都要单独得到用户明确的“是”。发布许可不等于删除许可。

```sh
vpub delete RECEIPT_KEY --dry-run
# 用户明确同意删除上面这一条作品后，使用预览返回的 CODE
vpub delete RECEIPT_KEY --confirm CODE --person-approved
```

原发布历史保留；`vpub status` 的 `deletions` 区域记录删除状态。结果不确定时，只运行 `vpub delete RECEIPT_KEY --verify-only` 读回，绝不再次删除。

YouTube、抖音、Instagram、X 的四条浏览器删除路线已实现，但尚未在真实账号上试过。B站不支持：没有经核验的自有视频删除 API 或明确的“视频已删除”响应契约。无法核验精确ID的抖音私密作品会拒绝。X、Instagram 的非空个人主页目前不能证明列表完整，即使作品页面显示已消失，也会保持待确认。今后的真实账号测试仍需另行取得用户许可。

只有精确作品的明确消失证据，加上新鲜、完整的本人作品列表中不再有它，才算删除已核验。通用“不可用”、权限错误或网络失败都不算。

</details>

<details>
<summary><b>💬 评论、点赞和私信</b></summary>


读评论、用评论者的语言回复，点赞、收藏，在抖音、Instagram、X 上读写私信，查看 @ 提醒。还能在平台允许的地方对单条评论置顶、隐藏、删除或移除，也能管理你的合集和播放列表（调整顺序属于付费附加组件）。每次只做你授权的那一条，不会自动群发。各平台能做的不一样，详见[功能范围](FEATURES.md)。

</details>

<details>
<summary><b>📊 数据和运营汇报</b></summary>


查看单条作品的播放和互动，巡检多个平台的新评论和数据变化。完整的运营汇报属于付费附加组件。

</details>

<details>
<summary><b>📧 需要你时发邮件提醒（可选）</b></summary>


*属于付费附加组件，目前还没有开售。*

平台要你扫码或验证时，可以给你发一封提醒邮件。只支持 Gmail，默认关闭，你主动要求时才开启。

</details>

## 🆓 免费与付费

发布和日常运营都免费，以 MIT 开源。付费附加组件只多出下面列出的几项，现在还买不到，其他功能也都不需要它。

**免费版**（现在就能用，不需要模型 API Key，也不需要平台的官方 API）

- **发布**：五个平台，读回核验，定时、草稿、章节，上传时加入合集或播放列表，AI 声明
- **文案和封面**：AI 助手为每个平台写文案，vpub 检查并适配封面
- **发布之后**：修改作品，认领旧作品，作品列表和数据，巡检和简短摘要
- **互动**：评论和回复，单条评论管理，@ 提醒，单条私信，点赞和收藏
- **合集和播放列表**：创建、修改、增删作品（调整顺序除外）
- **B站互动组件**：关联视频、关注、投票和评分
- **安全和恢复**：下面这些安全护栏，`vpub status` 和 `vpub resume`

**付费附加组件**（还没开售，单独安装）

- YouTube、X 和 Instagram 的官方 API 路径
- 运营报告（`vpub report`）
- 完整读取（`vpub read-all`）
- 邮件提醒（只支持 Gmail，默认关闭）
- 播放列表调整顺序
- YouTube 待审核评论队列

## 🧭 平台支持

实测记录是过去的真实发布，不代表在你的账号、地区和平台状态下一定成功，详见[验证记录](VALIDATION.md)。这里写的是免费路径；YouTube、X 和 Instagram 的官方 API 路径属于付费附加组件。

- **B站**（B站 App 扫码登录，用内置的 bili-cli 发布）：目前只有作者直接用旧版 bili-cli 的上传，通过 CrossCast 发布和定时都还没试过，`vpub capabilities` 里仍标为未实测。合集和草稿已在真实账号上确认过。
- **YouTube**（在专用 Chrome 里登录一次，后台操作 YouTube Studio）：2026 年 11 月 2 日已公开发布两条视频并读回。两条第一次都只留下草稿，重新打开再按一次发布才发出，现在自动处理这一步的代码还没在真实频道跑过。上传时加入播放列表和定时只在草稿上传里确认过。
- **抖音**（专用官方 Chrome，CLI 后台浏览器）：长时间保持登录还在验收。上传中断后不能续传，但 `vpub resume` 会接着留下的草稿完成。封面窗口曾卡住；只有用户明确选择省略封面时，才可不带封面发布并在手机上补设，程序不会自动省略。Windows 上现在改用隐藏桌面上的真实 Chrome 窗口，这还没试过。标题和简介保存已尝试，但仍未核验成功。
- **Instagram**（专用官方 Chrome，CLI 后台浏览器）：视频公开发出并由 CrossCast 自己读回核验过。封面只能在手机上改，也不能定时发布。
- **X**（专用官方 Chrome，后台操作 Media Studio）：账号必须已订阅并能进入 Media Studio；未订阅账号的视频上限是 140 秒、512 MB，本工具不支持。X 目前有两个版本的网站，CrossCast 两种都支持；真实记录都在旧版上，新版还没有在已登录账号上试过。
- **TikTok**：暂停，CLI 会拒绝 TikTok 的登录和发布请求。
- **还没有的**：TikTok（暂停）、YouTube 卡片和可点击片尾、自动或批量私信与点赞、完整历史导出。

<details>
<summary><b>每个平台的完整记录</b></summary>

- **YouTube**：免费路径还能在后台读取作品、数据、评论和播放列表，回复评论，给视频和评论点赞，修改或删除自己的评论，修改标题、简介和封面，并创建、修改和删除播放列表。目前在真实账号上试过的有：各项读取；创建、修改（名称、说明、公开范围）、删除一个私享播放列表并往里面添加和移除视频；给视频点赞和收藏、给评论点赞（都已撤销）；回复评论；以及修改自己的评论再改回去。修改一个私享视频的标题和简介再改回去，以及置顶评论和取消置顶，也都确认过。CrossCast 在视频自己的页面上读取置顶状态，因为 Studio 的行和菜单在置顶之后仍然只提供“置顶”。删除自己的一条回复试过一次：回复从页面上消失了，但 CrossCast 无法证明 YouTube 已经接受，所以报告为未确认；要等再有一次真实删除显示出成功的回复长什么样，才会改变。上述实测之外的写入不能据此视为已验收；上传另有私享测试和 2026-11-02 公开发布记录，见验证记录。新封面会报告为未确认（`EDIT_SUBMITTED_UNVERIFIED`），因为 Studio 没有可以比对的东西，请自己到 Studio 里看一眼。调整播放列表顺序和待审核评论队列需要付费的 API 附加组件，官方 API 路径也是。免费的后台路径可以按标题把视频加进播放列表，也能定时发布，但只在 Studio 的定时区域明确标识一个 IANA 时区或 UTC 时自动操作。时间按目标日期的时区规则换算，不从电脑时区或数字 GMT 偏移猜测时区。只显示偏移、时区未知或不明确，以及夏令时回拨时重复出现的时间，都会在最后保存前停下，保留草稿供手动设置定时；不要重新上传。播放列表和定时都会在保存前读回确认，时区和时间还会在按下保存前再次核对。这项保守的时区保护只经过离线模拟测试，没有新增真实账号验证证据。真实账号上的存草稿选中了新播放列表，也保留了定时的日期和时间（整刻钟和非整刻钟都试过），并读回确认。这些运行都停在最后保存之前，所以最后保存时把视频加进播放列表和真正保存定时，都还没在那里确认过。只有付费的 API 路径才涉及这两点：Google 项目还在测试模式时，要先把登录用的账号加进“测试用户”；按 Google 的规定，2020 年 7 月 28 日之后创建、又没通过审核的 API 项目，上传的视频会被锁成私享。免费路径都不需要。
- **X**：免费路径只能用 Media Studio。账号必须已订阅并能进入 Media Studio；未订阅账号的视频上限是 140 秒、512 MB，此免费浏览器路径不支持。X 目前有两个版本的网站：已登录的测试账号仍然用旧版，未登录的访客看到的是重建后的新版。CrossCast 的 X 路径两种都支持；如果页面两种都对不上，命令会报错停下，这次尝试的回执会说明有没有东西已经发出去。新版路径来自 X 公开的网页代码，还没有在已登录账号上试过。在旧版上，把帖子移出书签文件夹要通过文件夹页面自己的分享菜单。在真实账号（旧版）上，创建、重命名、删除书签文件夹，把帖子加入文件夹再移出，以及给帖子点赞、收藏都确认过。回复（由后续的 `vpub comments verify-reply` 确认）和删除这条回复也确认过。通过 Media Studio 上传有三次在发帖前停下，什么都没有发出去，因为上传好的文件打不开它的页面；第四次停下是因为 X 要求验证。这一步改过之后，一次草稿走到了 X 的发帖框，正确文件的信息也填好了，之后未发布就删除了。接下来一次定时发布用的上传，在同一个页面等了十分钟后停下，什么都没有排上，原因还没有确认。这一步又改过之后，一次定时发布测试走到了 X 的日历，并显示日历的 Save 只是确认日期，真正的最后一步是写着 Done 的按钮。这一点改过之后，下一次测试按了一次 Done，帖子就出现在 X 的排程页面上，文字、日期、时间都对，也带着视频；到点之前已从那个页面取消，视频也删除了。另一次全新上传的草稿也走到了发帖框，视频和文件对得上，之后未发布就删除了。还没有定时帖子真正走到发布时间；普通帖子已经在真实账号上发出过，详见验证记录。 X 的编辑版本行为尚未在真实账号上验证；原地址显示旧文案或跳到不同帖子 ID，都不能证明编辑失败。`EDIT_SUBMITTED_UNVERIFIED` 表示可能已保存。CrossCast 会拒绝再次编辑这条结果不明的作品，即使加 `--person-approved` 也不能重试；只能用原修改请求做 `--verify-only` 读回，或由用户到 X 查看当前文字和编辑历史并手动处理。
- **Instagram**：视频保留原比例；封面必须在手机上改：打开作品的“编辑封面 → 从相册添加”，选和抖音共用的那张竖版图，再看一眼主页网格的裁切。Instagram 不能定时发布，要求定时的方案会被拒绝。删除自己的评论在真实账号上试过一次：事后重新读取已看不到那条评论，但 CrossCast 无法证明 Instagram 已经接受删除，所以报告为未确认（`COMMENT_UNVERIFIED`）；要等再有一次真实删除显示出成功的回复长什么样，才会改变。
- **抖音**：长时间保持登录的稳定性还在验收；网页上传中断后不能续传，要先检查已有草稿：如果抖音的上传页已经在提供一份留下的草稿，新的上传会在选任何视频之前以 `existing_draft_requires_attention` 停下，请自己在页面上继续或放弃那份草稿，然后再运行一次。真实账号上留下过一次草稿：`vpub resume` 接着完成了之前中断的一次尝试，抖音随后在“继续编辑”下提供了这份草稿，标题和测试时用的完全一致。后来的一次存草稿以 `PREVIEW_READY` 结束，CrossCast 自己也看到了这样的入口，它只作记录，不算读回了草稿。上传前页面已经提供草稿时的停止，还没在那里跑过。创建和删除合集在真实账号上通过了（第一次尝试曾停在上传封面后的一次等待，这个等待现在加长了），上传表单里的合集选择器也列出了真实的合集。私密定时发布已经从卡片上确认过，文字、时间和私密标记都对，并在到点前手动删除，所以还没有一次真正走到发布时间。 **早期版本可能在带换行的简介里留下多余的 `*`：有一个真实视频的公开简介曾显示这些符号。** 这个视频后来已删除，所以已经没有线上的例子。现在简介改为一行一行输入，测试卡片上已经没有这些符号，但还没有检查过新视频的公开页面。如果你以前往抖音发过带换行的简介，请看一眼，并在抖音上或用 `vpub edit` 改正；`vpub edit` 在抖音上的简介保存已在私密测试视频上尝试，但尚未验证成功；审核中的作品会阻止修改。一个真实视频的竖封面已经在 CrossCast 的后台浏览器里改好了，用的是和 `vpub edit` 相同步骤的脚本；重新查看时显示新封面，文字和可见范围都没有变。`vpub edit` 本身在那之前就停下了，停在一个已经修好、但还没在那里重新跑过的等待上。在无头 Chrome 里通过抖音封面窗口的真实尝试，无论是修改还是新发布，大多数都停在这里：按下“完成”后一直在处理，按钮是灰的，什么都没有保存。唯一一次在真实 Chrome 窗口里的尝试一下就通过了。这不算证明，因为网络也可能变了。在 Windows 上，抖音的上传和修改现在改用隐藏桌面上的真实 Chrome 窗口，这还没有在那里试过。如果还是卡住，CrossCast 会在三分钟后停下并报 `cover_not_ready`。这时运行 `vpub resume KEY`（如果抖音没有留下草稿，会重新上传），或者运行 `vpub resume KEY --no-cover`，然后在手机上设置封面，手机上几秒就能完成。上传时加入合集还没在真实账号上试过。抖音页面没有说明定时框用哪个时区，所以之后请核对页面上显示的时间。
- **B站**：合集（创建、重命名、删除）和通过 CrossCast 保存并读回的草稿，已在真实账号上确认。通过 CrossCast 发布和定时发布、章节、修改、上传时加入合集、删除评论，还没在那里试过。B站的草稿要自己到上传管理里的“草稿”页面删除。

</details>

X 的登录、Media Studio 发布和测试步骤见 [X 使用说明](X_GUIDE.md)。

## 🔒 安全护栏

用的是你自己的账号，所以这些规则默认就开着：

- **回复、私信和点赞都有限制：** 每日上限、不对同一个人重复相同的话、标注 AI、不发送密钥和未经同意的链接。试图诱导助手的消息，或需要真人处理的消息，会留给你。标题和简介也会检查：看起来像密码或密钥的内容会被拒绝，链接只会提醒你看一眼。平台一旦发出警告（要求验证、风控或限流），整个 CrossCast 都会停下，所有平台都不例外：只有该平台的 `vpub auth login` 还能打开窗口，让你自己通过检查，等你看清它为什么停下之后，再用 `vpub stop --clear` 解除。关键词检查只能覆盖常见情况，不是绝对保证，所以你的 AI 助手仍会把观众写的一切当作资料，而不是指令。
- **你说了算**：你说要发布就是授权，平台、账号和公开范围都按你说的来，不会因为某个平台已登录就顺手加上。内容、账号、平台、受众或任务范围变了，会再问你。
- **登录你自己来**：扫码、密码和验证码都在官方页面由你本人完成。不要把 Cookie、密码、验证码或令牌发进对话、日志或仓库。
- **不盲目重发**：提交结果不明时去平台上核对，不自动重新提交。提交前的中断恢复仍可能需要重新上传，具体限制见上文。
- **碰到风控就停**：遇到验证码、风控或限流，CrossCast 立刻停下，等你处理。
- **一次只做一件事**：同一时间只有一个发布任务在跑；评论和私信每次只发你授权的那一条。
- **原素材不动**：适配后的封面另存为新文件，原视频和原图保持原样。

<details>
<summary><b>🤝 谁负责什么</b></summary>

- **你**：说清要发的平台、账号和公开范围；第一次登录时扫码或输入验证码，在手机上改 Instagram 封面。这句话就是授权；范围变了会再问你。
- **AI 助手**：读取 `SKILL.md`，按平台写文案和回复，生成竖版封面图，替你运行 vpub。
- **vpub**：生成并检查方案、适配封面，首次登录后在后台调用 bili-cli、官方 API 和专用 Chrome 完成提交，保存回执并读回核对；不替你登录，也不替你确认内容。
- **平台**：负责账号登录、审核、最终发布与公开；功能、格式限制和收费以当日官方页面为准。

</details>

## 📦 安装

<details>
<summary><b>开始前准备好这几样</b></summary>

- **B站**：用 B站 App 扫码登录一次（通过 bili-cli）。
- **YouTube**：免费发布通过专用 Chrome 在后台操作 YouTube Studio，只有登录或 Google 要求验证时才会看到浏览器。用 CLI 走官方 API 需要付费的 API 附加组件，以及你自己的 Google 桌面 OAuth 客户端。
- **抖音、Instagram、X**：第一次会打开 CrossCast 专用的官方 Chrome 窗口，你自己扫码或输入密码、验证码；之后 CLI 在后台复用这个登录。
- **X**：免费路径只能用 Media Studio 发布，账号必须已订阅并能进入 Media Studio。
- **可能会有“新设备”验证**：CrossCast 通过自己的专用浏览器登录，YouTube、抖音、Instagram 或 X 可能把它当成新设备，要求你验证身份或找回账号。请你自己在平台的官方页面完成；AI 助手不会替你回答验证。反复重新登录没有用，已保存的登录之后也可能再次被要求验证。
- **电脑上要有 Google Chrome**：没有的话，运行 `vpub setup --install-browser` 安装。

</details>

<details>
<summary>想自己在终端里装？</summary>

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor        # 检查本机依赖
vpub capabilities  # 查看这次安装提供的发布方式；vpub doctor 会检查缺少什么
```

需要 Python 3.10–3.14。这两条命令都不会登录或发布。登录和手动流程见[安装](#-安装)。

</details>

需要 Python 3.10–3.14 和 Google Chrome。Windows 安装脚本已经实测；macOS、Linux 尚未验收。最好装进单独的 Python 虚拟环境（Windows 安装脚本会自动建好），以免改动电脑上其他 Python 程序在用的组件。

上面的安装命令使用当前 main 分支。现有 v0.16.0 发行包仍要求 Python 3.10–3.12。

<details>
<summary><b>Windows 安装脚本</b></summary>

```powershell
git clone https://github.com/whitecarp/publishops-crosscast.git
cd publishops-crosscast
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

脚本会打印 `vpub.exe` 的完整路径，但不会把它加入 PATH。可以直接使用该路径，或在脚本建立的虚拟环境中运行命令。如果它找不到 Python 3.10–3.14，加上 `-Python` 和合适的 `python.exe` 的完整路径再运行一次。

</details>

<details>
<summary><b>通过 pip 从 Git 安装（需要 Git 和兼容的 Python）</b></summary>

```sh
pip install "git+https://github.com/whitecarp/publishops-crosscast.git"
vpub doctor
vpub onboard --platforms bilibili,douyin,youtube
```

pip 安装不会留下代码目录，所以 `SKILL.md` 在该 Python 环境的文件夹里：`share/publishops-crosscast/skills/publishops-crosscast/SKILL.md`。`vpub doctor` 发现需要处理的问题时，会以退出码 4 和 `ok: false` 结束，`issues` 里的每一项都说明怎么处理。

</details>

<details>
<summary><b>登录各平台</b></summary>

```sh
vpub auth login youtube --method browser
vpub auth login youtube --method api --client-json /path/to/client.json  # 付费 API 附加组件，用它代替上一行
vpub auth login douyin
vpub auth login instagram
vpub auth login x
vpub auth status all
```

登录 YouTube（免费路径）、抖音、Instagram 或 X 时，会打开 CrossCast 自己的 Chrome 窗口。平台可能把它当成新设备，要求验证或找回账号：请你自己在平台的官方页面完成，不要反复重新登录。

登录命令会很快返回，窗口随后打开。请保留窗口，用 `vpub auth status PLATFORM --method browser --wait 5` 查看进度（若指定了 `--account`，两条命令保持一致）。AI 应用终止等待命令后，登录进程可能仍能完成；先查状态，不要再开一次。`login_interrupted` 表示进程已停止且尚未记录完成：关闭遗留的 CrossCast 登录窗口，再运行一次登录，沿用保存的配置。如果应用反复终止进程，请在自己的终端运行这些命令，不必绕过应用限制；这也不会解除平台停止状态。

YouTube 的 OAuth 客户端 JSON 和登录令牌只留在本机，不要提交到 GitHub。

</details>

<details>
<summary><b>想自己动手？看一眼手动流程</b></summary>

```sh
vpub prepare final.mp4 --platforms bilibili,douyin,youtube --language zh-Hans --visibility public --ai-generated yes --about "这条视频讲的是什么" --cover cover.jpg --portrait portrait.jpg
vpub go final-crosscast --dry-run
vpub go final-crosscast
```

`vpub prepare` 不会上传，只告诉你要写什么。AI 助手把文案写进新文件夹里的 `copy.json`。`--dry-run` 不会上传。`vpub go` 可以放心重跑，已经发出去的会跳过。

想在步骤之间停下来，可以手动一步步来：

```sh
vpub init --video final.mp4 --language zh-Hans --out publish.json --platforms bilibili,douyin,youtube
vpub metadata request publish.json --source script.txt --out request.json
vpub metadata apply publish.json --request request.json --response response.json --out localized.json
vpub covers prepare localized.json --source cover.jpg --portrait portrait.jpg --out-dir covers --out ready.json
vpub plan ready.json
vpub publish ready.json --dry-run
vpub publish ready.json --confirm <确认码>
vpub verify RECEIPT_KEY
```

当前本地数据目录里，每个平台首次正式发布，或启用了“每次先给我看”时，`go` 和 `publish` 都会在访问账号前停下。该检查不跟踪每个账号或频道。必须先展示返回方案的完整文案、账号、平台、公开范围、AI 声明、封面及文案警告，停下来等用户明确同意。之后才执行 `vpub go final-crosscast --confirm CODE --person-approved` 或 `vpub publish ready.json --confirm CODE --person-approved`，CODE 必须是当前方案的确认码。单独的 `go --confirm CODE` 不代表已经审核；参数只是调用者声明，软件不能证明用户真的同意。方案变化要重新展示并取得同意。仅存草稿不算首次正式发布；启用每次预览时草稿也要审核。该平台已有一次核验成功的正式发布后，未要求审核的后续发布按现有指令执行。

显式使用原生浏览器交接路径时，先运行 `vpub browser authorize-publish KEY --identity ID`，取得从草稿改为正式发布的新方案。展示它并等用户明确同意后，才运行 `vpub browser authorize-publish KEY --identity ID --confirm CODE --person-approved`。这只授权继续同一份草稿，不会重新上传；旧草稿确认码不能代替新方案的确认码。

`--dry-run` 只做本地检查，不联系平台。缺少登录记录会给出登录命令并以退出码 4 结束；有记录也不等于登录仍有效，真正发布时会核对账号，无需先逐个平台运行状态查询。文件检查只识别容器头，不证明能播放或平台会接受。

`--dry-run` 不会上传。`response.json` 由 AI 助手按 `request.json` 中的模板撰写。中断后用 `vpub resume RECEIPT_KEY` 接着做，用 `vpub receipts` 查看所有回执。

可选：`vpub go final-crosscast --workers 2`（或 `vpub publish ready.json --confirm <confirmation-code> --workers 2`） 会把 YouTube、抖音、Instagram 和 X 两个两个一起发，不再一个接一个。B站，以及走付费插件或你的助手自带浏览器的平台，仍然一个接一个发。任何平台发出警告，仍会停下所有平台。按 Ctrl+C 会等正在上传的平台安全停下，已经发出的记录照样保留，之后 CrossCast 保持停止，直到你用 `vpub stop --clear` 解除。目前只做过离线测试：能快多少、两个隐藏浏览器占多少内存，还没在真实账号上测过。

</details>

## 💰 费用与隐私

- **模型**：CrossCast 本身不需要另购模型 API，文案由你正在用的 AI 助手完成。
- **可能的费用**：AI 助手的订阅、X 的订阅（免费浏览器发布路径要用 Media Studio），以及 YouTube API 的配额（由 Google 决定）。本项目不承诺免费或无限使用。
- **本地数据**：登录会话、任务、设置和回执都保存在本机应用数据目录，请不要提交到 GitHub。分享清单、截图或日志前，先移除账号标识、作品 ID、私有路径和凭证。
- **敏感信息**：不要在对话、日志或仓库中提供 Cookie、验证码、密码、令牌或 OAuth 文件。

## ❓ 常见问题

<details>
<summary><b>发布前还要我再确认吗？</b></summary>

当前本地数据目录里，每个平台首次正式发布前，助手都要展示当前方案并停下来，等你说“发”。这按平台判断，不跟踪账号或频道是否首次使用。该平台后续按你的发布指令执行；保存了“每次先给我看”或临时要求预览时，每次都要等你同意。需要审核时，当前 `--confirm CODE` 必须与单独的 `--person-approved` 一起使用；确认码本身不代表同意，参数也只是调用者的声明，软件无法证明你真的说过“可以”。内容、账号、平台、受众或范围改变，仍需确认。偏好不等于长期发布授权。

</details>

<details>
<summary><b>YouTube 一定要配 OAuth 客户端吗？</b></summary>

只有付费的 API 路径需要：API Key 本身不能授权上传，必须用“桌面应用”OAuth 客户端在 Google 页面授权一次。免费路径由后台 Chrome 在 YouTube Studio 上发布，不需要 OAuth 客户端。授权在你自己的电脑上完成，令牌只留在本机。

</details>

<details>
<summary><b>X 没订阅能发吗？</b></summary>

免费浏览器路径不能：它只通过 Media Studio 发布 X，账号必须已订阅并能进入 Media Studio。付了费或登录了，不代表 Media Studio 已经开通，上传前会先核对。官方 API 路径属于独立的付费附加组件，目前尚未开卖；这不代表普通免费账号或 Basic 账号已受支持。

</details>

<details>
<summary><b>Instagram 的封面为什么要在手机上改？</b></summary>

实测下来，要在手机 App 里“编辑封面 → 从相册添加”才能换上和抖音共用的竖版图。AI 助手会准备好这张图，手机上的操作由你完成。

</details>

<details>
<summary><b>能发不是 AI 生成的视频吗？</b></summary>

能。告诉 AI 助手视频是不是 AI 生成的：是的话，各平台都会勾上 AI 生成内容声明（X 上是帖子文字里的一行）；不是的话，就按普通视频发布，不加 AI 标注。

</details>

<details>
<summary><b>只在终端里运行 vpub，能直接发出去吗？</b></summary>

能。首次登录之后，CLI 自己在后台完成上传，不需要 AI 助手一步步操作浏览器。登录过期或平台要求验证时，需要你回到官方页面处理。

</details>

<details>
<summary><b>提交结果不确定怎么办？</b></summary>

用 `vpub verify` 读回核对，或用 `vpub resume` 接着原来的任务，不要盲目重新上传。本地回执可以用 `vpub receipts` 查看，但它本身不代替平台证据。

</details>
