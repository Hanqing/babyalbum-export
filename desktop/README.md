# 相册导出助手 · 安卓桌面版

把海马爸比「时光相册」里的照片批量保存到电脑：在官方 App 里自动勾选“未下载”的照片并点“下载”，再把手机上的文件逐张校验后存进电脑。只用 Python 3.9+ 标准库，另需 adb。

下载：[album-export-desktop.zip](https://github.com/Hanqing/babyalbum-export/releases/latest/download/album-export-desktop.zip)，或克隆整个仓库。Mac 双击「相册导出助手.command」（第一次需要右键 → 打开），Windows 双击「相册导出助手.bat」，就会在浏览器里打开图形界面。

## 用法

```bash
python3 -m album_export gui                       # 浏览器里的图形界面（推荐给家长）
python3 -m album_export doctor                    # 检查连接、手机状态、App 版本
python3 -m album_export export --dest ~/Pictures/海马爸比相册归档
python3 -m album_export sync   --dest ...         # 只搬运手机上已下载的照片，不操作 App
python3 -m album_export archive --dest ...        # 生成按月份、写入拍摄时间的副本
python3 -m album_export report  --dest ...
```

`export` 的常用参数：`--batch 150`（每批勾选张数）、`--max-batches N`、`--dry-run`（只勾选一批不下载）、`--verbose`（每轮勾选细节）、`--tz Asia/Shanghai`（默认取手机时区）。

找 adb 的顺序：`--adb` 参数、环境变量 `ADB`、程序目录下的 `platform-tools/`、PATH、`ANDROID_HOME` 和 Android SDK 默认目录。Mac 可以 `brew install android-platform-tools`，也可以把 Google 的 platform-tools 解压到程序目录。

## 手机准备

1. 设置 → 关于手机 → 连续点“版本号”7 次，打开开发者选项。
2. 开发者选项里打开“USB 调试”，连接电脑后在手机上点“允许”（建议勾选“始终允许”）。
3. 手机解锁，海马爸比 App 已登录。导出期间工具会让手机保持亮屏，结束后恢复原设置。

## 归档目录

```
海马爸比相册归档/
  原始文件/            官方 App 下载的原文件，原名、原字节，不做任何修改
  按月份/2025/2025-04/  （archive 生成）写入了拍摄时间的副本，导入相册后按真实时间排序
  导出报告.md          搬运完整性、来源覆盖、质量与限制
  .album-export/       账本（SQLite）、临时目录、隔离区
```

## 工作方式

1. 体检：设备授权、亮屏未锁、App 版本、手机时区。
2. 先把手机上已下载、电脑上还没有的照片搬过来。
3. 循环：进入「精彩瞬间」→ 打开“仅显示未下载的文件”→ 编辑 → 回到列表顶部 → 按日期组勾选到目标张数 → 下载 → 等文件落盘 → 拉取并校验 → 等列表刷新。
4. 列表显示“暂无内容”即结束，写报告。

每张照片都核对手机端 SHA-256、大小和 JPEG 结构，全部通过才原子地放进「原始文件」；同一内容只存一份。

## 安全规则

- 只点导航白名单里的控件和日期勾选框；文字含“删除”等词的控件一律不点。
- 勾选框必须完整位于标题栏和底部操作栏之间才点；“下载”只点远离“删除”的一侧，并先核对两个按钮的文字和 ID。
- 不认识的页面、锁屏、断线、连续无进展都会暂停；再次运行从断点继续。
- 不删除、不移动手机上的任何照片。

## 已验证

- 海马爸比 App 3.4.2，vivo V2133A，Android 14，macOS。全量 2,515 张：20 分 44 秒（原型 68.5 分钟），失败 0。
- 已知行为：App 以“导出目录里有没有同名文件”判断是否已下载，结果在 App 进程内缓存；同一照片再次下载会生成“ (1)”副本（内容相同）。
- Windows：adb 路径自动查找（`%LOCALAPPDATA%\Android\Sdk\platform-tools`），部分手机需要厂商 USB 驱动；尚未实机验证。

## 开发

```bash
python3 -m unittest discover -s tests                 # 单元测试（含真机界面样本）
python3 tools/test_queue.py hold --newest 200 --manifest /tmp/h.json   # 造一个真实的待下载队列
python3 tools/test_queue.py restore --manifest /tmp/h.json            # 逐张核对后复原
```

`test_queue` 会把最新的 N 张挪到手机的 Download 临时目录（先记录哈希），之后需要强制停止海马 App 让它重新判断；`restore` 只在重新下载的文件与原文件哈希一致时才删除临时副本。
