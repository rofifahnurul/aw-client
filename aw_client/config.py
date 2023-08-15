from aw_core.config import load_config_toml

default_config = """
[server]
hostname = "10.243.147.43"
port = "5600"

[client]
commit_interval = 10

[server-testing]
hostname = "10.243.147.43"
port = "5666"

[client-testing]
commit_interval = 5
""".strip()


def load_config():
    return load_config_toml("aw-client", default_config)
