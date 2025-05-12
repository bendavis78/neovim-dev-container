#!/usr/bin/env python3
import argparse
import logging
import os
import subprocess
import sys
import tempfile
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import IO, Any, Iterable, Literal

from ruamel.yaml import YAML
from xdg import BaseDirectory as xdg  # type: ignore

# Set up logging to write to stderr
log = logging.getLogger(__name__)

class ColorFormatter(logging.Formatter):
    RED = "\033[91m"
    GREEN = "\033[92m"
    BLUE = "\033[94m"
    RESET = "\033[0m"
    ORANGE = "\033[33m"

    def format(self, record):
        msg = super().format(record)
        if record.levelno == logging.ERROR:
            return f"{self.RED}⊗{self.RESET} {msg}"
        elif record.levelno == logging.WARNING:
            return f"{self.ORANGE}⚠{self.RESET} {msg}"
        elif record.levelno == logging.DEBUG:
            return f"{self.BLUE}➤{self.RESET} {msg}"
        return f"{self.GREEN}ⓘ{self.RESET} {msg}"

handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(ColorFormatter())
log.setLevel(os.environ.get("LOG_LEVEL", logging.INFO))
log.addHandler(handler)

script_dir = os.path.dirname(os.path.realpath(__file__))


def config_path(path: str) -> str:
    config_home: Path = Path(xdg.xdg_config_home)
    return os.path.abspath(os.path.join(config_home, path))


def deep_merge(dict1: dict[str, Any], dict2: dict[str, Any]) -> dict[str, Any]:
    result = dict1.copy()
    for key, value in dict2.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result

def normalize_env_vars(env_vars):
    # if env vars are a dict, convert to list
    if isinstance(env_vars, dict):
        env_vars = [f"{k}={v}" for k, v in env_vars.items()]
    return env_vars


class CommandError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class Dockerfile:
    def __init__(
        self,
        base_image_name: str,
        template_path:  Path | None = None,
        overrides: Iterable[Path] | None = None,
        out_path: Path | Literal["-"] | None = None,
    ):
        self.base_image_name = base_image_name
        self.out_path = out_path
        self.file = None
        self.overrides = overrides
        if self.overrides is None:
            self.overrides = (
                Path(xdg.xdg_config_home) / "nvim-devcontainer" / "Dockerfile",
                Path.cwd() / ".nvim-devcontainer" / "Dockerfile",
            )

        self.template_path = template_path
        if self.template_path is None:
            self.template_path = os.path.join(script_dir, "Dockerfile.tpl")

    def __enter__(self) -> IO[Any]:
        self.file = self._write_dockerfile()
        return self.file

    def __exit__(self, *_) -> None:
        if self.file and self.file is not sys.stdout:
            self.file.close()

    def _write_dockerfile(self) -> IO[Any]:
        sys.stdout.flush()

        with open(self.template_path, "r") as f:
            template_contents = f.read()

        new_dockerfile_contents = template_contents.replace(
            "%%__BASE_IMAGE__%%", self.base_image_name
        )

        for override in self.overrides:
            if not os.path.exists(override):
                continue

            with open(override, "r") as f:
                override_contents = f.read()

            # Get full path string of override, replacing home with ~
            heading = f"## --| {override.absolute()} |--".ljust(80, "-")
            new_dockerfile_contents += f"\n\n{heading}\n{override_contents}"

        if self.out_path == "-":
            sys.stdout.write(new_dockerfile_contents)
            sys.stdout.flush()
            return sys.stdout
        elif self.out_path:
            file = open(self.out_path, "w")
            file.write(new_dockerfile_contents)
            file.flush()
            return file
        else:
            file = tempfile.NamedTemporaryFile(delete=False)
            file.write(new_dockerfile_contents.encode())
            file.flush()
            return file

    def write(self) -> IO[Any]:
        return self._write_dockerfile()


def build(
    base_image_name: str,
    tag: str,
    context_dir: Path | str,
    args: list | None = None,
    template_path: Path | None = None,
    overrides: list[Path] | None = None,
    dockerfile: Path | Literal["-"] | None = None,
) -> None:
    args = args or []

    with Dockerfile(
        base_image_name,
        template_path=template_path,
        out_path=dockerfile
    ) as f:
        tag = tag or f"{base_image_name}:nvim-devcontainer"
        cmd: list[str] = ["docker", "build"]
        cmd.extend(["-t", tag, "-f", f.name, str(context_dir)])
        cmd.extend(args)
        log.debug(" ".join(cmd))
        subprocess.run(cmd, check=True)

    log.info('Built image "%s"', tag)


