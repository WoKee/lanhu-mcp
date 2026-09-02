---
name: lanhu-xml-plan
description: 输入蓝湖设计图详情 URL（含 image_id），先通过 lanhu MCP 获取截图与 annotations(dp)，再按业务区块逐个规划、确认并实现传统 Android XML；布局优先 ConstraintLayout，shape 样式优先复用项目 View.changeBg()。
---

# 蓝湖 → Android XML（image_id 直连 + annotations(dp) 主路径 + schema 兜底）

## Skill 执行优先级（硬门禁）

当用户消息显式包含 `$lanhu-xml-plan`（或明确点名本 skill）时，必须执行以下规则：

1. **本 skill 规则最高优先级**：
    - 禁止回退到通用转换文档（例如 `ai/ui.md`、`ai/base-ui-convert-rules.md`）作为主流程。
    - 禁止输出“按设计图视觉比例落地”这类非本 skill 数据链路结论。
2. **先分区、再规划、确认后实现**：
    - MCP 数据获取完成后，必须先输出页面业务区块地图并等待用户确认。
    - 区块地图确认后，每次只规划一个区块；当前区块方案未被用户明确批准前，禁止输出 XML/Kotlin 或修改项目文件。
    - 用户最初提出“写 XML”不等于批准尚未展示的区块地图或区块方案；禁止据此跳过确认门禁。
3. **未满足契约即失败**：
    - 已具备继续条件却跳过数据审计、区块地图或当前区块确认，视为未执行本 skill；按本 skill 规则进入“补充信息暂停态”除外。
    - 触发时必须输出：`FAIL_FAST: SKILL_CONTRACT_VIOLATION` 并停止。

## 输入格式（从用户消息解析）

支持任意一种：

- 只给一行蓝湖 URL
- 或 key=value 多行：
    - `LANHU_URL=...`
    - `HOST_ACTIVITY=...`（可选）
    - `HOST_FRAGMENT=...`（可选）
    - `PRESENTATION=auto|inline|fragment|dialog|bottom_sheet|popup|activity|dialog_activity`

### 输入硬要求

- `LANHU_URL` 必须是设计图详情链接并包含 `image_id`（`detailDetach?...&image_id=...`）。
- 若缺少 `LANHU_URL`：仅提示补充后停止。
- 若缺少 `image_id`：`FAIL_FAST: IMAGE_ID_MISSING_IN_URL`。

## 必须使用的 MCP 工具（主路径）

必须调用（禁止无工具猜测）：

- `lanhu_get_ai_analyze_design_result(url, image_ids)`
- `lanhu_get_design_annotations(url, image_id)`
- `lanhu_get_design_slices(url, image_id)`

以上工具应优先通过当前任务已经注入的 MCP tool call 调用。主路径不自行建立 MCP 连接；仅在下述有限恢复分支中，才允许使用当前已配置的只读 STDIO Client。不得把 `fastmcp list`、`tools/list` 输出或任意本地脚本输出当作设计数据。

## MCP 工具可见性与有限恢复（主路径前置）

当前阶段的 `required_tools` 固定为 `lanhu_get_ai_analyze_design_result`、`lanhu_get_design_annotations`、`lanhu_get_design_slices`，结果键为 `target_key=(url,image_id)`。

任务工具目录与 MCP 服务传输是两个独立状态。当前任务暂时看不到上述任一必需工具，或首个未完成调用对应的工具不可见时，不得仅凭工具缺失断言 lanhu-mcp 未启动，也不得立即进入 `web_schema_fallback`、猜测几何或终止整个工作流。原生工具虽可见、但在返回结构化结果前发生 `initialize`/连接关闭等传输错误时，也按同一恢复分支处理；已经返回结构化 `CallToolResult` 的鉴权、参数、业务或数据错误不属于目录恢复。恢复标记已建立后，后续传输错误留在同一恢复轮处理，不得重新建立 pending 状态。

1. 该恢复分支只适用于阶段 1 的三项数据证据集尚未完整时，固定记录 `recovery_stage=stage1`。目录缺失不重试：直接记录 `task_tool_catalog=unavailable`，建立临时 `mcp_recovery_pending`，保存 `stage1_data_pending`、已成功结果和当前 `target_key`，并输出 `MCP_TOOL_CATALOG_PENDING`。目录可见但原生调用在返回结构化 `CallToolResult` 前发生传输错误时，先在当前 Client 立即重试一次；仍失败才保留 `task_tool_catalog=available` 并建立/保留同一临时标记。该标记不是业务审批状态；“继续/重试”只触发恢复轮，不表示区块、方案或 Preview 已批准。更换 URL 或 `image_id` 时废弃旧恢复轮；三项数据完整后，后续阶段不因目录变化重新拉取。
2. 仅在 `mcp_recovery_pending` 有效且收到明确“继续/重试”时，执行一次目录重查并计入 `catalog_refresh`。若此前目录为 `unavailable` 且重查后包含全部尚未完成工具，记录 `task_tool_catalog=recovered_via_continue`；若此前为 `available`（仅传输错误），保留 `task_tool_catalog=available`。目录重查只更新目录字段，不据此提前写 `mcp_transport_status` 或 `mcp_access_mode`；随后立即从首个未完成工具继续原生调用并复用同键结果。每项取得可解析的结构化 envelope 后才确认该次传输健康；若三项均由原生结果补齐，记录 `mcp_transport_status=healthy`、`mcp_access_mode=native_task_tools` 并清除 pending，再按阶段 1 的数据规则校验。目录重查计入后不得再次刷新或建立 pending；原生即时重试仍失败时直接进入尚未使用的 STDIO 探测，无可用 launcher 时 `FAIL_FAST: MCP_RECOVERY_EXHAUSTED`。
   - 目录重查发生即记录 `mcp_recovery_steps=catalog_refresh`；若重查后仍缺工具，不发起不可见的原生调用，直接进入步骤 3。STDIO 介入后将该字段更新为 `catalog_refresh_then_stdio_bridge`。
