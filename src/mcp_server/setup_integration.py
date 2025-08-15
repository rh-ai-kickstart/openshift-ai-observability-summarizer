#!/usr/bin/env python3
"""
Claude Desktop and Cursor IDE Integration Setup Script

This script automatically configures MCP integration for:
- Claude Desktop (claude_desktop_config.json)
- Cursor IDE (.cursor/mcp.json)

Features:
- Auto-detects project paths and virtual environment
- Backs up existing configurations
- Generates configuration with current MCP tools
- Validates JSON and tests MCP server
- Cross-platform support (macOS/Linux/Windows)
"""

import json
import os
import sys
import shutil
import subprocess
import platform
from pathlib import Path
from typing import Dict, Any, Optional


MODEL_CONFIG_DEFAULT = '{"google/gemini-2.5-flash":{"apiUrl":"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent","cost":{"output_rate":0.000001,"prompt_rate":3e-7},"external":true,"modelName":"gemini-2.5-flash","provider":"google","requiresApiKey":true,"serviceName":null},"meta-llama/Llama-3.2-3B-Instruct":{"external":false,"requiresApiKey":false,"serviceName":"llama-3-2-3b-instruct"},"openai/gpt-4o-mini":{"apiUrl":"https://api.openai.com/v1/chat/completions","cost":{"output_rate":6e-7,"prompt_rate":1.5e-7},"external":true,"modelName":"gpt-4o-mini","provider":"openai","requiresApiKey":true,"serviceName":null}}'

def get_project_root() -> Path:
    """Find the project root by looking for pyproject.toml and src/ directory."""
    current = Path.cwd()
    
    # Start from current directory and walk up
    for path in [current] + list(current.parents):
        if (path / "pyproject.toml").exists() and (path / "src").exists():
            return path
    
    # Fallback: assume we're in src/mcp_server and go up two levels
    if current.name == "mcp_server" and current.parent.name == "src":
        return current.parent.parent
    
    return current


def find_virtual_env(project_root: Path) -> Optional[Path]:
    """Find the virtual environment directory."""
    possible_venvs = [".venv", "venv", ".virtualenv"]
    
    for venv_name in possible_venvs:
        venv_path = project_root / venv_name
        if venv_path.exists() and venv_path.is_dir():
            return venv_path
    
    return None


def get_mcp_server_executable(project_root: Path) -> str:
    """Get the HTTP MCP server executable path (not used for stdio)."""
    venv_path = find_virtual_env(project_root)
    if not venv_path:
        print("⚠️  Warning: Virtual environment not found, using system Python")
        return "obs-mcp-server"
    if platform.system() == "Windows":
        executable = venv_path / "Scripts" / "obs-mcp-server.exe"
        if not executable.exists():
            executable = venv_path / "Scripts" / "obs-mcp-server"
    else:
        executable = venv_path / "bin" / "obs-mcp-server"
    return str(executable)


def get_mcp_stdio_executable(project_root: Path) -> str:
    """Get the STDIO MCP server executable path (recommended for Claude/Cursor)."""
    venv_path = find_virtual_env(project_root)
    if not venv_path:
        print("⚠️  Warning: Virtual environment not found, using system Python")
        return "obs-mcp-stdio"
    if platform.system() == "Windows":
        executable = venv_path / "Scripts" / "obs-mcp-stdio.exe"
        if not executable.exists():
            executable = venv_path / "Scripts" / "obs-mcp-stdio"
    else:
        executable = venv_path / "bin" / "obs-mcp-stdio"
    return str(executable)


def get_claude_config_path() -> Path:
    """Get Claude Desktop configuration file path."""
    if platform.system() == "Darwin":  # macOS
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif platform.system() == "Windows":
        return Path.home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    else:  # Linux
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def get_cursor_config_path(project_root: Path) -> Path:
    """Get Cursor IDE configuration file path."""
    return project_root / ".cursor" / "mcp.json"


def backup_config(config_path: Path) -> Optional[Path]:
    """Create a backup of existing configuration."""
    if config_path.exists():
        backup_path = config_path.with_suffix(f"{config_path.suffix}.backup")
        shutil.copy2(config_path, backup_path)
        print(f"📁 Backed up existing config to: {backup_path}")
        return backup_path
    return None


