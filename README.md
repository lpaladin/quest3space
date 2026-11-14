# Quest3Space

Quest 3 物件扫描原型：头显采集，当前 PC 重建，所有新增开发工具、运行环境和扫描数据安装在 **E:\Quest3Space**。源码位于 **D:\Projects\AIProjects\quest3space**。

## 启动 PC

双击 `start-pc.cmd`。控制台默认地址：<http://127.0.0.1:8765>。

首次部署已在 E 盘建立 Python 运行环境；启动脚本不重新安装依赖。控制台显示配对码、接收帧数、有效帧数、扫描方位、实时模型和导出文件。
扫描记录显示创建日期与时间，使用浏览器本地时区。

## 启动 Quest

1. 开启开发者模式，用 USB 连接 Quest 3，允许 USB 调试。
2. 安装 `E:\Quest3Space\builds\quest3space-0.2.0.apk`。
3. 保持 PC 服务运行，双击 `connect-quest.cmd`（或执行 `tools\connect-quest.ps1`）配置 USB 反向端口和头显连接文件，并启动应用。此操作不会清除应用数据。
4. 在头显内打开 **Quest3Space Scanner**，允许相机和空间数据权限。
5. 等待面板显示 **RGB: ready / Depth: ready / PC: connected**。

USB 模式通过 `adb reverse tcp:8765 tcp:8765` 连接本机，不需要开放 Windows 入站端口。断开 USB 后反向端口不可用；重新连接后再次运行连接脚本。

Wi-Fi 模式可将头显内 PC 地址改为 `http://PC局域网IP:8765`，使用控制台配对码。PC、Quest 应在可互通的局域网中；Windows 防火墙应允许该应用端口。无线网络的吞吐和防火墙状态需要单独验证。

## 扫描

- 用右手柄指向物件，按**右握把**放置扫描框。默认边长 30 cm，可用面板按钮切换尺寸。
- 扫描框包住物件，底面略高于桌面，避免把桌面或旁边物体一起融合。
- **A**：开始／继续；**B**：暂停；**X**：结束并导出；**Y**：将控制面板重新放到视线前。
- 手柄射线加扳机可点击按钮和输入框。
- 按钮悬停时变亮；点击时短暂变色，并给右手柄一次轻震动。
- **默认原位显示模型；按住左握把移至左手旁预览（最长边最多 22 cm），随手移动和旋转，松开恢复原位 1:1**。使用 MRUK 203 的 sceneless world locking 维护本次运行的世界坐标。系统重置视野后由 SDK 补偿 TrackingSpace，采集等待至少 0.75 秒且世界锁定有效后继续。物件移动、跨应用重启的旧坐标恢复仍不保证。
- **按住右握把 RGrip**：扫描盒持续跟随右手位置与旋转，中心在手柄前方约 25 cm；松开后锁定，按 A 开始扫描。扫描开始后盒子固定，避免改变当前融合坐标。旋转同时用于 PC 重建与离线裁剪。
- **平面围合模式**：开始扫描前点击 `Plane region: Start` / `Switch to PLANE mode`，把右手前方的洋红色参考点放在要围住的物体内部。移动右手预览新平面，用 A、`Place` 按钮或右握把松开动作放置；指向已有平面，用 B 或 `Delete pointed plane` 删除。已放平面不能移动、旋转或翻面。开放时平面双侧；围住参考点形成有限区域后自动确定内侧并尝试开始扫描，不增加确认步骤。摄像头/PC 未就绪时保留闭合选区，恢复后按原有开始扫描操作重试。
- **模式切换**：开始扫描前，盒子模式点击 `Switch to PLANE mode`；平面模式点击 `Switch to BOX mode`。切换会重新定义选区（不删除扫描记录），扫描开始后选区锁定，需 New session 才能切换。
- 平面模式最多 16 个平面，围合区域各轴范围 3 cm–4 m，当前支持凸区域。平面按无限延伸计算，界面只画局部框线或闭合面的轮廓。正式采集后范围锁定；开始新 session 后才能重新定义范围。PC 会按真实平面裁剪，不以外包盒代替选区。大范围使用同样的体素网格，几何分辨率会下降。
- **实时环境深度遮挡**：扫描框、平面线框、顶点与模型使用 Meta SDK 的双眼深度遮挡，默认 Soft；手部保留在深度中。无需 PC 重建完成。控制面板、文本、进度条和操作射线也参与环境遮挡，使用相同 Soft / Hard / Off 开关。`Depth occlusion: cycle Soft / Hard / Off` 可对比切换；模式写入性能日志。深度尚未就绪时自动显示原有内容。默认相对深度容差为 0.015，用于缓解实物表面附近闪烁，遮挡并非精确测量。
- **按住左扳机 LT**：显示绿色框内已重建顶点，以及蓝紫色持久环境点。紫色点以 5 Hz 从 64×64 深度图累积，范围 0.2–5 m；3.5 cm 空间哈希、至少三帧确认、相邻点至少 2.5 cm、固定半径 2.5 mm，确认后位置锁定。上限 12000 点，达到上限停止添加；密度逐步填充，不会随转头整片重采样。松开 LT 立即清空紫色参考点，下次按住重新构建；仅在内存中临时使用，不保存到磁盘。New session 也会清空。它适合静态环境，移动物体可能留残影；使用 MRUK 世界锁定，重置视野或锁定状态变化时清空参考点；点间距不是深度精度保证。
- **扫描框与进度**：采集中琥珀色、采集瞬间闪白，建议目标达成后绿色；暂停/待开始青色，保存中紫色，采集时传感器/连接异常红色。框旁显示有效帧数、方向覆盖与建议进度。推荐 60 帧、10 个方向，并补拍高低视角；目标达成不等于质量验收通过。
- 物件保持静止，人缓慢绕行，补拍不同高度。至少四张有效帧、三个不同方位才允许导出。
- 必须先放置扫描框才能新建扫描；RGB 会先进行像素验证，空白／整张灰色图不会作为有效照片融合。
- 点击结束后，程序先上传本地缓存，再等待 PC 完成导出。