3. 目录重查后仍缺工具，或原生调用仍在结构化结果前传输失败，且能读取已配置的 STDIO 启动方式时，最多执行一次受控只读 `stdio_probe`：使用完全相同的 `command`、`args`、`cwd`、`env`（或已确认的 `run_lanhu_mcp_stdio` launcher），以分离参数依次发送 `initialize`、`notifications/initialized`、`tools/list`。不得猜路径、打印 Cookie/环境变量或调用写入工具；Windows 路径不得拼成未经转义的单字符串，STDOUT 只接受 MCP JSON-RPC 帧，诊断留在 STDERR。探测 Client 与后续调用 Client 可以是两个短会话，但必须指向同一已配置的 server/launcher；若 `catalog_refresh` 已计入，不得重新建立 pending 或刷新目录。
4. 只有握手和 `tools/list` 响应可解析，且列表包含本轮尚未完成调用所需工具时，才记录 `stdio_probe=passed`、`mcp_transport_status=healthy`；没有可复用结果时列表必须包含全部三个必需工具。握手成功但缺工具记 `stdio_probe=failed`、`mcp_transport_status=healthy`、`reason=required_tool_missing`；协议/分帧/超时/进程退出记 `stdio_probe=failed`、`mcp_transport_status=unhealthy`。探测成功后立即用同一 launcher 发起未完成的 `tools/call`，不等待下一次“继续”；若调用在结构化结果前失败，保留 `stdio_probe=passed`、覆盖 `mcp_transport_status=unhealthy`，当前 Client 立即重试一次；重试若取得可解析的结构化 `CallToolResult`（包括 `isError=true`），立即恢复 `mcp_transport_status=healthy`，仍失败则按预算结束恢复。`tools/list` 永远不算设计数据。
5. 以 `(target_key,tool_name,canonical_args)` 为结果键复用已成功且仍有效的结果，只调用缺失或明确失效项。三项工具均取得结构化 `CallToolResult` envelope（可合并已复用的原生结果）后，最终记录 `mcp_transport_status=healthy` 并清除 pending；只要至少一项由 STDIO 补齐，就记录 `mcp_access_mode=stdio_recovery`、`mcp_recovery_steps=catalog_refresh_then_stdio_bridge`，再回到阶段 1 证据校验。合法 envelope 的接口错误或 payload 问题仍按截图暂停、schema 兜底或 `FAIL_FAST` 规则处理，不把它们当作传输失败；阶段 1 不得再次拉取已成功工具，证据通过后才继续区块地图、确认和实现门禁。
6. 每个 `target_key=(url,image_id)` 的恢复预算固定为：目录重查最多 1 次、STDIO 探测最多 1 次、STDIO 恢复调用最多 1 轮；当前 Client/恢复轮内的瞬时传输错误只立即重试 1 次。切换传输不重置预算，重复“继续”不得重置计数；STDIO 调用重试后仍失败则直接结束，不再追加刷新或等待下一次“继续”。`mcp_retry_count` 只累计额外的传输重试次数，不包含 `catalog_refresh`、`stdio_probe` 或结构化业务错误；`mcp_calls` 逐项记录工具、方式、参数键、顺序、尝试次数和 `reused|success|error`。无法启动/握手/列出所需工具，或预算内无法完成恢复调用时，输出 `FAIL_FAST: MCP_RECOVERY_EXHAUSTED` 及 `reason=native_call_transport_failed|stdio_transport_unhealthy|required_tool_missing|stdio_call_transport_failed|recovery_budget_exhausted` 后停止；已配置的本地 STDIO 服务不要求用户每轮手动启动。

## URL 与工具路由（强制）

设计稿 URL（`/detailDetach` 且含 `image_id`）仅走 image_id 精确链路：

`lanhu_get_ai_analyze_design_result(image_ids)` -> `lanhu_get_design_annotations(image_id)` -> `lanhu_get_design_slices(image_id)`

- 禁止通过 `design_name/design_names` 选择画板。
- 禁止为“筛选画板”调用 `lanhu_get_designs`。
- PRD/原型和邀请链接不属于本 Skill；提示用户改用包含 `image_id` 的设计图详情链接后停止。

## 数据源策略（强制）

### 主路径数据源（默认）

- 第一优先：`lanhu_get_design_annotations`（结构化标注，`unit=dp`）
- 第二优先：`lanhu_get_design_slices(..., image_id)` 的 metadata（仅补充样式/资源信息；该调用只返回 metadata 和下载 URL，不代表允许下载或写入切图文件）
- `lanhu_get_ai_analyze_design_result(..., image_ids)` 提供实际截图与视觉语义，用于业务区块划分和视觉核对，不参与尺寸计算

### 几何与测量来源（主路径）

- 元素坐标与尺寸：`annotations.layers`（已是整数 dp）
- 自动测量优先使用：`annotations.measurements`
    - `text_container_paddings`
    - `icon_text_distances`
    - `nearest_neighbors`
- 当 measurements 缺项时，才回退到图层几何关系推导间距

### 尺寸与单位规则（主路径）

- `annotations.unit = dp` 时：
    - 固定图标、固定控件、间距、padding 等确认需要固定的几何值，使用 annotations 的同值 `dp` 字面量
    - 绝对坐标用于推断约束拓扑并校验设计宽度下的结果，不要求把每个 `x/y/width/height` 直接写成相对 parent 的 margin 或固定宽高
    - `ConstraintLayout` 的 match-constraints 直接使用 `0dp`
    - Kotlin `View.changeBg()` 的圆角、边框等尺寸保持同一 dp 语义，并在调用处通过 `toPx()` 转为 px
    - 主路径不做二次换算
- 文本尺寸：
    - `text.font_size` 默认采用同值 `sp` 字面量映射到 `android:textSize`，不做二次换算
    - `text.line_height` 仅用于排版审计；默认不映射到 `android:lineHeight`

### 禁止项

- 禁止使用 OCR
- 禁止无来源猜测文本字号或间距
- 禁止在主路径中再次执行 `px -> dp` 缩放

## 网页 schema 兜底链路（仅失败时启用）

仅在对应 `tools/call` 已返回可解析的结构化 `CallToolResult` 后，以下接口或 payload 问题才可触发兜底：

- `lanhu_get_design_annotations` 返回接口错误，或 annotations payload 缺少 `unit/layers`，或其 layers 缺少关键坐标/尺寸且 measurements 也无法补齐
- 用户明确要求本轮落地真实切图，且 `lanhu_get_design_slices` 返回接口错误、项目内又不存在可复用的关键资源

`lanhu_get_ai_analyze_design_result` 已取得结构化 envelope 但返回接口错误，或 payload 中没有可读取的实际截图时，不触发 schema fallback；记录 `screenshot_status=unavailable` 并进入“补充信息暂停态”，等待 MCP 恢复或用户提供截图。若该调用未取得 envelope，按上述恢复分支处理。截图是业务区块划分的必需证据，禁止仅凭 annotations 推定业务区块。

仅因当前任务工具目录暂缺、STDIO 探测成功但原生目录未恢复、或一次瞬时 `initialize`/传输错误，不触发 schema fallback；先按“MCP 工具可见性与有限恢复”处理。只有实际发起 `lanhu_get_design_annotations` 并确认其结构化结果数据缺失/接口失败，才按本节既定条件判断是否启用 schema fallback。

触发兜底后：

- 启用旧 schema 链路（cookie + `project/image` + `store_schema_revise`）
- 仅在此链路使用固定换算：`round(px * 375 / 750)`，即 `750px = 375dp`
- A) 审计区必须标注：`source_mode=web_schema_fallback` 与 `fallback_reason`

### 兜底 cookie 约束

- 认证凭据必须与当前 lanhu MCP 使用同一份完整 Cookie 请求头；禁止在 Skill 中维护第二份或过期副本
- 文件兜底时，从当前 lanhu MCP 仓库根目录已被 Git 忽略的 `.env` 读取单行 `LANHU_COOKIE=...`；禁止把真实凭据写入仓库已跟踪的 `cookie` 文件
- 若无法定位实际 MCP 仓库或 `.env` 中缺少 `LANHU_COOKIE`，按 cookie 缺失处理；禁止猜测旧的固定安装路径
- 日志与输出中禁止回显 cookie 内容

## 硬门禁（强制）