def generate_claude_config(mcp_stdio_path: str) -> Dict[str, Any]:
    """Generate Claude Desktop MCP configuration (STDIO)."""
    return {
        "mcpServers": {
            "ai-observability": {
                "command": mcp_stdio_path,
                # No args needed for stdio server
                "env": {
                    "PROMETHEUS_URL": "http://localhost:9090",
                    "MODEL_CONFIG": MODEL_CONFIG_DEFAULT
                },
                "autoApprove": [
                    "list_models",
                    "list_namespaces",
                    "get_model_config"
                ],
                "alwaysAllow": [
                    "list_models", 
                    "list_namespaces",
                    "get_model_config"
                ],
                "disabled": False
            }
        }
    }


def generate_cursor_config(mcp_stdio_path: str) -> Dict[str, Any]:
    """Generate Cursor IDE MCP configuration (STDIO)."""
    return {
        "mcpServers": {
            "ai-observability": {
                "command": mcp_stdio_path,
                "env": {
                    "PROMETHEUS_URL": "http://localhost:9090",
                    "LLAMA_STACK_URL": "http://localhost:8321/v1/openai/v1",
                    "MODEL_CONFIG": MODEL_CONFIG_DEFAULT
                },
                "disabled": False
            }
        }
    }


def write_config(config_path: Path, config: Dict[str, Any]) -> bool:
    """Write configuration to file with validation."""
    try:
        # Ensure directory exists
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write configuration
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        # Validate JSON
        with open(config_path, 'r') as f:
            json.load(f)
        
        print(f"✅ Configuration written to: {config_path}")
        return True
        
    except Exception as e:
        print(f"❌ Error writing configuration: {e}")
        return False


def test_mcp_server(mcp_path: str) -> bool:
    """Test if the MCP server executable exists. Avoid running it (stdio)."""
    try:
        p = Path(mcp_path)
        if p.exists() and os.access(p, os.X_OK):
            print("✅ MCP server binary found")
            return True
        print(f"❌ MCP server not executable or missing: {mcp_path}")
        return False
    except Exception as e:
        print(f"❌ Error testing MCP server: {e}")
        return False


def main():
    """Main setup function."""
    print("🚀 OpenShift AI Observability MCP Integration Setup")
    print("=" * 60)
    
    # 1. Detect project paths
    project_root = get_project_root()
    print(f"📁 Project root: {project_root}")
    
    # 2. Get MCP stdio executable path (recommended)
    mcp_stdio_path = get_mcp_stdio_executable(project_root)
    print(f"🔧 MCP stdio server: {mcp_stdio_path}")
    
    # 3. Test MCP server
    if not test_mcp_server(mcp_stdio_path):
        print("\n❌ MCP server test failed. Please ensure it's installed:")
        print("   cd src/mcp_server && pip install -e .")
        return False
    
    # 4. Setup Claude Desktop configuration
    claude_config_path = get_claude_config_path()
    print(f"\n📋 Setting up Claude Desktop configuration...")
    
    # Backup existing Claude config
    backup_config(claude_config_path)
    
    # Generate and write Claude config
    claude_config = generate_claude_config(mcp_stdio_path)
    claude_success = write_config(claude_config_path, claude_config)
    
    # 5. Setup Cursor IDE configuration
    cursor_config_path = get_cursor_config_path(project_root)
    print(f"\n📋 Setting up Cursor IDE configuration...")
    
    # Backup existing Cursor config
    backup_config(cursor_config_path)
    
    # Generate and write Cursor config
    cursor_config = generate_cursor_config(mcp_stdio_path)
    cursor_success = write_config(cursor_config_path, cursor_config)
    
    # 6. Summary
    print("\n" + "=" * 60)
    print("📋 Setup Summary:")
    print(f"   Claude Desktop: {'✅' if claude_success else '❌'}")
    print(f"   Cursor IDE: {'✅' if cursor_success else '❌'}")
    
    if claude_success and cursor_success:
        print("\n🎉 Integration setup complete!")
        print("\n📝 Next steps:")
        print("   1. Start development environment: scripts/local-dev.sh")
        print("   2. Restart Claude Desktop application") 
        print("   3. Restart Cursor IDE")
        print("   4. Test with queries like:")
        print("      - 'What models are available?'")
        print("      - 'What namespaces exist?'")
        
        return True
    else:
        print("\n❌ Setup completed with errors. Please check the messages above.")
        return False


if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⚠️  Setup interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)
