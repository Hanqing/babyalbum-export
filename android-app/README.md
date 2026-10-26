# 宝宝相册导出 · 安卓独立 App

在手机上直接把海马爸比「时光相册」批量保存到手机相册，不需要电脑。App 通过无障碍服务在官方 App 的「精彩瞬间」页面按日期勾选“未下载”的照片并点击“下载”，照片由官方 App 存进 `DCIM/Camera/海马爸比`。

## 使用

从 [Releases](https://github.com/Hanqing/babyalbum-export/releases/latest) 下载 `babyalbum-export.apk`，需要 Android 8.0 及以上。手机提示“来源不明”或“有风险”时，确认继续安装。

1. 安装后打开「宝宝相册导出」，点“去开启”，在无障碍设置里打开「宝宝相册导出」。
2. （可选）允许读取相册，用于核对每批是否真的下载成功。
3. 点“开始导出”。App 会打开海马爸比、进入相册、按批下载；顶部黑条显示进度，可随时“停止”。部分手机（如 vivo）第一次会问“是否允许打开海马爸比”，请点“始终打开”。
4. 完成后自动回到本 App 显示结果；中断后再次开始会接着下载。

## 安全与隐私

- 无障碍服务只接收海马爸比 App 的事件，只在点“开始导出”后操作。
- 只点击导航白名单里的控件和日期勾选框；含“删除”等文字的控件一律不点；“下载”直接调用按钮自身的点击动作。
- 不读取照片内容，不联网，不上传。相册权限只用于统计文件夹里的照片数量。
- 导出期间保持亮屏；屏幕关闭会自动暂停。

## 构建

不依赖 Gradle：`./build.sh` 依次调用 aapt2、javac、d8、zipalign、apksigner，输出 `build/babyalbum-export.apk`（默认调试签名，可用 `KEYSTORE`/`KS_PASS`/`KEY_ALIAS` 换成正式签名）。需要 Android SDK build-tools 与任意 JDK 17+（默认用 Android Studio 自带的 JBR）。

## 测试（adb）

```bash
adb install -r build/babyalbum-export.apk
adb shell pm grant app.babyalbum.export android.permission.READ_MEDIA_IMAGES
# 开启无障碍服务（保留手机上已开启的其他服务）
adb shell settings get secure enabled_accessibility_services
adb shell settings put secure enabled_accessibility_services "<原值>:app.babyalbum.export/.ExportService"
adb shell am start -n app.babyalbum.export/.MainActivity --ez autostart true --ei batch 150
adb logcat -s AlbumExport
```

## 已知限制

- 已验证：海马爸比 3.4.2，vivo V2133A，Android 14；全量 2,515 张约 11 分钟，重新下载的文件与原文件逐字节一致。其他版本遇到不认识的页面会暂停。
- 测试时不要同时运行 uiautomator（会暂停所有无障碍服务）；vivo 每次 USB 安装都需在手机上确认。
- 从 adb 以外的渠道侧载时，Android 13+ 可能要求先在应用详情里“允许受限设置”才能开启无障碍。
- 照片只存到手机相册；要存到电脑请用数据线复制 `DCIM/Camera/海马爸比`，或使用桌面版（带逐张校验）。
