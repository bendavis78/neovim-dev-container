#!/usr/bin/env python3

import os
import random
import re
import string
import sys


def random_string(length):
    charset = string.ascii_lowercase + string.digits
    return "".join(random.choice(charset) for _ in range(length))


def usage():
    return f"Usage: {os.path.basename(sys.argv[0])} [DOCKERFILE_PATH]"


def main():
    if len(sys.argv) > 1:
        dockerfile_path = sys.argv[1]
    else:
        dockerfile_path = os.path.join(os.getcwd(), "Dockerfile")

    if not os.path.isfile(dockerfile_path):
        print(f"File not found: {dockerfile_path}\n\n{usage()}")
        sys.exit(1)

    dir_path = os.path.dirname(os.path.realpath(sys.argv[0]))
    template_path = os.path.join(dir_path, "Dockerfile.template")

    with open(dockerfile_path, "r") as f:
        dockerfile_contents = f.read()

    # Find the last occurance of FROM in source Dockerfile
    from_line = re.findall(r"^FROM.*", dockerfile_contents, re.MULTILINE)[-1]
    base_stage = re.search(r" AS (.*)", from_line)
    stage_prefix = f"devcontainer-{random_string(8)}"

    # Add the base final name if it doesn't exist
    if base_stage:
        base_stage = base_stage.group(1)
    else:
        base_stage = f"{stage_prefix}-base"
        new_from_line = re.sub(
            r"FROM (.+)$", rf"FROM \1 AS {base_stage}", from_line, flags=re.IGNORECASE,
        )
        dockerfile_contents = dockerfile_contents.replace(from_line, new_from_line)

    with open(template_path, "r") as f:
        template_contents = f.read()

    output = (
        template_contents.replace("%%__BASE_DOCKERFILE__%%", dockerfile_contents)
        .replace("%%__BASE_STAGE__%%", base_stage)
        .replace("%%__STAGE_PREFIX__%%", stage_prefix)
    )

    print(output)


if __name__ == "__main__":
    main()
