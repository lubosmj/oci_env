import subprocess
import os
import pathlib

from oci_env.logger import logger
from oci_env.utils import exit_if_failed, exit_with_error
from oci_env.templates import profile_templates


def compose(args, client):
    logger.info(f'COMPOSE {args}')
    client.compose_command(args.command, interactive=True)


def exec(args, client):
    logger.info(f'EXEC {args}')
    exit_if_failed(client.exec(args.command, interactive=True, service=args.service))


def db(args, client):
    action = str(args.action[0])
    if action == 'reset':
        exit_if_failed(
            client.exec_container_script(
                f"database_reset.sh",
                args=None,
                interactive=True)
        )
        client.poll(10, 5)

    elif action == 'snapshot':
        exit_if_failed(
            client.exec_container_script(
                f"db_snapshot.sh",
                args=[args.filename, ],
                interactive=True)
        )

    elif action == 'restore':
        bash_args = [args.filename, ]
        if args.migrate:
            bash_args.append("1")
        exit_if_failed(
            client.exec_container_script(
                f"db_restore.sh",
                args=bash_args,
                interactive=True)
        )
        client.poll(10, 5)

    else:
        raise Exception(f'db {args.action} not implemented')


def shell(args, client):
    cmd = []
    if args.shell == "bash":
        cmd = ["bash"]
    elif args.shell == "python":
        cmd = ["pulpcore-manager", "shell_plus"]
    elif args.shell == "db":
        cmd = ["pulpcore-manager", "dbshell"]
    else:
        exit_with_error("Unsupported shell")

    client.exec(cmd, interactive=True, privileged=args.privileged)


def test(args, client):
    tr = TestRunner(args, client)
    tr.install_requirements()
    tr.run_tests()


class TestRunner:
    test_requirements_scripts = [
        "install_functional_requirements.sh",
        "install_performance_requirements.sh",
        "install_unit_requirements.sh",
        "install_lint_requirements.sh",
    ]

    test_run_scripts = [
        "run_functional_tests.sh",
        "run_performance_tests.sh",
        "run_unit_tests.sh",
        "run_lint_tests.sh",
    ]

    def __init__(self, args, client):
        self.args = args
        self.client = client
        self.projects = client.config["DEV_SOURCE_PATH"].split(":")

    def install_requirements(self):
        if self.args.install_deps:
            if self.args.test != "all":
                test_script = f"install_{self.args.test}_requirements.sh"
                if self.args.plugin:
                    self.install_test_requirements([self.args.plugin], [test_script])
                else:
                    self.install_test_requirements(self.projects, [test_script])
            else:
                if self.args.plugin:
                    self.install_test_requirements([self.args.plugin], self.test_requirements_scripts)
                else:
                    self.install_test_requirements(self.projects, self.test_requirements_scripts)

    def install_test_requirements(self, projects, test_requirements_scripts):
        for project in projects:
            for test_script in test_requirements_scripts:
                print(f"Running {test_script} for {project}...")
                exit_if_failed(
                    self.client.exec_container_script(
                        test_script,
                        args=[project],
                        privileged=self.args.privileged,
                    ).returncode
                )

    def run_tests(self):
        if self.args.plugin:
            if self.args.test != "all":
                self.run_test_command(self.args.plugin, [f"run_{self.args.test}_tests.sh"])
            else:
                self.run_test_command(self.args.plugin, self.test_run_scripts)

    def run_test_command(self, project, tests):
        for test_script in tests:
            exit_if_failed(
                self.client.exec_container_script(
                    test_script,
                    args=[project] + self.args.args,
                    interactive=True,
                    privileged=self.args.privileged,
                )
            )


def generate_client(args, client):
    api_root = client.get_dynaconf_variable("API_ROOT")

    base_cmd = [
        "bash",
        os.path.join(client.path, "base", "local_scripts", "generate_client.sh"),
    ]

    if args.plugin:
        plugins = [args.plugin,]
    else:
        plugins = client.config["DEV_SOURCE_PATH"].split(":")

    env = {**os.environ, **client.config, "PULP_API_ROOT": api_root}

    for plugin in plugins:
        cmd = base_cmd + [plugin.replace("-", "_"), args.language]
        if args.is_verbose:
            print(f"Running local command: {' '.join(cmd)}")

        exit_if_failed(subprocess.run(cmd, env=env, cwd=client.path).returncode)

        if args.install_client:
            exit_if_failed(client.exec_container_script("install_client.sh", args=[plugin.replace("-", "_")]).returncode)


def pulpcore_manager(args, client):
    client.exec(["pulpcore-manager"] + args.command, interactive=True)


def profile(args, client):
    src_dir = client.config["SRC_DIR"]

    if args.action == "init":
        if args.plugin:
            profiles_dir = os.path.join(src_dir, args.plugin, "profiles")
            profile_name = f"{args.plugin}/{args.profile_name}"
        else:
            profiles_dir = os.path.join(client.path, "profiles")
            profile_name = args.profile_name

        new_profile_dir = os.path.join(profiles_dir, args.profile_name)

        pathlib.Path(profiles_dir).mkdir(exist_ok=True)

        try:
            pathlib.Path(new_profile_dir).mkdir(exist_ok=False)
        except FileExistsError:
            print(f"A profile already exists at {new_profile_dir}")
            exit_with_error(1)

        for template in profile_templates:
            with open(os.path.join(new_profile_dir, template["file"]), "x") as f:
                f.write(template["template"].format(profile_name=profile_name))

        print(f"New profile \"{profile_name}\" successfully created at: {new_profile_dir}")
        print(f"To use it set \"COMPOSE_PROFILE={profile_name}\"")

    elif args.action == "ls":
        plugins = []
        for f in os.listdir(src_dir):
            if os.path.isdir(os.path.join(src_dir, f)):
                plugins.append(f)

        for p in plugins:
            profile_dir = os.path.join(src_dir, p, "profiles")
            if not  os.path.isdir(profile_dir):
                continue

            print(f"Plugin: {p}")
            for f in os.listdir(profile_dir):
                if os.path.isdir(os.path.join(profile_dir, f)):
                    if p == "oci_env":
                        print(f"  {f}")
                    else:
                        print(f"  {p}/{f}")

    elif args.action == "docs":
        if "/" in args.profile:
            plugin, profile = args.profile.split("/", maxsplit=1)
        else:
            plugin = "oci_env"
            profile = args.profile

        profile_path = os.path.join(src_dir, plugin, "profiles", profile)
        if not os.path.isdir(profile_path):
            exit_with_error(f"{args.profile} doesn't exist")

        try:
            with open(os.path.join(profile_path, "README.md"), "r")as f:
                print(f.read())
        except FileNotFoundError:
            exit_with_error(f"{args.profile} doesn't have a README.md")


def poll(args, client):
    client.poll(args.attempts, args.wait)


def pulp(args, client):
    exit(client.exec(["pulp"] + args.command, interactive=False).returncode)
