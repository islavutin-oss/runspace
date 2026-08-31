#!/usr/bin/env python3
"""Bootstrap virtual team from team.yml → agents.yml + SOUL.md + systemd services."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml


def render_template(template: str, variables: dict[str, str]) -> str:
    """Simple {{var}} template rendering."""
    result = template
    for key, value in variables.items():
        result = result.replace("{{" + key + "}}", value)
    return result


def build_stakeholders_block(stakeholders: list[dict]) -> str:
    """Build SOUL.md stakeholders section from config."""
    lines = []
    for s in stakeholders:
        lines.append(f"- <@{s['slack_id']}> — {s['role']} ({s['name']})")
    return "\n".join(lines)


def build_priorities_block(priorities: list[str]) -> str:
    """Build numbered priority list."""
    lines = []
    for i, p in enumerate(priorities, 1):
        lines.append(f"{i}. *{p}*")
    return "\n".join(lines)


def build_stakeholder_instructions(stakeholders: list[dict]) -> str:
    """Build tool_instructions snippet for stakeholder tagging."""
    lines = []
    for s in stakeholders:
        lines.append(f"Use <@{s['slack_id']}> for {s['name']}.")
    lines.append("Never use plain text names for stakeholders.")
    return "\n      ".join(lines)


def generate_soul(role: str, config: dict, roles_dir: Path) -> str:
    """Render a SOUL.md from role template + project config."""
    template_path = roles_dir / f"{role}.soul.md"
    if not template_path.exists():
        raise FileNotFoundError(f"Role template not found: {template_path}")

    template = template_path.read_text()

    project = config["project"]
    variables = {
        "project_name": project["name"],
        "project_description": project["description"],
        "domain": project.get("domain", "technology"),
        "linear_team": project.get("linear_team", ""),
        "stakeholders_block": build_stakeholders_block(config["stakeholders"]),
        "priorities_block": build_priorities_block(config.get("priorities", [])),
    }

    return render_template(template, variables)


def generate_agents_yml(
    bot_name: str, bot_config: dict, config: dict, soul_path: str, team_dir: Path = Path(".")
) -> dict:
    """Generate agentino agents.yml structure for a single bot."""
    project = config["project"]
    stakeholder_instructions = build_stakeholder_instructions(config["stakeholders"])

    # Render tool_instructions with project variables
    tool_instructions = bot_config.get("tool_instructions", "")
    tool_instructions = render_template(
        tool_instructions,
        {
            "project_name": project["name"],
            "linear_team": project.get("linear_team", ""),
        },
    )

    # Append formatting + stakeholder instructions
    tool_instructions += f"""
      MANDATORY: You MUST call at least one tool before responding with text.
      Never answer from memory. Always verify with tools first.

      FORMATTING: You are in Slack.
      {stakeholder_instructions}"""

    agent = {
        "model": config.get("model", "router/gpt-5.4-codex"),
        "soul": soul_path,
        "tools_dir": str((team_dir / config.get("tools_dir", "../../runspace/tools")).resolve()),
        "tools": bot_config["tools"],
        "temperature": 0.3,
        "max_turns": 50,
        "require_tool_use": True,
        "tool_instructions": tool_instructions,
    }

    slack = bot_config["slack"]
    return {
        "providers": {
            "router": {
                "base_url": "https://router.example.com/v1",
                "api_key": "${AI_API_KEY}",
                "provider": "openai-codex",
            }
        },
        "agents": {bot_name: agent},
        "gateway": {
            "slack": {
                "bot_token": slack["bot_token"],
                "app_token": slack["app_token"],
                "agent": bot_name,
            }
        },
    }


def bootstrap(team_path: Path) -> dict[str, Path]:
    """Generate all config files from team.yml. Returns {bot_name: output_dir}."""
    team_dir = team_path.resolve().parent
    base_dir = team_dir.parent.parent  # virtual_team/
    roles_dir = base_dir / "roles"

    config = yaml.safe_load(team_path.read_text())
    config["project"]["name"].lower().replace(" ", "")
    outputs = {}

    for bot_name, bot_config in config["bots"].items():
        role = bot_config["role"]
        bot_dir = team_dir / bot_name
        bot_dir.mkdir(exist_ok=True)

        # Generate SOUL.md
        soul = generate_soul(role, config, roles_dir)
        soul_path = bot_dir / "SOUL.md"
        soul_path.write_text(soul)
        print(f"  ✓ {soul_path}")

        # Generate agents.yml
        agents = generate_agents_yml(bot_name, bot_config, config, "./SOUL.md", team_dir)
        agents_path = bot_dir / "agents.yml"
        agents_path.write_text(
            yaml.dump(agents, default_flow_style=False, allow_unicode=True, sort_keys=False)
        )
        print(f"  ✓ {agents_path}")

        outputs[bot_name] = bot_dir

    return outputs


def generate_systemd(project_slug: str, bot_name: str, team_dir: Path) -> str:
    """Generate a systemd service file."""
    f"vteam-{project_slug}-{bot_name}"
    env_file = team_dir.resolve() / ".env"
    work_dir = team_dir.resolve()

    return f"""[Unit]