头显默认在原扫描位置以 1:1 比例显示模型，按住左握把移至左手旁缩放预览，松开复位；该叠加用于检查融合误差，精度仍需通过实物对照验收。左扳机显示的是重建顶点采样，不是原始深度相机的实时点云。

## 输出及数据

扫描结束后，PC 页面中的 **PC 离线精修** 按钮可启动照片驱动的网格精修与全分辨率纹理制作。每次运行写入 `E:\Quest3Space\offline\扫描ID\运行ID`，完成后下载 `quality.glb`；`quality-comparison.jpg` 是相同视角下的原模型／新模型实际渲染对比。处理使用 CPU，最多占用 16 个 OpenMP 线程，独立于 Quest 端显示；原始照片和实时模型不覆盖。

**扫描中迭代：** PC 页面新增“扫描中自动迭代”开关，工具可用时默认开启。至少 24 个有效帧、3 个方位后生成第一版；随后新增至少 16 个有效帧且间隔至少 30 秒才启动下一轮。全局最多一个后台任务，期间的新帧合并成最新待处理批次；扫描结束后追加包含剩余有效帧的最终批次。每轮使用固定的帧集合及对应网格快照，通过硬链接（不可用时复制）读取已提交原始数据，绝不遍历正在增长的扫描目录。后台任务最多使用 8 个 OpenMP 线程，空间不足 2 GiB 时暂停启动新任务。

PC 可勾选“显示最新精修网格预览”，在新版本完成前持续显示上一成功版本。彩色网格预览最多 12,000 个三角面，完整照片纹理保存在 GLB。Quest 继续显示低延迟粗预览，本次不需要更新 APK。这是反复处理固定快照的批次迭代；并未实现逐帧增量优化或全局 BA。关闭自动迭代会清除待处理批次，已在运行的任务会正常完成。

默认使用已融合表面作为几何初值，剔除微小孤岛，轻量平滑，再运行 OpenMVS 的照片一致性网格精修、清晰照片筛选和原分辨率纹理映射。没有使用生成图像，也没有把未观测孔洞填成实测表面。未覆盖区域可能显示为空洞或橙色。照片精修本身是估计，最终测量精度尚未用实物标定。

工具版本为 Open3D **0.19.0**、OpenMVS **2.4.0** CPU Windows 官方包，均安装在 E 盘。Open3D 0.20.0 在本机最小平面融合测试中未生成表面，因此固定使用通过该测试的 0.19.0。OpenMVS 全局接缝优化在本机原生崩溃，局部优化产生暗斑，因此当前禁用两者；纹理可能仍有曝光接缝。

```powershell
# 在新环境补装离线依赖，当前机器已安装
.\tools\setup-offline.ps1

# 保守模式：沿用原始几何，使用照片精修与清晰纹理
E:\Quest3Space\runtime\Scripts\python.exe -m pc.offline 扫描ID --refine

# 实验模式：重新融合原始深度，可选局部 ICP；细体素可能放大深度噪声
E:\Quest3Space\runtime\Scripts\python.exe -m pc.offline 扫描ID --geometry tsdf --voxel-mm 4 --refine
```

| 内容 | E 盘目录 |
|---|---|
| 原始 JPEG、相机参数、原始浮点深度图 | `E:\Quest3Space\captures\扫描ID` |
| 融合检查点、最近测量覆盖图 | `E:\Quest3Space\reconstruction` |
| GLB、OBJ、MTL、PNG 纹理、PLY、报告 | `E:\Quest3Space\exports\扫描ID` |
| PC、Unity 构建日志 | `E:\Quest3Space\logs` |
| 签名 APK | `E:\Quest3Space\builds` |

模型单位为**米**，GLB 为 Y 轴朝上。OBJ 需要和同目录的 MTL、PNG 纹理一起使用。

头显原始帧保存到应用私有缓存，只有 PC 确认已写入后才删除。网络失败会重试；本地积压到 120 帧后暂停采集，避免无限占用空间。PC 数据盘低于 512 MiB 可用空间时拒绝新增上传。