1. URL 缺少 `image_id`：
    - `FAIL_FAST: IMAGE_ID_MISSING_IN_URL`
2. 主路径 annotations 缺关键结构且兜底失败：
    - `FAIL_FAST: PRIMARY_AND_FALLBACK_BOTH_FAILED`
3. 进入兜底后 cookie 文件不存在或为空：
    - `FAIL_FAST: COOKIE_FILE_MISSING`
4. 进入兜底后预检失败或返回非成功 code：
    - `FAIL_FAST: COOKIE_INVALID_OR_EXPIRED`
5. 进入兜底后 schema 拉取失败：
    - `FAIL_FAST: SCHEMA_FETCH_FAILED`
6. 进入兜底后 schema 解析失败：
    - `FAIL_FAST: SCHEMA_PARSE_FAILED`
7. MCP 工具目录刷新与 STDIO 恢复预算耗尽：
    - `FAIL_FAST: MCP_RECOVERY_EXHAUSTED`

默认一律硬失败，禁止静默降级为估算模式。

## 硬约束（必须严格执行）

1) **XML 定义布局结构**：禁止 Compose 和 Kotlin 动态创建 View；允许在已有 Activity、Fragment、自定义 View 或 Adapter 的初始化/绑定位置，复用目标文件现有的 ViewBinding/DataBinding 等访问方式调用项目 `View.changeBg()` 设置背景样式。

2) **XML 直接使用 dp/sp**：
   - 仅当布局角色确认某个具体数值需要固定时，宽高、margin、padding 才使用 `dp` 字面量，例如 `16dp`
   - 宽高首先服从布局策略：内容自适应使用 `wrap_content`，弹性区域使用 `0dp`，宿主要求铺满时使用 `match_parent`；禁止用 source bbox 的固定 dp 覆盖这些策略
   - `android:textSize` 使用 `sp` 字面量，例如 `14sp`
   - 禁止生成或引用 `@dimen/...`，禁止创建、检查、复制或合并 `dimens.xml`

3) **主路径禁止换算**：
   - 当 `annotations.unit=dp` 时直接使用原值，禁止再次缩放
   - 仅 `web_schema_fallback` 使用 `round(px * 375 / 750)`

4) **TextView 默认自适应**：
   - 普通标题、数值、说明、列表文字等内容型 `TextView` 及其子类，`android:layout_height` 默认使用 `wrap_content`；禁止把 annotations 的文本框高度直接写成固定高度
   - 只有 TextView 本身承担按钮、Tab、筛选项、胶囊背景或固定点击区域时才允许固定高度，并在区块方案中标记 `height_strategy=fixed_control` 及原因
   - 默认不输出 `android:lineHeight`、`android:includeFontPadding`、`android:lineSpacingExtra` 或 `android:lineSpacingMultiplier`，沿用 Android 与目标项目的字体度量
   - 只有多行文本存在明确行距要求，且项目已有相同实现或用户明确确认时，才允许设置 `lineHeight`/行距属性，并记录 `line_height_strategy=explicit` 及依据
   - 单行文字的纵向关系优先使用同 top、同 bottom 或相对同一稳定目标的 top+bottom 双向约束，固定控件可使用 `gravity`；尽量不使用 `app:layout_constraintBaseline_toBaselineOf`，只有上下约束无法稳定表达明确的排版基线关系时才允许作为例外；禁止用固定文本高度、`lineHeight` 或 `includeFontPadding=false` 修补对齐
   - 多行文字通过可用宽度、`maxLines`、`ellipsize` 和项目既有换行策略控制，禁止依赖固定高度裁切内容

5) **shape 类背景优先复用 `View.changeBg()`**：
   - 纯色填充：`view.changeBg().setColor(...)`
   - 普通线性渐变：`view.changeBg().setGradient(intArrayOf(...), GradientDrawable.Orientation.*)`
   - 单层边框：`.setStroke(width.toPx(), color)`；需要虚线时使用已有 `dashWidth/dashGap` 参数
   - 统一圆角：`.setRadius(radius.toPx())`
   - 分角圆角：`.setRadii(topLeftRadius=..., topRightRadius=..., bottomRightRadius=..., bottomLeftRadius=...)`
   - annotations 中的 dp 数值传入 `changeBg()` 前必须调用 `toPx()`；不得把 dp 数值裸传给要求 px 的 API
   - 无圆角时显式 `.setRadius(0)`，避免继承公共 `bg_rectangle` 的默认 `2dp` 圆角
   - 目标 View 必须有稳定 ID，并复用宿主现有绑定方式；禁止为此引入 `findViewById` 或动态 `addView`
   - 静态背景在视图初始化阶段设置一次；列表 Item 或动态状态背景在 Adapter 绑定或状态刷新位置设置

6) **项目资源复用与切图延后策略**：
   - selector、ripple、layer-list、inset、多层背景、复杂渐变、精确阴影、内容裁剪、bitmap、nine-patch、vector 等继续使用已有项目资源或 Android drawable
   - 已有项目 drawable/组件能完整覆盖时优先复用，禁止重复创建等价 shape
   - 对设计稿中的真实图片、复杂 icon 和其他 export/slice 图层，先搜索目标模块及其依赖的公共资源；结合语义名称、尺寸/宽高比和视觉内容确认是否可复用，禁止只因名称相似就误用
   - `lanhu_get_design_slices(url, image_id)` 默认只用于读取 metadata 和下载 URL；当前请求未明确要求补齐真实切图时，禁止另行下载、写入或伪造切图文件
   - 上述 export/slice 图层在项目内未找到合适资源时，保留目标 View 的尺寸和约束，使用 `tools:background="@color/..."` 做 Preview 色块，并在资源清单标记为待下载；不得用 `android:background` 把占位色带入运行时
   - 只有用户明确要求本轮补齐真实切图时才允许下载；下载后的格式与资源目录遵循目标模块现有约定，不强制 WebP 或 `drawable-nodpi`

7) **资源命名**：新增资源使用 `ic_xxx` / `img_xxx` / `bg_xxx` 等语义名称，禁止 `image_123`。