def compose(args: argparse.Namespace) -> None:
    compose_file = Path(args.compose_file)
    compose_override_file = Path(args.compose_override_file)
    if not os.path.exists(args.compose_file):
        raise CommandError(f"File not found: {os.path.relpath(compose_override_file)}")

    yaml = YAML()

    # load source service from current docker-comose config
    if os.path.exists(compose_override_file):
        with open(compose_override_file) as f:
            compose_override_config = yaml.load(f) or {}
    else:
        compose_override_config = yaml.load(StringIO("services:"))

    with open(compose_file) as f:
        compose_config = yaml.load(f) or {}

    # Create new service in compose_override_config using deep clone of source service
    source_service = compose_config["services"][args.source_service]
    new_service = source_service.copy()

    # Normalize env vars to list
    new_service["environment"] = normalize_env_vars(new_service.get("environment", []))

    # If new service name already exists in compose_override_config, then we merge any keys from
    # there into the new service
    if existing_service := compose_override_config["services"].get(args.name):
        if not args.replace_existing:
            new_service = deep_merge(existing_service, new_service)

    # Get the context dir
    build_config = source_service.get("build", {})
    context_dir = build_config.get("context")

    # Require context_dir
    if not context_dir:
        raise CommandError(
            f"Could not determine context dir from service `{args.source_service}`"
        )

    breakpoint()
    # Update env vars in new service
    env_list = normalize_env_vars(new_service.get("environment", []))
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

    if args.no_build and args.dockerfile:
        log.debug(f"Setting build settings to use {args.dockerfile}")
        new_service["build"] = source_service.get("build", {})
        # get context_dir relative to compose file
        new_service["build"]["context"] = context_dir
        new_service["build"]["dockerfile"] = args.dockerfile
        new_service["build"]["target"] = "nvim-devcontainer"


    else:
        build_args = args.build_args and args.build_args[1:] or []
        build(
            source_image,
            new_service["image"],
            context_dir,
            args=build_args,
            dockerfile=args.dockerfile,
        )


    dockerfile = args.dockerfile
    if dockerfile and dockerfile != "-":
        dockerfile = Path(args.dockerfile)

    outfile = Dockerfile(source_image, out_path=dockerfile).write()

    log.info("Dockerfile written to %s", outfile.name)

    with open(compose_override_file, "w") as f:
        yaml.dump(compose_override_config, f)

    log.info(
        f"Configured service '%s' in %s",
        args.name,
        os.path.relpath(compose_override_file),
    )


class ArgumentParser(argparse.ArgumentParser):
    def convert_arg_line_to_args(self, arg_line: str) -> list[str]:
        return arg_line.split()


def main() -> None:
    parser = ArgumentParser(fromfile_prefix_chars="@")
    subparsers = parser.add_subparsers(dest="command")

    build_parser = subparsers.add_parser("build", help="Build devcontainer image")
    build_parser.add_argument("base_image", type=str, help="Base image to use")
    build_parser.add_argument(
        "directory",
        type=str,
        help="Build context directory",
        nargs="?",
    )
    build_parser.add_argument("build_args", nargs=argparse.REMAINDER, help="Args to pass to docker build")
    build_parser.add_argument(
        "--dockerfile",
        dest="dockerfile",
        type=str, 
        help="Output path for the generated Dockerfile (uses temp file by default)",
    )
    build_parser.add_argument(
        "--context-dir",
        type=str,
        help="Context directory for the build (default: use context from source service)",
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
        "--dockerfile",
        dest="dockerfile",
        type=str, 
        help="Output path for generated Dockerfile (uses temp file by default)",
    )
    compose_parser.add_argument(
        "--no-build",
        action="store_true",
        help="Output path for generated Dockerfile (uses temp file by default)",
    )
    compose_parser.add_argument(
        "--replace-existing",
        action="store_true",
        help=(
            "Replace existing service in compose override file. By default the existing serrvice "
            "will be merged with the updated one."
        ),
    )
    compose_parser.add_argument("build_args", nargs=argparse.REMAINDER, help="Args to pass to docker build")

    args = parser.parse_args()

    try:
        if args.command == "build":
            tag_base = args.base_image.split(":")[0]
            tag = f"{tag_base}:nvim-devcontainer"
            build_args = args.build_args and args.build_args[1:] or []

            if args.directory is None:
                with TemporaryDirectory() as tmpdir:
                    build(args.base_image, tag, tmpdir, args=build_args)
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