PC 服务重启后会读取最近一次未完成扫描的原始帧并重新融合，恢复完成后继续接收。已完成扫描的输出和记录仍保留在 E 盘。

## 重建方法与边界

正式 Quest 应用使用 **Meta Environment Depth + MRUK Passthrough Camera Access**。深度图有自己的空间重投影矩阵，彩色图有其拍摄位姿和内参；PC 使用对应的矩阵融合深度，在生成的网格上投影照片生成纹理。
调试版使用 Meta 官方全程 MR Boundaryless 声明 `com.oculus.feature.BOUNDARYLESS_APP`，彩透是必需功能，应用不含沉浸式 VR 模式。未同时启用互斥的 Contextual Boundary API；不修改头显全局设置。此声明用于头显独立运行的 APK，不适用于 PC/Link 模式。实际行为仍需在对应 Horizon OS 真机验证。

本版实现 CPU TSDF 融合，不依赖 NVIDIA CUDA，不使用生成式模型补造形状。未观测表面可能保持开口；透明、镜面、细杆、小凹槽和深度边缘会受到 Quest 深度质量限制。扫描方位计数只代表相机方向，不等于表面完整度或测量精度。最终物件质量需要实物扫描验收。
Quest 端 JPEG 编码在后台线程执行；状态文字每秒更新四次，预览仅在有效帧数变化时更新并在后台解析 JSON。运行日志中的 `QUEST3SPACE_PERF` 每十秒记录应用 Update 平均帧率、超过 25 ms 的帧数和最慢帧；这些指标不等同于显示合成器帧率。

PC 还保留基于 A4 标记板的轮廓交集备用流程；正式 Quest 应用默认使用深度流程，不需要打印定位板。

## 开发与构建

- Unity **6000.0.66f2**：`E:\Quest3Space\tools\Unity6`
- Meta XR Core SDK / MRUK **203.0.0**：`E:\Quest3Space\tools\packages`
- Unity OpenXR **1.15.1** / Unity OpenXR Meta **2.1.0**
- Android NDK **r27c**、JDK **17**、CMake **3.22.1**、Android SDK：E 盘 `tools` 下
- Python 3.12 运行环境：`E:\Quest3Space\runtime`
- UPM、Gradle、Bee 构建缓存：E 盘 `tools` 下
- Unity Library 通过目录联接存放于 `E:\Quest3Space\unity-cache\Library`

`unity-source` 是应用及构建脚本的源文件；`tools\build-quest.ps1` 会同步它们到正式 Unity 项目 `quest-unity`，通过 Unity Editor API 配置和构建。请修改 `unity-source`，避免下一次同步覆盖仅在 Unity Assets 中进行的代码编辑。

```powershell
# 正常重建 APK
.\tools\build-quest.ps1

# 更改 Meta 功能配置后先配置，再运行官方项目修复和构建
.\tools\build-quest.ps1 -Step Configure
.\tools\build-quest.ps1 -Step FixSetup
.\tools\build-quest.ps1 -Step Build

# PC 验证：使用合成几何和独立 HTTP 测试服务，不混入真实扫描
E:\Quest3Space\runtime\Scripts\python.exe -m unittest discover -s tests -v
```

Unity／Meta 工具可能在 Windows 用户目录保留许可证、日志和少量配置；编辑器、SDK、主要缓存与扫描数据使用上面的 E 盘路径。

## 官方参考

- [Meta Passthrough Camera Access](https://developers.meta.com/vr/documentation/unity/unity-pca-documentation/)
- [Meta Environment Depth](https://developers.meta.com/vr/documentation/unity/unity-depthapi-occlusions-get-started/)
- [Meta VR Agentic Tools / metavr](https://github.com/meta-quest/agentic-tools)
- [Unity 构建缓存位置](https://docs.unity.com/en-us/engine/6000.5/manual/building-and-publishing/build-cache-location-reference)



Quest 主面板按状态显示操作；连接地址、配对码、框大小、遮蔽模式与诊断信息收进 Settings。主面板和扫描区域旁同时显示进度条、有效帧数 / 60、方位数 / 10；百分比是采集建议目标，不是几何完整度或保存进度。
系统重置视野后，主控面板在短暂稳定期后自动移到新的正前方；此操作不移动扫描框、平面选区或模型，且不等待世界锁定恢复。Y 键仍可随时手动召回面板。

显示 SDK 官方 Quest 3 Touch Plus 左右手柄模型，跟随控制器位姿并保留按钮与扳机动画。所有应用虚拟元素（模型、手柄、点、线框、射线、UI）共享轻微的距离亮度提示：0.35 m 内保持亮度，平滑过渡至 4 m 的 84%；更远不再变暗，彩透画面不修改。手柄模型使用原始贴图和统一的无光照深度材质。
手柄模型保留距离亮度提示，但不参与现实环境深度遮挡裁剪；射线、UI、扫描框和扫描模型仍按 Soft / Hard / Off 遮挡模式显示。
