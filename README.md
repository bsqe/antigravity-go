# Antigravity-Go

仿照 `dsh-Go` 架构设计的 **Google Antigravity 移动端远程操控套件**：
- **100% 官方合规零改包**：电脑端基于官方公开的 `google-antigravity` Python SDK 与 Antigravity 2.0 原生 **Plugin / Sidecar / Hooks** 机制运行，跨 Windows / macOS / Linux。
- **跨网极速连接**：局域网扫码配对 + **48 位随机配对口令 + 设备令牌 SHA-256 存证**，自动融合并并行测速 **LAN / Tailscale / Cloudflare Tunnel** 三通道择优连接。
- **原生 Flutter 客户端**：纯 Dart 协议包 `packages/agy_client` + Flutter/Riverpod 原生 App（`app/`），支持多主机切换、多项目工作区、多会话持久化、思考过程折叠卡片、Markdown 渲染、相册/拍照发图与中途停止生成。

---

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `bridge/` | 电脑端 Python Bridge 服务（FastAPI + WebSocket + SQLite WAL + `google-antigravity` SDK） |
| `plugin/` | Antigravity 2.0 原生插件（`plugin.json`、`sidecars/bridge/sidecar.json`、`hooks.json`） |
| `packages/agy_client/` | 纯 Dart 协议客户端 SDK（零 Flutter 依赖，含多通道并行测速与 WS 断线补发） |
| `app/` | Flutter 原生手机 App（Android + iOS） |
| `scripts/` | Windows / macOS / Linux 插件一键安装脚本与 Windows `.exe` 打包脚本（`build-exe.ps1`） |
| `.github/workflows/ci.yml` | Python `pytest` + Windows 便携版 `.exe` 打包 + Dart/Flutter 测试 + 云端自动构建 Android APK |

---

## 快速开始

### 1. 启动电脑端 Bridge（两种方式任选）

#### 方式 A：Windows 一键免环境 `.exe`（推荐）
- 可直接从 GitHub Actions Artifacts 下载 `antigravity-go-bridge-win-x64.zip` 解压，或本机运行 `.\scripts\build-exe.ps1` 本地打包。
- **双击 `AntigravityGoBridge.exe`**（务必保留同目录下的 `_internal` 文件夹）：
  - 首次运行会自动进入交互式引导：输入 API Key（支持 `AIza...` Gemini API Key 或 `AQ....` Vertex AI Express Mode Key）和本地项目目录，自动写入配置与白名单。
  - 启动后直接显示终端配对二维码，等待手机 App 扫码连接。

#### 方式 B：Python 源码运行（Windows / macOS / Linux）

需要 Python `3.11+`：

```powershell
cd bridge
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

配置官方 SDK 凭据（二选一）：
- **Google AI Studio API Key（推荐，免费免绑卡）**：设置环境变量 `GEMINI_API_KEY` 或写入 `bridge-config.json`（默认模式 `api_key`，同时兼容 2026 新版 `AQ....` 与旧版 `AIza...` 前缀）
- **Vertex AI（GCP 项目 / ADC / Vertex Express）**：启动时显式加上 `--auth-mode vertex`（或在配置中设 `"auth_mode": "vertex"`）

登记一个允许远程访问的工作区目录并启动（加 `--trust-root` 会自动加入允许根目录白名单，无需手改 JSON）：

```powershell
.\.venv\Scripts\python.exe -m agy_bridge.main add-project "E:\path\to\your\project" --name "MyProject" --trust-root
.\.venv\Scripts\python.exe -m agy_bridge.main start
```

启动后终端会打印本机 LAN / Tailscale / Cloudflare 地址和**配对二维码**。

- 查看二维码：`python -m agy_bridge.main pair`
- 轮换 48 位配对口令（旧码立即作废）：`python -m agy_bridge.main reset-token`
- 启用免登录 Cloudflare 临时外网隧道（需本机已安装 `cloudflared`）：启动时追加 `--cloudflared quick`
- 允许 SDK 执行写文件/命令工具（默认只读安全模式）：启动时追加 `--allow-write-tools`

### 2. 挂载为 Antigravity 2.0 原生插件 / Sidecar（可选）

让 Antigravity 2.0 自动拉起守护 Bridge 并通过 `hooks.json` 实时推送桌面会话事件：

- **Windows**：
  ```powershell
  .\scripts\install-plugin.ps1
  ```
- **macOS / Linux**：
  ```bash
  bash scripts/install-plugin.sh
  ```

插件将安装到 `~/.gemini/config/plugins/antigravity-go/`。

### 3. 构建或安装 Flutter 手机 App

#### 方式 A：GitHub Actions 云端自动出包（本机无需装 Flutter）
将仓库推送到 GitHub 后，触发 `.github/workflows/ci.yml`，即可在 Actions Artifacts 中直接下载 `app-arm64-v8a-release.apk`。

#### 方式 B：本机安装 Flutter SDK 后构建

```bash
cd packages/agy_client && dart pub get && dart test
cd ../../app
# 若尚未生成 Gradle Wrapper，先补齐 Android 构建骨架（保留自定义 AndroidManifest.xml）
flutter pub get
flutter analyze
flutter test
flutter build apk --release --split-per-abi
```

打开手机 App → 点击「扫码配对电脑」→ 扫描终端二维码即可开始对话。

---

## 运行自动化测试

```powershell
# Bridge 全量单元 + 集成 + 官方 SDK 契约测试
.\bridge\.venv\Scripts\python.exe -m pytest -v bridge/tests
```
