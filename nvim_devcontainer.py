#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
import tempfile
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ruamel.yaml import YAML
from xdg import BaseDirectory as xdg  # type: ignore

script_dir = os.path.dirname(os.path.realpath(__file__))

config: dict[str, str] = {
    "config_home": str(xdg.xdg_config_home),
}
config_dir = os.path.join(xdg.xdg_config_home, "nvim-devcontainer")
config_file = os.path.join(config_dir, "config.yaml")

if os.path.exists(config_file):
    with open(os.path.join(config_file)) as f:
        yaml = YAML()
        config.update(yaml.load(f))


def config_path(path: str) -> str:
    config_home: Path = Path(config["config_home"])
    return os.path.abspath(os.path.join(config_home, path))


def deep_merge(dict1: dict[str, Any], dict2: dict[str, Any]) -> dict[str, Any]:
    result = dict1.copy()
    for key, value in dict2.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class CommandError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def build(
    base_image_name: str,
    tag: str,
    context_dir: Path | str,
    no_cache: bool = False,
) -> None:
    with open(os.path.join(script_dir, "Dockerfile.amd64"), "r") as f:
        template_contents = f.read()

    new_dockerfile_contents = template_contents.replace(
        "%%__BASE_IMAGE__%%", base_image_name
    )

    with tempfile.NamedTemporaryFile() as f:
        f.write(new_dockerfile_contents.encode())
        f.flush()
        tag = tag or f"{base_image_name}:nvim-devcontainer"
        cmd: list[str] = ["docker", "build"]
        if no_cache:
            cmd.append("--no-cache")
        cmd.extend(["-t", tag, "-f", f.name, str(context_dir)])
        print(" ".join(cmd))
        subprocess.run(cmd, check=True)

    print(f'Image "{tag}" is now available')


def compose(args: argparse.Namespace) -> None:
    compose_file = Path(args.compose_file)
    compose_override_file = Path(args.compose_override_file)
    if not os.path.exists(args.compose_file):
        raise CommandError(f"File not found: {os.path.relpath(compose_override_file)}")

    yaml = YAML()

    # load source service from current docker-comose config
    if os.path.exists(compose_override_file):
        with open(compose_override_file) as f:
            compose_override_config = yaml.load(f)
    else:
        compose_override_config = yaml.load(StringIO("services:"))

    with open(compose_file) as f:
        comopse_config = yaml.load(f)

    # Create new service in compose_override_config using deep clone of source service
    source_service = comopse_config["services"][args.source_service]
    new_service = source_service.copy()

    # Get the context dir
    default_context_dir = os.path.dirname(args.compose_file)
    context_dir = source_service.get("build", {}).get("context", default_context_dir)

    # Update env vars in new service
    env = new_service.get("environment", [])
    if isinstance(env, dict):
        env_list: list[str] = [f"{k}={v}" for k, v in env.items()]
    else:
        env_list = env

    env_list.extend(
        [
            "COLORTERM",
            "ITERM_PROFILE",
            "ITERM_SESSION_ID",
            "LC_TERMINAL",
            "LC_TERMINAL_VERSION",
            "TERM",
            "TERM_PROGRAM",
            "TERM_PROGRAM_VERSION",
            "TERM_SESSION_ID",
        ]
    )
    new_service["environment"] = env_list

    source_image = source_service.get("image")
    if source_image is None:
        project_name = os.path.basename(os.path.abspath(context_dir))
        source_image = f"{project_name}-{args.source_service}"

    source_image_base = source_image.split(":")[0]
    new_service["image"] = f"{source_image_base}:nvim-devcontainer"

    build(source_image, new_service["image"], context_dir, no_cache=args.no_cache)

    new_service["stdin_open"] = True
    new_service["tty"] = True
    new_service["entrypoint"] = "nvim"

    ignore_keys = ["command", "build", "depends_on"]
    for key in ignore_keys:
        if key in new_service:
            del new_service[key]

    # Set up mountpoints
    new_service["volumes"] = new_service.get("volumes", [])
    new_service["volumes"].extend(
        [
            # The XDG dirs will be changed in the docker image to point to these
            f"{config_path('nvim')}:/nvim-devcontainer/config/nvim:ro",
            f"{config_path('git')}:/nvim-devcontainer/config/git:ro",
            f"{config_path('github-copilot')}:/nvim-devcontainer/config/github-copilot",
            "nvim-data:/nvim-devcontainer/data/nvim",
            "/tmp/.X11-unix:/tmp/.X11-unix:ro",
        ]
    )

    # Set up shared nvim-data volume
    compose_override_config["volumes"] = compose_override_config.get("volumes") or {}
    compose_override_config["volumes"] = {"nvim-data": {}}

    # Add the new service
    compose_override_config["services"] = compose_override_config.get("services") or {}
    compose_override_config["services"][args.name] = new_service

    with open(compose_override_file, "w") as f:
        yaml.dump(compose_override_config, f)

    print(
        f"Configured service '{args.name}' in {os.path.relpath(compose_override_file)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    build_parser = subparsers.add_parser("build", help="Build devcontainer image")
    build_parser.add_argument("base_image", type=str, help="Base image to use")
    build_parser.add_argument(
        "directory",
        type=str,
        help="Build context directory",
        nargs="?",
    )
    build_parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not use cache when building the image",
    )

    compose_parser = subparsers.add_parser(
        "compose",
        help="Create docker-compose override and build devcontainer from the given service",
    )
    compose_parser.add_argument(
        "-s",
        "--service",
        dest="source_service",
        type=str,
        help="The service to use as a base for the new service",
    )
    compose_parser.add_argument(
        "--compose-file",
        type=str,
        default="docker-compose.yml",
        help="Compose file path",
    )
    compose_parser.add_argument(
        "--compose-override-file",
        type=str,
        default="{compose_file_dir}/docker-compose.override.yml",
        help="Compose override file path",
    )
    compose_parser.add_argument(
        "--name", type=str, default="vim", help="Name for new service"
    )
    compose_parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not use cache when building the image",
    )

    args = parser.parse_args()

    try:
        if args.command == "build":
            tag_base = args.base_image.split(":")[0]
            tag = f"{tag_base}:nvim-devcontainer"

            if args.directory is None:
                with TemporaryDirectory() as tmpdir:
                    build(args.base_image, tag, tmpdir, no_cache=args.no_cache)
            else:
                build(args.base_image, tag, args.directory)

        elif args.command == "compose":
            args.compose_override_file = args.compose_override_file.format(
                compose_file_dir=Path(os.path.dirname(args.compose_file))
            )
            compose(args)
    except CommandError as e:
        sys.stderr.write(f"{e.message}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