8) **字体/颜色（可读性优先）**：
   - 禁止为单个控件创建 style
   - 颜色默认引用现有 `@color/...`；同色 `>=3` 次或全局语义色才建议归并 token
   - 字体判断以 annotations 的 `text.font_family + text.font_weight` 为主路径证据；字段为空时沿用项目默认字体，禁止根据截图观感猜测字体
   - `苹方-简`、Roboto、Android 系统 sans-serif 等界面常规字体沿用项目默认字体；字重按项目既有 `textStyle`/TextView 用法处理，不映射到数字字体
   - 设计稿明确指定特殊字体时，先核对项目 `BindingUtils.font` 与 `TypefaceCache`，只允许按下表精确或规范化匹配后使用 `app:fontType`：

     | 设计字体证据 | XML | 项目字体 |
     |---|---|---|
     | `D-DIN + Exp` / `D-DINExp Regular` | `app:fontType="@{0}"` | `D-DINExp.otf` |
     | `D-DIN + Exp Bold` / `D-DINExp Bold` | `app:fontType="@{1}"` | `D-DINExp-Bold.otf` |
     | `Exo 2 + SemiBold` / `Exo2-SemiBold` | `app:fontType="@{2}"` | `Exo2-SemiBold.ttf` |
     | `Tektur + SemiBold` / `Tektur-SemiBold` | `app:fontType="@{3}"` | `Tektur-SemiBold.ttf` |

   - 规范化匹配只允许忽略大小写以及字体名中的空格、连字符，或合并 annotations 分开的 family/weight；禁止用“都是粗体”“都是数字字体”或字体外观相近作为匹配依据
   - `app:fontType` 只用于 `TextView` 及其子类，且必须位于 Data Binding `<layout>` 根包装的 XML 中并使用 `@{0}` 到 `@{3}` 的整数表达式；禁止写成 `app:fontType="1"`，也禁止使用范围外整数（项目 Adapter 的 `else` 会错误落到 DIN Bold）
   - 新建布局确需 `app:fontType` 时沿用目标模块的 Data Binding 结构；修改既有非 Data Binding 布局时，禁止仅为字体静默迁移根结构，必须在区块方案中标记 `font_layout_mode=needs_input` 并等待确认
   - `fontType=1` 仅表示 `D-DINExp-Bold`，不是通用粗体；选择已包含字重的 `fontType` 后，不再用 `android:textStyle` 重复模拟同一字重
   - 设计明确要求的其他特殊字体若项目映射表无法命中，标记 `FONT_UNRESOLVED` 并等待用户决定新增字体或接受项目默认字体；禁止擅自选择相近 `fontType`

## Android Studio Preview 规则（强制）

1. XML 中使用任意 `tools:*` 属性时，根节点必须声明 `xmlns:tools="http://schemas.android.com/tools"`。
2. 普通页面、Fragment、Dialog 和列表 Item 的根节点禁止生成 `tools:layout_width`、`tools:layout_height`：
   - 蓝湖画板宽高只用于测量和设计宽度下的复原校验，不是 XML 根布局的尺寸契约
   - 根布局尺寸沿用宿主需要的 `match_parent`、`wrap_content` 或项目既有结构；Preview viewport 由 Android Studio 设备配置控制
3. `tools:*` 只服务 Layout Preview，不得替代运行时属性、DataBinding/ViewBinding 数据或真实资源：
   - 动态文本使用设计稿中的示例文案设置 `tools:text`；静态文案仍按项目规范设置运行时文本
   - 已确认可复用的项目图片若仅在运行时动态加载，可用 `tools:src` 展示；静态图片应设置真实的运行时资源引用
   - 项目内尚未找到真实图片/切图时，`ImageView` 或普通 `View` 使用项目已有 `@color/...` 配置 `tools:background`，以直观显示图片区域、尺寸和约束
   - 背景在宿主 Kotlin 中通过 `View.changeBg()` 设置、因而 Preview 无法执行时，应使用项目已有且与目标样式匹配的 `@color/...` 配置 `tools:background`；该占位只预览基础填充，圆角、边框和渐变以运行时为准
   - 运行时默认隐藏但设计稿需要展示的控件可设置 `tools:visibility="visible"`，不得修改其运行时 `android:visibility` 或显隐表达式；Preview 可见不代表运行时约束已验证，仍须执行可见性与整体约束审计
   - `RecyclerView` 的预览方式沿用目标项目现有惯例；项目未使用 `tools:listitem` / `tools:itemCount` 时不得强制引入，可用已有 `tools:background`、`tools:visibility` 等方式展示列表区域
4. Preview 占位色优先使用项目已有的中性色或对应语义色；禁止只为预览新建 color 资源，也禁止编造不存在的 `@color/...`。
5. Preview 属性必须与设计稿元素尺寸、层级和可见状态一致，不得为了让预览“看起来完整”而改变运行时约束、根 viewport 或资源策略。

## ConstraintLayout 与间距规则（强制）

1. **先还原关系拓扑，再消费数值**：
   - 截图用于识别业务语义、视觉分区、重复行列和组合关系；annotations 用于确认元素边界、间距和样式
   - annotations 的绝对坐标是关系推断与结果校验依据，不得默认把每个元素实现为相对 `parent` 的独立 `marginStart/marginTop + 固定宽高`
   - `measurements.sibling_spacings` 和坐标差值只有在锚点关系确定后才能作为 margin；不得因为存在 `gap_to_next` 就跳过拓扑分析
2. **区块方案必须完成约束拓扑分析**：
   - 聚类相同或因整数归一产生 `<=1dp` 误差的 left/right/centerX/centerY/baseline，识别共享边缘、列、行和对齐组；baseline 默认只作为测量证据，不自动映射为 XML baseline 约束
   - 从截图业务语义与重复几何共同识别 `layout_role=fixed|flexible|equal_weight|wrap_content`
   - 每个横向区块必须声明 `width_delta_owner`，明确屏幕变窄或变宽时由哪个 View/区域吸收宽度变化
   - 无法解释的相对 `parent` 绝对定位必须标记 `constraint_strategy=absolute_exception` 并给出原因；没有原因时禁止批准方案
3. **表格和重复行使用公共列模型**：
   - 表头与至少两行内容共同推断 `column_group`；优先按重复列的 centerX、相邻中心中线和父容器 end padding 推导列边界
   - 数值、状态、图标等统计列可使用设计值确定固定列宽，并组成整体靠 `parent.end` 的固定统计区；禁止各列独立通过 parent margin 模拟坐标
   - 名称、标题等可伸缩内容使用 `0dp`，约束在前置图标/起始边界与固定统计区之间，并按项目惯例设置 `maxLines`、`ellipsize` 或换行策略
   - 表头与数据行必须引用同一列顺序和列宽规格；若重复实现，方案中仍须使用同一个 `column_group` 标识
4. **等价元素使用等分关系**：
   - 同一业务区域内语义等价且视觉等分的元素，使用 `0dp + 相同 horizontal_weight` 的 chain，或由共享 Guideline 明确等分
   - 每个单元内部的图标与数值组成 packed 关系并在单元内居中；说明文字约束到单元中心，禁止分别使用相对 parent 的绝对 margin 模拟三等分
5. **文字、图标和色块优先使用上下约束表达语义对齐**：
   - 同一文本行的文字默认使用同 top、同 bottom 或相对同一稳定目标的 top+bottom 双向约束来表达共享边缘或垂直居中；尽量不使用 `app:layout_constraintBaseline_toBaselineOf`
   - 只有设计明确要求共享排版基线，且 top/bottom 约束因字号、字体度量或动态内容差异无法稳定复原时，才允许使用 `app:layout_constraintBaseline_toBaselineOf`；区块方案必须标记 `baseline_strategy=required_exception` 并说明 `baseline_exception_reason`
   - 图标、色块与文字行通过上下约束居中；禁止用各自独立的纵向 margin 模拟同一对齐关系
   - 重复的多行说明使用垂直 chain、共享 top/bottom 边缘或稳定行容器；禁止为每个成员分别计算独立 `marginTop` 来模拟对齐
   - 单个文本框的 source bbox 高度不代表运行时 TextView 高度；文本对齐不得依赖固定高度
