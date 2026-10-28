import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--prefix', required=True)
parser.add_argument('--tag', default='latest')
parser.add_argument('--output', required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
data = (root / 'deploy/k8s/stack.yaml').read_text()
for role in ('gateway', 'metadata', 'worker', 'storage', 'ui'):
    data = data.replace('distributed-framework:' + role, args.prefix.rstrip('/') + '/' + role + ':' + args.tag)
Path(args.output).write_text(data)
