import json


def load_config():
    with open("config/settings.json", "r") as f:
        return json.load(f)