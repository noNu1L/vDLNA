# vDLNA

将 Windows 系统音频通过 DLNA 协议实时推送到局域网内的 DLNA 播放器。

---

## 环境要求

- Windows 10 / 11（64 位）
- Python 3.11+
- [VB-CABLE](https://vb-audio.com/Cable/index.htm) 虚拟声卡驱动

---

## 快速开始

```bash
# 创建虚拟环境
py -3.11 -m venv .venv
.venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 运行
python main.py
```

### 使用步骤

1. 选择"监听设备"（VB-CABLE）
2. 点击"搜索设备"扫描 DLNA 播放器
3. 选择目标设备，点击"建立连接"
4. 将 Windows 音频输出切换到 VB-CABLE 即可推流

---

## 打包为 exe

```bash
# 单文件打包
pyinstaller --noconfirm --clean --onefile --windowed ^
    --name vDLNA ^
    --icon assets\icon.ico ^
    --add-data "assets;assets" ^
    --add-binary ".venv\Lib\site-packages\pyflac\libraries\windows-x86_64\libFLAC.dll;pyflac\libraries\windows-x86_64" ^
    main.py
```

---

## 常见问题

- **搜索不到 DLNA 设备**：检查 Windows 防火墙是否阻止 UDP 1900 端口
- **播放卡顿或无声**：确认 VB-CABLE 采样率与编码器一致（默认 44100Hz）
