"""Judge calibration vs human labels; writes var/calibration.json; CI trust requires kappa>=0.7."""
import argparse
import json
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from packages.evals.judge import calibrate  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default="docs/human_labels.yaml")
    ap.add_argument("--provider", default=None)
    ap.add_argument("--out", default="var/calibration.json")
    args = ap.parse_args()
    root = pathlib.Path(__file__).resolve().parents[1]
    labels = yaml.safe_load((root / args.labels).read_text(encoding="utf-8"))["labels"]
    rep = calibrate(labels, provider_name=args.provider)
    (root / args.out).write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k != "pairs"}, ensure_ascii=False))
    sys.exit(0 if rep.get("trusted_for_ci") else 2)


if __name__ == "__main__":
    main()