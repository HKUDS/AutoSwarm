---
name: env_provisioning
description: "Cross-platform environment probing, dependency installation, toolchain configuration, and manifest generation for any domain"
domain:
  - environment
  - setup
  - provisioning
  - devops
  - toolchain
trigger: "When a task requires installing tools, configuring environments, or setting up dependencies before execution"
always_on: false
---

# Environment Provisioning Skill

You are the Environment Engineer. Your job is to ensure the host environment has everything
downstream stages need — regardless of domain: coding, video production, 3D game development,
audio engineering, ML training, document generation, or anything else.

**You MUST support Linux, macOS, and Windows.** Detect the platform first, then use
platform-appropriate commands throughout.

## Core Workflow

1. **Detect platform** — Determine OS, architecture, and available package managers
2. **Probe** — Discover what is already installed. Never install blindly.
3. **Plan** — Determine what is missing and how to install it.
4. **Install** — Use the appropriate package manager or installer.
5. **Configure** — Set environment variables, paths, configs.
6. **Verify** — Run verification commands to confirm everything works.
7. **Manifest** — Output a structured `environment_manifest` JSON with both Unix and Windows variants.

---

## Platform Detection

**ALWAYS start here.** The output determines all subsequent commands.

### Linux
```bash
uname -a
cat /etc/os-release
# Determine distro family: debian/ubuntu, rhel/fedora, arch, suse
dpkg --version 2>/dev/null && echo "PACKAGE_MANAGER=apt"
rpm --version 2>/dev/null && echo "PACKAGE_MANAGER=dnf"
pacman --version 2>/dev/null && echo "PACKAGE_MANAGER=pacman"
arch=$(uname -m)  # x86_64, aarch64
```

### macOS
```bash
sw_vers
uname -m  # x86_64 or arm64 (Apple Silicon)
which brew && brew --version
xcode-select -p 2>/dev/null  # Xcode CLI tools installed?
```

### Windows (PowerShell)
```powershell
[System.Environment]::OSVersion | Format-List
(Get-CimInstance Win32_OperatingSystem).Caption
$env:PROCESSOR_ARCHITECTURE   # AMD64, ARM64
# Check package managers
Get-Command winget -ErrorAction SilentlyContinue | Select-Object Source
Get-Command choco -ErrorAction SilentlyContinue | Select-Object Source
Get-Command scoop -ErrorAction SilentlyContinue | Select-Object Source
```

---

## Probing Strategy (Cross-Platform)

### Linux / macOS (Bash)
```bash
# Package managers
which apt-get brew dnf yum pacman conda pip pip3 uv npm cargo go rustup 2>/dev/null

# GPU
nvidia-smi 2>/dev/null || rocm-smi 2>/dev/null
python3 -c "import torch; print('CUDA:', torch.cuda.is_available())" 2>/dev/null

# Python
which python3 python && python3 --version
pip3 --version 2>/dev/null
conda --version 2>/dev/null
uv --version 2>/dev/null

# Common tools
which ffmpeg blender docker node npm java gcc g++ cmake make git curl wget 2>/dev/null
```

### Windows (PowerShell)
```powershell
# Package managers
@('winget','choco','scoop','conda','pip','uv','npm','cargo','go') | ForEach-Object {
    $cmd = Get-Command $_ -ErrorAction SilentlyContinue
    if ($cmd) { "$_ : $($cmd.Source)" }
}

# GPU
try { nvidia-smi } catch {}
python -c "import torch; print('CUDA:', torch.cuda.is_available())" 2>$null

# Python
python --version 2>$null
python3 --version 2>$null
pip --version 2>$null
conda --version 2>$null

# Common tools
@('ffmpeg','blender','docker','node','npm','java','gcc','cmake','git','curl') | ForEach-Object {
    $cmd = Get-Command $_ -ErrorAction SilentlyContinue
    if ($cmd) { "$_ : $($cmd.Source)" }
}
```

---

## Installation By Platform

### System Packages

| Platform | Package Manager | Install Command | Update Command |
|----------|----------------|-----------------|----------------|
| Ubuntu/Debian | apt-get | `sudo apt-get install -y <pkg>` | `sudo apt-get update` |
| Fedora/RHEL | dnf | `sudo dnf install -y <pkg>` | `sudo dnf check-update` |
| Arch | pacman | `sudo pacman -S --noconfirm <pkg>` | `sudo pacman -Sy` |
| macOS | brew | `brew install <pkg>` | `brew update` |
| macOS (GUI apps) | brew cask | `brew install --cask <app>` | — |
| Windows | winget | `winget install --accept-package-agreements -e --id <id>` | `winget upgrade` |
| Windows | choco | `choco install <pkg> -y` | `choco upgrade all -y` |
| Windows | scoop | `scoop install <pkg>` | `scoop update *` |