6. **锚点优先级**：
   - 优先顺序为：稳定 `parent/Guideline` -> chain/Barrier -> 稳定同级容器 -> 不会隐藏的同级 View -> 有理由的绝对定位例外
   - 跨业务区域定位时约束到稳定的区块容器，不让后续整段内容依赖区块内部的可选 View
7. **精确间距计算**：
   - 垂直间距 = `next_element.position.y - (current_element.position.y + current_element.size.height)`
   - 水平间距 = `next_element.position.x - (current_element.position.x + current_element.size.width)`
   - 禁止使用"大约"、"看起来像"等估算；但计算出的间距只能应用到已确定且 source/runtime 边界语义一致的相对关系，不能反过来替代关系推断
   - TextView 使用 `wrap_content` 和平台默认字体度量时，运行时外边界不等于设计工具的文本 bbox；文本 bbox 差值主要用于复原校验，不得直接转成逐文本 margin。文本间及文本与其他元素优先使用 top/bottom 约束到稳定行、单元容器或明确锚点；baseline 仅按已说明原因的例外策略使用
8. **容器优先级与居中表达**：
   - 页面根布局和普通内容容器优先使用 `ConstraintLayout`
   - `layout_direction = horizontal|vertical` 时，优先使用同一 `ConstraintLayout` 内的相对约束或 chain
   - `layout_direction = stack` 时，优先在同一 `ConstraintLayout` 中通过约束和 XML 顺序表达重叠关系
   - 只有目标工程已有明确结构、需要特殊裁剪/滚动，或 `ConstraintLayout` 无法清晰表达时，才使用 `LinearLayout`、`FrameLayout` 等其他容器
   - 父级已经是 `ConstraintLayout` 时，优先让当前区块元素成为同父直接子 View，并使用 margin、Guideline、chain 或 Barrier 表达区域边界；内部 `ViewGroup` 的保留条件按“层级树消费规则”审计
   - 固定尺寸或 `wrap_content` 的 View 同时约束到同一稳定目标的 top/bottom、默认 vertical bias 为 `0.5` 时，双向约束已经表达上下居中；仅为居中不得再设置相同的 `layout_marginTop/layout_marginBottom`
   - 相同上下 margin 只有在定义 `0dp` match-constraint 尺寸、保证明确的最小边距或配合非默认 bias 表达设计位置时才保留，并在区块方案中记录原因
9. **约束作用域**：
   - 约束目标只能是同一父 `ConstraintLayout` 的直接子 View 或 `parent`
   - 禁止跨父容器引用 View ID；需要跨区域定位时约束到稳定的同级容器
10. **可见性与整体约束审计**：
   - 建立相对约束前，必须检查 XML、DataBinding、Activity/Fragment、自定义 View 和 Adapter 中的显隐逻辑
   - 区分 `INVISIBLE`（保留尺寸）与 `GONE`（尺寸归零），明确隐藏后是保持位置还是收缩间距
   - 稳定内容优先约束到 `parent`、Guideline、Barrier 或不会隐藏的容器；禁止让后续整段内容只依赖一个可能 `GONE` 的 View
   - 需要跟随一组可选 View 收缩时可使用 Barrier，并根据目标行为显式选择 `barrierAllowsGoneWidgets`；不得沿用默认值而不审查
   - 必须审查 chain 中成员隐藏后的重排；仅在确实需要收缩时依赖 `GONE` 行为，并按需要设置 `layout_goneMargin*`
   - 至少验证全显示、每个可选 View 单独隐藏以及连续可选 View 隐藏时，整体约束不会错乱
   - 若无法从宿主代码、DataBinding 表达式或已有设计状态确定“保持位置/收缩间距”，标记 `needs_input` 并在生成最终约束前询问用户，禁止猜测或标记 `passed`
11. **宽度扰动审计**：
   - 方案批准前，除设计宽度下的坐标复原外，还必须静态推演一个更窄和一个更宽的可用宽度
   - 固定统计区、图标和控件应保持规格；`width_delta_owner`、等分 chain 和 `0dp` 文本区域负责吸收差值，不得重叠或在右侧留下无归属空白
   - 此审计只验证关系，不得把测试 viewport 通过 `tools:layout_width/height` 写入根 XML
12. **不得篡改设计间距**：
   - 在关系拓扑确定后使用 annotations 的精确间距；禁止用通用系统适配值替代设计稿间距

## 层级树消费规则（强制）

1. `layout_tree`、`layer_path` 和 `parent_name` 是语义与层级证据，不要求每个设计 group 都生成一个 `ViewGroup`。
2. 纯设计分组应在同一 `ConstraintLayout` 中扁平化，避免无意义嵌套。
3. 只有需要整体显隐、裁剪、背景、滚动、点击区域或独立复用时才保留容器，并优先使用 `ConstraintLayout`。
4. 每个非区块根 `ViewGroup` 必须声明可观察的运行时职责；若仅因设计稿存在 group、需要 padding、形成视觉分区、限制一组元素的坐标范围或方便定位而创建容器，必须删除并把子 View 扁平化到现有父 `ConstraintLayout`。
5. 只有当 margin、Guideline、chain、Barrier 仍无法在同一约束作用域清晰表达，且方案写明无法扁平化的具体原因时，才允许为约束作用域保留内部容器；“更容易写”不构成理由。
6. 当层级信息不可用或扁平时，从绝对坐标和包含关系推断同级约束，但仍需遵守同父级约束规则。

## shape 与阴影消费规则（强制）

1. **填充/渐变**：读取 annotations 的填充或渐变值，优先映射到 `changeBg().setColor(...)` 或 `setGradient(...)`。
2. **边框**：读取 `style.borders_parsed[]` 的颜色与 dp 宽度，优先映射到 `changeBg().setStroke(width.toPx(), color)`。
3. **圆角**：读取已归一为 dp 的 `border_radius` 或 `border_radius_detail_raw`，优先映射到 `setRadius(...toPx())` 或 `setRadii(...)`，禁止再次缩放。
4. **阴影**：`changeBg()` 不提供阴影能力；简单外阴影可按项目现有模式使用 `android:elevation`，复杂阴影必须复用或创建合适的 drawable/自定义实现并在规格表说明。
5. 禁止对 selector、ripple、layer-list、bitmap 或 vector 背景调用 `changeBg()`，因为该调用会将非 `GradientDrawable` 背景替换掉。

## 安全区与系统栏

System insets should NOT be used to replace design-specified spacing. Only use system insets for areas not covered by the design.

## 多轮工作流与状态门禁（严格）

默认状态顺序：

`partition_pending -> partition_approved -> block_plan_pending -> block_plan_approved -> block_implemented -> preview_choice_pending`

Preview 分支：

- 选择 `AI验收`：`preview_choice_pending -> ai_preview_review -> block_preview_accepted -> next_block`
- 选择 `跳过验收`：`preview_choice_pending|ai_preview_review -> block_preview_skipped -> next_block`
- 所有区块完成后：`next_block -> page_integration_pending -> page_integrated`

MCP 恢复临时分支（不改变业务审批状态）：

