# 阿里云DDNS（astrbot_plugin_fns_ddns）

<div align="center">

基于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的阿里云 DDNS 插件

自动检测公网 IPv4 变化，并同步更新阿里云域名解析记录

</div>

<div align="center">

![Version](https://img.shields.io/badge/version-v1.0.2-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-brightgreen)
![License](https://img.shields.io/badge/license-AGPL--3.0-red)

</div>

## 目录

- [简介](#简介)
- [功能特性](#功能特性)
- [环境要求](#环境要求)
- [安装](#安装)
- [配置](#配置)
- [工作原理](#工作原理)
- [目录结构](#目录结构)
- [注意事项](#注意事项)
- [更新记录](#更新记录)
- [开源许可](#开源许可)

## 简介

本插件周期性查询本机公网 IPv4 地址，并与阿里云 DNS 上的 A 记录进行比对。当公网 IP 发生变化时，自动更新（或新建）对应的 A 解析记录，让域名始终指向最新的公网 IP。

适用于家庭宽带、无固定公网 IP 的服务器、NAS 等需要对外提供服务的场景。

## 功能特性

- 自动检测公网 IPv4 地址，多个公共 API 轮询兜底
- IP 变化时自动 **更新 / 新建** 阿里云 DNS 的 A 记录
- 支持主域名（`@`）与任意子域名（如 `www`、`api`）
- 智能清理同名重复 A 记录，避免脏数据累积
- 更新冷却机制（`min_cool_second`），避免触发阿里云 API 限流
- 查询失败自动重试，异常不影响主循环
- 优雅停止：插件卸载 / 重载时自动取消后台任务

## 环境要求

- AstrBot v3.x 及以上
- Python 3.10+
- 阿里云账号，并开通云解析 DNS 服务
- 公网可访问（插件需要从公网 API 查询出口 IP）

## 安装

### 方式一：通过 AstrBot 插件商店（推荐）

AstrBot 管理面板 → 插件管理 → 插件商店，搜索 **阿里云DDNS** 并安装。

### 方式二：通过 Git 地址安装

AstrBot 管理面板 → 插件管理 → 通过 Git 地址安装：

```
https://github.com/FuLingSnow/astrbot_plugin_fns_ddns.git
```

### 方式三：手动安装

将本项目克隆/下载到 AstrBot 的 `data/plugins/` 目录下，重启 AstrBot 即可。

## 配置

安装后在插件管理中找到本插件，点击「配置」填写以下参数：

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `access_key_id` | string | 空 | 阿里云 AccessKey ID |
| `access_key_secret` | string | 空 | 阿里云 AccessKey Secret |
| `domain` | string | 空 | 主域名，例如 `example.com`，不要带 `http://` 或末尾斜杠 |
| `sub_domain` | string | 空 | 子域名：填 `www` 更新 `www.example.com`；填 `@` 更新主域名 `example.com`；留空自动设为 `@` |
| `check_interval` | int | 300 | 检查间隔（秒），建议 ≥ 60，最小强制 30 |
| `min_cool_second` | int | 60 | 单次更新成功后的最小冷却时间（秒），防止 API 限流 |

> 完整域名 = `sub_domain` + `.` + `domain`（`sub_domain` 为 `@` 时即主域名本身）。

### 配置示例

```json
{
    "access_key_id": "LTAI5tXXXXXXXXXXXXXXXX",
    "access_key_secret": "your_access_key_secret",
    "domain": "example.com",
    "sub_domain": "www",
    "check_interval": 300,
    "min_cool_second": 60
}
```

## 工作原理

```
┌────────────┐      ┌────────────────┐      ┌──────────────────┐
│  查询公网IP  │ ──► │ 对比阿里云DNS记录  │ ──► │ 一致：跳过 / 变更：更新 │
└────────────┘      └────────────────┘      └──────────────────┘
```

1. 每隔 `check_interval` 秒从公共接口获取当前公网 IPv4（多接口轮询，失败自动重试）；
2. 查询阿里云解析记录中该域名的现有 A 记录；
3. 无记录 → **新建**；记录 IP 与当前 IP 不一致 → **更新**；一致 → 跳过；
4. 若存在多条同名 A 记录，保留一条并清理其余。

## 目录结构

```
astrbot_plugin_fns_ddns/
├── __init__.py          # 插件入口（register 函数）
├── main.py              # 插件主逻辑（DDNS 循环、阿里云 API 封装）
├── metadata.yaml        # 插件元数据（名称、版本、作者、仓库地址）
├── _conf_schema.json    # 配置项 Schema（插件配置面板定义）
├── requirements.txt     # 依赖清单
└── README.md            # 本文档
```

## 注意事项

- 仅支持 **IPv4**，IPv6 场景请使用其他方案。
- 建议使用阿里云 **RAM 子账号** 密钥，仅授予 `AliyunDNSFullAccess`（或更小范围）权限，避免主账号密钥泄露风险。
- 插件依赖 `aliyun-python-sdk-core`、`aliyun-python-sdk-alidns`、`requests`，首次启动时 AstrBot 会自动安装 `requirements.txt` 中的依赖。
- 修改配置保存后 AstrBot 会自动重载插件并应用新参数。
- 若无需解析的主机记录，请确保阿里云控制台已添加该主域名。

## 更新记录

- **v1.0.2**：统一代码风格并精简仓库配置（按 ruff 规范格式化代码、整理 `.gitattributes`/`.gitignore` 规则）。
- **v1.0.1**：新增公网 IP 校验，排除回环（127.x.x.x）、私网、链路本地、CGNAT 等非公网地址，防止将内网地址误写入阿里云 DNS 解析；新增插件 Logo（AstrBot 自动识别）。
- **v1.0.0**：首个版本，支持阿里云 DDNS 自动更新、多接口公网 IP 轮询、重复记录清理与更新冷却。

## 开源许可

本项目基于 [GNU Affero General Public License v3.0 (AGPL-3.0)](./LICENSE) 协议开源。

作者：[FuLingSnow](https://github.com/FuLingSnow) · 欢迎提交 [Issue](https://github.com/FuLingSnow/astrbot_plugin_fns_ddns/issues) 与 PR