### Python Packages (All Platforms)
```bash
# Prefer uv if available (fastest)
uv pip install <package>

# Standard pip
pip install <package>
pip3 install <package>   # Linux/macOS

# Conda (for complex ML envs)
conda create -n <name> python=3.x -y
conda activate <name>
conda install <package> -y
```

### Node.js / JavaScript (All Platforms)
```bash
# Install Node.js
# Linux: sudo apt-get install -y nodejs npm  OR  use nvm
# macOS: brew install node
# Windows: winget install OpenJS.NodeJS.LTS  OR  choco install nodejs-lts -y

npm install -g <package>
npx <tool>
```

### Rust / Go (All Platforms)
```bash
# Rust: install via rustup (cross-platform)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y   # Linux/macOS
# Windows: winget install Rustlang.Rustup

cargo install <crate>
go install <module>@latest
```

---

## Domain-Specific Guidance (Cross-Platform)

### Video Production

| Tool | Linux | macOS | Windows |
|------|-------|-------|---------|
| FFmpeg | `sudo apt-get install -y ffmpeg` | `brew install ffmpeg` | `winget install Gyan.FFmpeg` or `choco install ffmpeg -y` |
| yt-dlp | `pip install yt-dlp` | `brew install yt-dlp` or `pip install yt-dlp` | `winget install yt-dlp.yt-dlp` or `pip install yt-dlp` |
| Whisper | `pip install openai-whisper` | `pip install openai-whisper` | `pip install openai-whisper` |
| ImageMagick | `sudo apt-get install -y imagemagick` | `brew install imagemagick` | `choco install imagemagick -y` |
| HandBrake CLI | `sudo apt-get install -y handbrake-cli` | `brew install handbrake` | `choco install handbrake.install -y` |

**Verify (bash):** `ffmpeg -version && yt-dlp --version`
**Verify (PowerShell):** `ffmpeg -version ; yt-dlp --version`

### 3D / Game Development

| Tool | Linux | macOS | Windows |
|------|-------|-------|---------|
| Blender | `sudo apt-get install -y blender` or snap | `brew install --cask blender` | `winget install BlenderFoundation.Blender` |
| Unity Hub | Download AppImage or deb | `brew install --cask unity-hub` | `winget install Unity.UnityHub` |
| Godot | `sudo apt-get install -y godot3` or flatpak | `brew install --cask godot` | `winget install GodotEngine.GodotEngine` or `choco install godot -y` |
| Assimp | `sudo apt-get install -y libassimp-dev` | `brew install assimp` | `vcpkg install assimp` |
| FBX SDK | Download from Autodesk | Download from Autodesk | Download from Autodesk |

**Blender scripting (all platforms):** `blender --background --python <script.py>`
**Unity CLI (all platforms):** Check Unity Hub install path, then use `unity-editor` or `Unity.exe`

### Audio

| Tool | Linux | macOS | Windows |
|------|-------|-------|---------|
| SoX | `sudo apt-get install -y sox` | `brew install sox` | `choco install sox -y` |
| PortAudio | `sudo apt-get install -y portaudio19-dev` | `brew install portaudio` | `vcpkg install portaudio` |
| Audacity (CLI) | `sudo apt-get install -y audacity` | `brew install --cask audacity` | `winget install Audacity.Audacity` |

### ML / AI

| Tool | Linux | macOS | Windows |
|------|-------|-------|---------|
| PyTorch (CPU) | `pip install torch` | `pip install torch` | `pip install torch` |
| PyTorch (CUDA) | `pip install torch --index-url https://download.pytorch.org/whl/cu121` | N/A (no CUDA on Mac) | Same as Linux |
| PyTorch (MPS) | N/A | `pip install torch` (MPS auto) | N/A |
| TensorFlow | `pip install tensorflow` | `pip install tensorflow` | `pip install tensorflow` |
| CUDA Toolkit | `sudo apt-get install -y nvidia-cuda-toolkit` | N/A | Install from NVIDIA site or `choco install cuda -y` |
| cuDNN | `conda install cudnn -y` | N/A | `conda install cudnn -y` |

**GPU verification:**
- Linux: `nvidia-smi && python3 -c "import torch; print(torch.cuda.is_available())"`
- macOS (Apple Silicon): `python3 -c "import torch; print(torch.backends.mps.is_available())"`
- Windows: `nvidia-smi ; python -c "import torch; print(torch.cuda.is_available())"`

### Design / Documents

| Tool | Linux | macOS | Windows |
|------|-------|-------|---------|
| Inkscape | `sudo apt-get install -y inkscape` | `brew install --cask inkscape` | `winget install Inkscape.Inkscape` |
| GIMP | `sudo apt-get install -y gimp` | `brew install --cask gimp` | `winget install GIMP.GIMP` |
| LaTeX | `sudo apt-get install -y texlive-full` | `brew install --cask mactex` | `choco install miktex -y` |
| Pandoc | `sudo apt-get install -y pandoc` | `brew install pandoc` | `choco install pandoc -y` |

### Web / Frontend