- `mcp_recovery_pending` 只保存 `stage1_data_pending` 的临时传输标记，不是 `partition_pending`、`block_plan_pending` 或其他审批状态；恢复调用完成后从阶段 1 的数据校验继续，绝不改写后续审批状态。
- 明确“继续/重试”只触发一次 `catalog_refresh`；目录补齐所有未完成调用所需工具则使用原生工具继续，仍有缺失则进入一次 `stdio_probe`，不为同一 `(url,image_id)` 追加新的目录刷新轮。
- `stdio_probe` 通过后立即补齐未完成的 `stdio_recovery` 只读调用；三项工具均取得结构化 envelope 后清除恢复标记并记录 `mcp_access_mode=stdio_recovery`，随后再按阶段 1 数据源规则校验截图、annotations 与 slices。只有证据校验通过才继续区块地图；接口错误或 payload 问题仍走既定暂停、schema fallback 或 `FAIL_FAST`，不得再次拉取已成功工具。探测、握手或恢复调用在预算内失败时进入 `MCP_RECOVERY_EXHAUSTED`，不得把临时标记当作审批确认。

- 等待区块划分、区块方案或 Preview 验收方式选择是正常暂停，不属于失败或“补充信息暂停态”
- 区块划分确认不等于任何区块方案确认；上一块 AI 验收通过或跳过验收也不等于下一块方案批准
- 用户继续描述问题或保持沉默时，不得推定当前区块方案已批准；用户调整方案后必须重新等待确认
- 若上一条消息只展示了一个当前区块方案并明确询问是否实现，用户紧接着回复“可以”“按这个写”“继续”等肯定指令，可视为批准该唯一方案；存在多个候选、插入了其他讨论或上下文不明确时，必须要求用户指出 `block_id`
- 区块确认步骤不得合并或跳过；用户最初提出“写 XML/直接生成”不构成任何区块批准
- 每个区块实现后必须询问一次 `AI验收` 或 `跳过验收`；在用户选择前不得启动 Android Studio Preview、截图或视觉对比，也不得默认替用户选择
- MCP 数据默认只获取一次并在同一任务中复用；native task tools 与 `stdio_recovery` 之间切换时也必须复用已成功且仍有效的结果，除非数据缺失、失效或用户更换画板，不得每个区块重复拉取

## 阶段 1：数据获取与业务区块地图

进入本阶段时设置 `recovery_stage=stage1`。

1. 解析 `LANHU_URL` 并提取 `image_id`；设计稿 URL 直接使用该 ID，禁止调用 `lanhu_get_designs` 筛选画板。
2. 在本阶段补齐一次数据调用链（优先使用当前任务原生工具；处于 `mcp_access_mode=stdio_recovery` 时，通过同一已配置的 server/launcher 的受控 STDIO Client 调用相同的三个工具；Client 可以是新的短会话）。若恢复轮已经产生并验证了某项结果，将其作为本步骤的结果复用，只调用缺失或失效项，不重复成功调用：
   - `lanhu_get_ai_analyze_design_result(url, image_ids=[image_id])`
   - `lanhu_get_design_annotations(url, image_id)`
   - `lanhu_get_design_slices(url, image_id)`
3. 校验截图是否实际可读取，并校验 annotations 的 `unit/layers` 及关键坐标；截图不可用时进入“补充信息暂停态”，annotations 不完整时按既定条件决定是否使用 `web_schema_fallback`。bridge 与原生工具使用同一数据校验，不因传输方式变化而放宽证据要求；slices 调用只读取 metadata、颜色/样式信息和下载 URL，不下载文件。
4. 使用截图识别页面业务语义与区块边界，使用 annotations 校验区块 bbox、尺寸、颜色和样式；不得直接开始内部 View 约束或 XML 实现。
5. 同时提出页面 shell 方案：承载方式、根容器、滚动方式、区块排列和系统栏责任。默认 `PRESENTATION`：
   - 含“底部弹窗/底部弹出/bottom sheet” -> `bottom_sheet`
   - 含“弹窗/对话框/提示/确认/dialog” -> `dialog`
   - 含“气泡/悬浮/tooltip/pop” -> `popup`
   - 含“半屏/透明/浮层Activity” -> `dialog_activity`
   - 明确“独立整屏页面/导航入口/深链页” -> `activity`
   - 其他 -> `fragment`
6. 只输出 `A) 数据审计`、`B) 页面 shell 与区块地图` 和一个确认提示；禁止输出 XML/Kotlin、搜索整页实现资源或人工修改目标 Android 工程。允许上述 MCP 工具维护其自身已被 Git 忽略的数据缓存。
7. 输出后状态必须为 `partition_pending`，等待用户确认或调整区块划分与实现顺序。

### A) 数据审计强制字段

缺任意字段时立即输出 `FAIL_FAST: SKILL_CONTRACT_VIOLATION` 并停止：

- `tool_route=design_chain`
- `mcp_calls`
- `recovery_stage=stage1`
- `task_tool_catalog=available|recovered_via_continue|unavailable`
- `stdio_probe=not_run|passed|failed`
- `mcp_transport_status=healthy|unhealthy|not_probed`
- `mcp_access_mode=native_task_tools|stdio_recovery`
- `mcp_recovery_steps=none|catalog_refresh|catalog_refresh_then_stdio_bridge`
- `mcp_retry_count=<非负整数，按 target_key 累计额外传输重试次数；不含 catalog_refresh、stdio_probe 或结构化业务错误>`
- `selection_key=image_id`
- `selected_image_id`
- `source_mode=mcp_annotations_primary|web_schema_fallback`
- `screenshot_source=mcp_ai_analysis|user_supplied`
- `screenshot_status=available`
- `unit=dp|px`
- `conversion_applied=true|false`
- `dimension_mode=literal_dp_sp`
- `constraint_mode=relationship_first`
- `text_height_mode=adaptive_default`
- `line_metrics_mode=platform_default`
- `root_preview_size=external`
- `layout_mode=constraintlayout_primary`
- `shape_mode=changeBg_primary`
- `preview_mode=tools_attributes`
- `preview_acceptance_mode=ask_each_block`
- `asset_resolution=project_reuse_first`
- `slice_download=deferred|approved|not_required`
- `confirmation_mode=per_block`
- `text_source_priority`
- `data_source=text/spacing=annotations|dds_schema`
- `font_source=annotations.text.font_family+font_weight|dds_schema|not_required`
- `font_mapping_mode=project_fontType_exact|not_required`
- `icon_geometry_source=annotations|dds_schema`
- `icon_asset_source=project_existing|lanhu_metadata|dds_schema|mixed|not_required`
- `fallback_reason`（仅兜底时必填）

原生工具直接可用且未发生当前 Client 即时重试时填写 `task_tool_catalog=available`、`stdio_probe=not_run`、`mcp_transport_status=healthy`、`mcp_access_mode=native_task_tools`、`mcp_recovery_steps=none`、`mcp_retry_count=0`；发生即时重试时只按额外传输重试次数填写。目录刷新只更新 `task_tool_catalog`：仅此前为 `unavailable` 且重查补齐时记录 `recovered_via_continue`，仅传输故障时保留 `available`；原生调用取得三项合法 envelope 后才记录 `mcp_transport_status=healthy`、`mcp_access_mode=native_task_tools`。只要本轮 STDIO 补齐至少一项且三项所需 envelope 到齐，就记录 `mcp_transport_status=healthy`、`mcp_access_mode=stdio_recovery`、`mcp_recovery_steps=catalog_refresh_then_stdio_bridge`；接口错误或 payload 问题仍按既定规则处理。已成功调用按结果键复用，不能再次拉取；恢复挂起或失败时只输出临时状态与原因，不伪造完整 A) 审计。

