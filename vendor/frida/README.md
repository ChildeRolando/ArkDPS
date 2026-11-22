# Frida Gadget 17.26.1

Windows x64 Gadget 来自 Frida 官方发布，版本、下载地址及两级 SHA-256 由
`tools/frida_gadget_asset.py` 固定。源码运行前执行：

```powershell
python tools/frida_gadget_asset.py
```

构建完整便携包时自动加入经校验的 DLL 和来源清单。`--without-events` 不包含该组件。
构建缓存与版本目录中的二进制不提交。程序将已验证组件复制到运行时目录，配置和本机连接凭据由 Python 采集进程持有。

`COPYING` 保留官方固定版本的 wxWindows Library Licence 3.1 原文。
上游来源为 https://github.com/frida/frida-core/blob/17.26.1/COPYING 。
