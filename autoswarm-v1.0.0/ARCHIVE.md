# AutoSwarm v1.0.0

This directory is the complete source archive of AutoSwarm before the next major upgrade, frozen on **2026-10-20**. The source commit and release adjustments are recorded in [RELEASE.json](RELEASE.json).

此目录是 AutoSwarm 全面升级前的 **v1.0.0 完整源码归档**。代码、前后端资源、测试、配置模板、技能、共享人才与组织模板、文档和许可证均保留在此目录中。新版在[仓库根目录](../README.zh-CN.md)继续开发。

## Install and run / 安装运行

From the repository root, enter this directory and use a separate virtual environment:

从仓库根目录进入归档目录，为旧版本创建独立环境：

```bash
cd autoswarm-v1.0.0
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
autoswarm init
autoswarm ui
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.

The compatible `opc` command remains available. Follow [README.md](README.md) or [README.zh-CN.md](README.zh-CN.md) for configuration and usage. Run commands from this directory to keep the default `.opc` runtime directory separate from the development version. If `OPC_HOME` is set, choose a separate path for this release.

保留 `opc` 兼容命令。配置与功能说明见归档内的中英文 README。请在此目录运行命令，使默认的 `.opc` 数据目录与新版分离；若设置了 `OPC_HOME`，请为旧版本指定独立路径。

## Contents and integrity / 内容与校验

The archive copies all 994 Git-tracked files from the source commit. The Python project, runtime version, and frontend package metadata are normalized from `0.1.0` to `1.0.0`; README notices and release metadata identify this frozen release. Application behavior is unchanged.

The ignore rules preserve the originally tracked empty `.codex` marker. Existing ignored local runtime data and dependency caches were relocated here alongside the source; they remain ignored and are excluded from the source bundle and integrity manifest.

本地密钥、私有配置、数据库、日志、虚拟环境、Node 依赖与缓存不属于源码归档。未受版本控制且仍使用旧包名的本地 `uv.lock` 也未收录；Python 依赖沿用原项目声明，尚未锁定全部传递依赖。

`SHA256SUMS` covers every archived source file, including hidden shared templates and release metadata. Verify from this directory with:

```bash
sha256sum -c SHA256SUMS
```

To run the archived tests, keep the archived package first on the Python import path:

```bash
python -m pytest tests
```

The nested `.github/` directory preserves the historical repository configuration. The active repository workflows live in the root `.github/` directory.

## Maintenance / 维护约定

Treat this directory as a frozen release. Make new features and refactors in the repository root. If an intentional v1.0.0 correction is necessary, document it in `RELEASE.json` and regenerate `SHA256SUMS`.

此目录作为冻结版本保留；新功能和重构在仓库根目录进行。若确需修正归档，请同步记录变更并更新校验清单。