`slice_download` 是当前画板的聚合状态：任一真实切图待处理即为 `deferred`；仅当用户明确要求本轮补齐真实切图时为 `approved`；所有图片均已复用项目资源或无需切图时才为 `not_required`。

### B) 页面 shell 与区块地图字段

- `presentation | root_container | scroll_strategy | system_bar_owner`
- `block_id | business_role | source_bounds_dp | dependencies | implementation_order | status=partition_pending`
- `reuse_candidate | dynamic_states | cross_block_anchor`
- `open_question=none|<仅影响区块划分或 shell 的问题>`

## 阶段 2：单区块布局方案

前置：区块地图状态为 `partition_approved`。

1. 每次只选择一个 `block_id`；默认按已确认的 `implementation_order` 选择首个未完成区块，用户可指定顺序。
2. 只搜索当前区块需要的项目组件、宿主代码和资源；结合截图语义、annotations 与目标工程惯例形成关系拓扑。
3. 必须先输出一幅简洁关系图，再输出当前区块规格；关系图需要清楚表达固定区、弹性区、等分区、公共列和主要锚点。
4. 方案必须完成设计宽度、一个更窄宽度和一个更宽宽度的关系审计；这些测试 viewport 不得写入 XML 根节点。
5. 只输出当前 `BLOCK_PLAN` 和一个确认提示；禁止输出 XML/Kotlin 或修改文件。
6. 输出后状态为 `block_plan_pending`。用户提出调整时，更新同一方案并继续保持该状态；不得因为已经修改过一次方案而自动实现。

### BLOCK_PLAN 强制字段

以下字段必须形成可逐组件审计的规格表；每个组件对适用字段给出明确值，不适用时写 `not_applicable`，禁止直接省略。仅当公共值对整组组件完全一致且引用关系无歧义时，才允许在组级声明一次。

- `block_id | business_role | source_bounds_dp | dependencies | status=block_plan_pending | approval_notes`
- `relationship_diagram`
- `component | view_type | parent | source_layer_or_measurement`
- `layout_role=fixed|flexible|equal_weight|wrap_content`
- `width_strategy=wrap_content|match_constraint|fixed_dp | height_strategy=wrap_content|match_constraint|fixed_control|fixed_non_text`
- `container_role=block_root|internal|not_applicable | container_reason=<runtime responsibility|constraint_scope_required|not_applicable> | flattening_audit=passed|not_applicable`
- `width_delta_owner=<component|region|parent_free_space|not_applicable> | column_group | chain_group | alignment_group`
- `alignment_strategy=top_bottom|center_vertical|shared_edge|container_center|baseline_exception|not_applicable | alignment_reason`
- `baseline_strategy=avoided|required_exception|not_applicable | baseline_exception_reason=<top/bottom 无法稳定表达的具体原因|not_applicable>`
- `centering_strategy=dual_anchor_default_bias|chain|explicit_bias|explicit_margins|not_applicable | symmetric_margin_reason=match_constraint_size|minimum_edge_spacing|intentional_bias|not_applicable`
- `constraint_strategy=relative|chain|guideline|barrier|baseline_exception|absolute_exception | absolute_exception_reason`
- `constraint_anchor_start/end/top/bottom | chain_style | weight | stable_anchor`
- `text_size_sp=<value|not_applicable> | text_height_strategy=wrap_content|fixed_control|not_applicable | text_overflow`
- `line_height_strategy=default|explicit|not_applicable | include_font_padding=default|explicit|not_applicable | explicit_text_metric_reason`
- `design_font_family=<value|default|not_applicable> | design_font_weight=<value|default|not_applicable> | font_mapping=project_default|fontType_0|fontType_1|fontType_2|fontType_3|FONT_UNRESOLVED|not_applicable`
- `font_binding=<app:fontType expression|not_applicable> | font_layout_mode=data_binding|project_default|needs_input|not_applicable | font_match_evidence=annotations+project_mapping|not_applicable`
- `visibility_states | gone_behavior=keep|collapse|not_applicable | visibility_constraint_audit=passed|needs_input|not_applicable`
- `shape_strategy=changeBg|existing_resource|android_drawable|not_applicable | changeBg_call | host_binding`
- `runtime_content_source | preview_attrs`
- `asset_source=project_existing|lanhu_metadata|dds_schema|not_applicable | asset_delivery=reused|pending_download|approved_download|not_required | asset`
- `reference_width_audit | narrow_width_audit | wide_width_audit`

以下任一情况存在时，方案不得标记为可批准：

- 内容型 TextView 使用固定高度，却没有 `fixed_control` 角色和理由
- 根节点计划写入 `tools:layout_width/height`
- 非区块根 `ViewGroup` 既无运行时职责，也没有写明无法通过 margin、Guideline、chain、Barrier 扁平表达的 `constraint_scope_required` 具体原因；或保留理由仅为设计分组、padding、视觉分区、坐标范围或方便定位
- 固定尺寸或 `wrap_content` View 已通过同目标 top/bottom 双向约束居中，却仍使用相同上下 margin 且没有有效的 `symmetric_margin_reason`
- 表头和重复行未共享 `column_group`，或重复列分别使用相对 parent 的绝对 margin
- 名称/标题位于固定前后区域之间却未使用弹性宽度，也未声明 `width_delta_owner`
- 语义等价的等分项未使用等权 chain/共享 Guideline，也没有例外原因
- 文本使用 `app:layout_constraintBaseline_toBaselineOf`，却未标记 `baseline_strategy=required_exception`，或未说明 top/bottom 约束无法稳定表达的具体原因；或图标/色块与文字用独立纵向 margin 模拟对齐
- 设计明确要求特殊字体却为 `FONT_UNRESOLVED`，或既有非 Data Binding 布局需要迁移但 `font_layout_mode=needs_input`
- `app:fontType` 用于非 TextView、非 Data Binding 布局、非 `@{0}` 到 `@{3}` 表达式，或仅因通用粗体而错误选择 `fontType_1`
- 使用 `absolute_exception` 但未给出原因
- GONE 行为、宿主样式位置或关键资源策略无法确定

## 阶段 3：实现已批准区块

前置：用户按“多轮工作流与状态门禁”的无歧义确认规则批准当前方案，状态为 `block_plan_approved`。

