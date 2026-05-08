# vDLNA

使用虚拟声卡(VB-Audio)采集声音，推流至DLNA设备播放。

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
4. 将 Windows 音频（或音乐软件）输出切换到 VB-CABLE 即可推流