Description=Virtual Team — {project_slug}/{bot_name}
After=network.target

[Service]
Type=simple
WorkingDirectory={work_dir}
EnvironmentFile={env_file}
Environment=PYTHONUNBUFFERED=1
ExecStart=path/to/agentino/.venv/bin/agentino run {bot_name}/agents.yml --gateway
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def install_services(team_path: Path, outputs: dict[str, Path], action: str = "start"):
    """Install and manage systemd services."""
    config = yaml.safe_load(team_path.read_text())
    project_slug = config["project"]["name"].lower().replace(" ", "")
    team_dir = team_path.parent
    services = []

    for bot_name in outputs:
        service_name = f"vteam-{project_slug}-{bot_name}"
        service_path = Path(f"/etc/systemd/system/{service_name}.service")
        service_content = generate_systemd(project_slug, bot_name, team_dir)
        service_path.write_text(service_content)
        services.append(service_name)
        print(f"  ✓ {service_path}")

    subprocess.run(["systemctl", "daemon-reload"], check=True)

    if action == "start":
        for s in services:
            subprocess.run(["systemctl", "enable", s], check=True, capture_output=True)
            subprocess.run(["systemctl", "restart", s], check=True)
            print(f"  ✓ {s} started")
    elif action == "stop":
        for s in services:
            subprocess.run(["systemctl", "stop", s], check=True)
            print(f"  ✓ {s} stopped")
    elif action == "status":
        for s in services:
            result = subprocess.run(["systemctl", "is-active", s], capture_output=True, text=True)
            print(f"  {s}: {result.stdout.strip()}")


def main():
    parser = argparse.ArgumentParser(description="Bootstrap virtual team from team.yml")
    parser.add_argument("team_yml", type=Path, help="Path to team.yml")
    parser.add_argument("--start", action="store_true", help="Start systemd services")
    parser.add_argument("--stop", action="store_true", help="Stop systemd services")
    parser.add_argument("--status", action="store_true", help="Check service status")
    parser.add_argument(
        "--no-systemd", action="store_true", help="Only generate configs, skip systemd"
    )
    args = parser.parse_args()

    if not args.team_yml.exists():
        print(f"Error: {args.team_yml} not found")
        sys.exit(1)

    print(f"Bootstrapping from {args.team_yml}...")
    outputs = bootstrap(args.team_yml)

    if args.no_systemd:
        print("\nConfigs generated. Skipping systemd.")
        return

    if args.stop:
        print("\nStopping services...")
        install_services(args.team_yml, outputs, "stop")
    elif args.status:
        print("\nService status:")
        install_services(args.team_yml, outputs, "status")
    else:
        print("\nInstalling services...")
        install_services(args.team_yml, outputs, "start")

    print("\nDone.")


if __name__ == "__main__":
    main()