1. XML Agent 只能消费已批准方案；不得重新决定容器、公共列、固定/弹性角色、文本策略或显隐行为。
2. 若实施时发现方案无法成立、缺少宿主或产生新依赖，立即退回 `block_plan_pending` 并提出最小问题；禁止边实现边自行改变拓扑。
3. 按用户原始授权写入目标工程，或在只读请求中只输出当前区块代码。首次实现可建立已确认的最小页面 shell，但不得预填、猜测或顺带实现未批准区块。
4. 已实现区块仅允许本轮当前区块范围内的修改；禁止为了方便重构其他已批准或未批准区块。
5. Kotlin 只允许在已定位宿主中，通过现有 ViewBinding/DataBinding 等方式调用 `View.changeBg()` 设置样式；禁止动态创建 View、`findViewById` 或 `addView`。
6. 为当前区块的动态文本、动态图片、延后切图和运行时 `changeBg()` 补充必要 `tools:*`，但遵守根 Preview 尺寸禁令。
7. 只做当前区块的 XML 解析、资源/ID/同父约束、文本策略、baseline 例外、字体映射与 Data Binding 语法、内部容器必要性、居中边距冗余、宽度扰动和 GONE 状态定向检查；除非用户明确要求，不运行全量构建。所有定向静态检查必须通过后才能进入 `preview_choice_pending`；检查失败时先在已批准方案内修正并重跑，若失败说明方案拓扑不成立则退回 `block_plan_pending`，禁止提供“跳过验收”选项。
8. 定向静态检查全部通过后，输出 `BLOCK_IMPLEMENTED`、变更文件和检查结果，然后询问：`是否需要 AI 根据 Android Studio Preview 与蓝湖截图验收当前 <block_id>？请回复 AI验收 或 跳过验收。` 状态记为 `preview_choice_pending` 并停止。
9. 在用户选择前禁止打开 Preview、捕获截图或执行视觉对比；用户最初的“写 XML”授权不代表选择了 AI 验收。

## 阶段 3A：可选 AI Preview 验收

前置：当前区块已实现且用户明确选择 `AI验收`，状态为 `ai_preview_review`。

1. 仅渲染和检查当前区块；使用 Android Studio Preview 的设备配置提供 375dp 参考宽度，禁止向 XML 根节点写入 `tools:layout_width/height`。
2. 获取当前区块 Preview 截图，与蓝湖原图对应区块并排或叠加比较，同时以 annotations 的 bbox、间距、字号、颜色和样式数据做数值核对；不得只凭像素差判断通过与否。
3. 至少检查固定/弹性/等分关系、公共列、top/bottom 垂直对齐、baseline 例外必要性、TextView 裁切与换行、字体映射、间距、颜色、圆角、边框、Preview 占位和可见状态；`app:fontType` 若未在 Preview 执行，以 annotations 与项目映射的静态审计为准，不得据此误判为默认字体；待下载切图只验收占位区域的尺寸和约束，不验收图片内容。
4. 若差异只涉及已批准拓扑内的尺寸、颜色、文案、占位或资源引用，只修改当前区块并重新渲染；若需要改变容器、锚点、列模型、宽度角色或 GONE 行为，退回 `block_plan_pending` 并等待方案重新确认。
5. AI 自动修正并重新渲染最多两轮；仍有差异时输出剩余问题，状态保持 `ai_preview_review`，询问用户补充调整意见、稍后重试或 `跳过验收`，禁止无限重试或扩大到其他区块。
6. Preview 无法启动或截图不可读取时，不得声称验收通过，也不得为此运行全量构建；状态保持 `ai_preview_review`，说明阻塞，并重新询问用户选择稍后重试或 `跳过验收`。
7. 通过时输出 `AI_PREVIEW_REVIEW: PASSED`、对比结论和已知未验收项，状态改为 `block_preview_accepted`；下一轮才进入下一块的阶段 2。

## 阶段 3B：跳过 Preview 验收

前置：当前区块已实现、阶段 3 定向静态检查全部通过，且用户在 `preview_choice_pending` 或 `ai_preview_review` 状态明确选择 `跳过验收`。

1. 不启动 Android Studio Preview，不捕获截图，不执行截图对比，也不追加为视觉验收而运行的命令。
2. 输出 `PREVIEW_REVIEW: SKIPPED_BY_USER`，明确当前区块只完成了阶段 3 的定向静态检查、没有经过视觉验收；状态改为 `block_preview_skipped`。
3. 下一轮进入下一块的阶段 2。用户之后若反馈当前区块问题，只调整该区块：涉及关系拓扑时退回阶段 2，仅实现细节时按已批准方案修正并重新询问验收方式。

## 阶段 4：整页组合审计

前置：所有区块均为 `block_preview_accepted` 或 `block_preview_skipped`。

1. 进入本阶段时状态为 `page_integration_pending`；只处理页面 shell、滚动容器、区块间 top/bottom 约束、跨区块显隐和最终资源清单。
2. 禁止无确认重构已完成区块内部；确需改变时，将受影响区块退回阶段 2 并说明原因。跳过 Preview 的区块必须在最终审计中继续标记为 `visual_review=skipped`，不得写成已验收。
3. 验证设计宽度及窄/宽场景下的区块连接、滚动范围、最后一个稳定 bottom anchor、资源引用和可见性。检查失败时保持 `page_integration_pending` 并报告问题；需要修改区块内部拓扑时，将对应区块退回阶段 2。
4. 只有上述整页检查全部通过后才能输出 `PAGE_INTEGRATED`、最终审计和资源清单，并把状态改为 `page_integrated`；若用户要求写文件，同时确认工作树只包含本任务范围及原有无关改动。

## 补充信息暂停态

- 实际截图不可读取，或目标 Android 模块、页面 shell、宿主样式位置、关键动态状态、可隐藏 View 行为无法唯一确定时，只提出继续当前阶段所需的最小问题并停止。
- 该状态不等于用户确认，也不得绕过区块地图或区块方案门禁；用户补充后从原状态继续。

## 实施前模块与宿主预检

1. 先定位目标 Android 模块：优先使用 `HOST_ACTIVITY`/`HOST_FRAGMENT`，否则按用户指定的 XML、宿主 Kotlin 或资源所属模块定位；多个候选无法唯一判断时停止询问。
2. 需要 `changeBg()` 时，定位宿主已有 ViewBinding、DataBinding 或其他绑定位置：Fragment/Activity/自定义 View 的静态样式放初始化阶段，列表 Item 或动态状态放 Adapter 绑定/状态刷新位置。
3. 目标 View 必须有稳定 ID，宿主可通过既有绑定方式访问；禁止新引入 `findViewById`。
4. 需要 `app:fontType` 时，确认目标 View 是 `TextView` 子类、XML 使用 Data Binding `<layout>` 根包装，且取值严格匹配 `BindingUtils.font` 的 `0..3` 映射；既有普通布局若需迁移必须先经区块方案确认。
5. 禁止为本任务创建或修改 `dimens.xml`。

## 区块与最终资源清单字段

- `asset_source=project_existing|lanhu_metadata|dds_schema|generated_drawable|not_required`
- `asset_delivery=reused|pending_download|approved_download|not_required`
- `asset_format | asset_output_dir=existing|deferred|<target-dir> | runtime_reference=none|<resource> | preview_reference=none|<tools-attribute> | asset`
- `slice_download=deferred|approved|not_required`
- `shape_strategy=changeBg` 的项目不生成重复 drawable，只列出对应宿主文件和 View ID

## 兜底路径补充字段（仅 fallback）

- `scale_formula=375/750`
- `conversion_rule=round(px * 375 / 750)`
- `schema_version_id`
- `schema_source_url`