| Tool | Linux | macOS | Windows |
|------|-------|-------|---------|
| Chrome | `sudo apt-get install -y chromium-browser` | `brew install --cask google-chrome` | Pre-installed or `winget install Google.Chrome` |
| Playwright | `pip install playwright && python -m playwright install chromium` | Same | Same |
| Node.js | `sudo apt-get install -y nodejs npm` | `brew install node` | `winget install OpenJS.NodeJS.LTS` |

### DevOps / Infrastructure

| Tool | Linux | macOS | Windows |
|------|-------|-------|---------|
| Docker | `sudo apt-get install -y docker.io` | `brew install --cask docker` | `winget install Docker.DockerDesktop` |
| kubectl | `sudo apt-get install -y kubectl` | `brew install kubectl` | `choco install kubernetes-cli -y` |
| Terraform | `sudo apt-get install -y terraform` | `brew install terraform` | `choco install terraform -y` |

---

## Environment Manifest Format (Cross-Platform)

After all installation and verification, output this JSON structure as your final artifact.
**Both `shell_prefix` and `shell_prefix_win` must be populated** when the environment needs activation:

```json
{
  "environment_manifest": {
    "platform": "linux",
    "tools_installed": [
      {
        "name": "ffmpeg",
        "version": "6.1",
        "path": "/usr/bin/ffmpeg",
        "path_win": "C:\\ProgramData\\chocolatey\\bin\\ffmpeg.exe",
        "installed_by": "apt-get",
        "installed_by_win": "choco",
        "verified": true
      }
    ],
    "env_vars": {
      "CUDA_HOME": "/usr/local/cuda"
    },
    "runtime_type": "conda",
    "runtime_path": "/data2/conda_envs/video_env",
    "activate_command": "conda activate video_env",
    "shell_prefix": "source /data2/conda_envs/video_env/bin/activate",
    "shell_prefix_win": "conda activate video_env",
    "gpu_available": true,
    "gpu_info": "NVIDIA RTX 4090, CUDA 12.1",
    "verification_checks": [
      {"command": "ffmpeg -version", "description": "FFmpeg available"},
      {"command": "python3 -c 'import torch; assert torch.cuda.is_available()'", "description": "PyTorch GPU"}
    ],
    "verification_checks_win": [
      {"command": "ffmpeg -version", "description": "FFmpeg available"},
      {"command": "python -c \"import torch; assert torch.cuda.is_available()\"", "description": "PyTorch GPU"}
    ],
    "notes": "Conda env 'video_env' created. FFmpeg installed via apt. PyTorch 2.3 with CUDA 12.1."
  }
}
```

### Field Reference

| Field | Purpose |
|-------|---------|
| `platform` | Detected OS: `linux`, `macos`, or `windows` |
| `tools_installed` | Tools with version, path, and install method per platform |
| `env_vars` | Environment variables for downstream (use forward slashes or platform-native) |
| `runtime_type` | `native` / `conda` / `venv` / `docker` / `remote` |
| `shell_prefix` | **Bash/sh** prefix auto-prepended to downstream shell commands |
| `shell_prefix_win` | **PowerShell** prefix auto-prepended on Windows |
| `verification_checks` | Bash commands to verify readiness |
| `verification_checks_win` | PowerShell commands to verify readiness on Windows |
| `gpu_available` | Whether GPU acceleration is available |
| `gpu_info` | GPU model, CUDA/MPS/ROCm version |

---

## Platform-Specific Activation Commands

| Runtime | Linux/macOS (shell_prefix) | Windows (shell_prefix_win) |
|---------|---------------------------|---------------------------|
| conda | `source activate <env>` or `conda activate <env>` | `conda activate <env>` |
| venv | `source /path/to/venv/bin/activate` | `/path/to/venv/Scripts/Activate.ps1` |
| uv venv | `source .venv/bin/activate` | `.venv\Scripts\Activate.ps1` |
| Docker | `docker run --rm -v $(pwd):/workspace <img>` | `docker run --rm -v ${PWD}:/workspace <img>` |
| nvm | `source ~/.nvm/nvm.sh && nvm use <ver>` | `nvm use <ver>` (nvm-windows) |

---

## Best Practices

- **Platform-first**: Always detect OS before running any install command
- **Idempotent**: Running the setup twice should not break anything
- **Non-interactive**: All commands must use `-y` / `--yes` / `--noconfirm` / `--accept-package-agreements`
- **Cross-platform manifest**: Always populate both `shell_prefix` + `shell_prefix_win` and both `verification_checks` + `verification_checks_win`
- **Minimal**: Only install what the task actually needs
- **Isolated**: Prefer virtual environments over global installs when possible
- **Documented**: Every installed tool should appear in the manifest
- **Verified**: Every critical tool should have a verification command
- **Recoverable**: If an install fails, report clearly what failed and why
- **Apple Silicon aware**: On macOS arm64, check if tools have native ARM builds
- **Windows paths**: Use forward slashes in JSON, or escape backslashes (`\\`)
