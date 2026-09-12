import json

from voiceops import reset_demo


if __name__ == "__main__":
    print(json.dumps(reset_demo(), indent=2))
